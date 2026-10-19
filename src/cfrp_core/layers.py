"""Layer assembly: lead-in anchor, per-layer speed planning, seam-trimmed
ramps between layers (spec §8–§9), and the flat-laminate and revolve-shell
layer loops (spec §7–§8).

A trajectory is a list of point dicts:
    x, y, z (mm), dist (mm from the previous point), v (mm/s),
    e_mult (extrusion factor), layer, layer_start, is_transition,
    pump_floored, z_layer, cure_dwell, is_bridge, is_anchor (not every point
    has every key).
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import shapely
from shapely.geometry import LineString, Point
from shapely.ops import nearest_points, unary_union

from . import geometry as geo
from .config import ARCHIMEDEAN, CORNER_STEP, DIAGONAL_45, FERMAT, ORIENTED_SERPENTINE, ProcessConfig
from .geometry._common import enforce_min_radius
from .geometry.corner_step import _is_rectangle
from .geometry.laminate import _ring_from, _wall_seam, max_track_count
from .geometry.mesh_slicer import islands as mesh_islands
from .geometry.mesh_slicer import load_stl, slice_mesh_regions
from .kinematics import calculate_trajectory


@dataclass(frozen=True)
class KinematicsParams:
    v_max: float
    v_min: float
    accel: float
    decel: float
    p_advance: float
    pump_floor: float
    min_rad: float
    advance_table: tuple = ()

    @classmethod
    def from_config(cls, config: ProcessConfig) -> KinematicsParams:
        k = config.kinematics
        v_min = float(k.v_min_mm_s)
        return cls(
            v_max=float(k.v_max_mm_s),
            v_min=v_min,
            accel=float(k.accel_mm_s2),
            decel=float(k.decel_mm_s2),
            p_advance=float(k.pressure_advance_mm),
            pump_floor=float(k.pump_stall_floor_mm_s),
            min_rad=float(config.min_radius_mm),
            advance_table=tuple(k.pressure_advance_table),
        )


# --- Seam trimming -----------------------------------------------------------

def trim_trajectory_tail(trajectory, trim_length):
    """Remove up to `trim_length` mm of travel from the end of `trajectory`
    (mutates it in place), never crossing into an earlier layer or a prior
    ramp, and never more than 40% of the last layer's run. Returns the exact
    point at the new end (interpolated if the cut falls mid-segment), which
    is also the trajectory's last point: the vertex past the cut is moved
    onto it, keeping its planned speed and flow. (Dropping that vertex left
    the tail ending a whole segment early, e.g. 113 mm on a long edge, and
    that stretch was then driven as part of the ramp.)"""
    if trim_length <= 1e-9 or len(trajectory) < 2:
        last = trajectory[-1]
        return {'x': last['x'], 'y': last['y'], 'z': last['z']}

    target_layer = trajectory[-1].get('layer')
    run_start = len(trajectory) - 1
    while run_start > 0:
        pt = trajectory[run_start - 1]
        if pt.get('layer') != target_layer or pt.get('is_transition'):
            break
        run_start -= 1

    if run_start >= len(trajectory) - 1:
        last = trajectory[-1]
        return {'x': last['x'], 'y': last['y'], 'z': last['z']}

    run = trajectory[run_start:]
    total_length = sum(
        math.hypot(b['x'] - a['x'], b['y'] - a['y']) for a, b in zip(run[:-1], run[1:], strict=False)
    )
    trim_length = min(trim_length, total_length * 0.4)

    traveled = 0.0
    for idx in range(len(trajectory) - 1, run_start, -1):
        a, b = trajectory[idx - 1], trajectory[idx]
        seg = math.hypot(b['x'] - a['x'], b['y'] - a['y'])
        if traveled + seg >= trim_length:
            frac = (trim_length - traveled) / seg if seg > 1e-9 else 0.0
            cut = {
                'x': b['x'] - (b['x'] - a['x']) * frac,
                'y': b['y'] - (b['y'] - a['y']) * frac,
                'z': b['z'],
            }
            del trajectory[idx + 1:]
            trajectory[idx] = dict(b, x=cut['x'], y=cut['y'],
                                   dist=math.hypot(cut['x'] - a['x'], cut['y'] - a['y']))
            return cut
        traveled += seg

    last = trajectory[-1]
    return {'x': last['x'], 'y': last['y'], 'z': last['z']}


def trim_path_head(points, trim_length):
    """Remove up to `trim_length` mm (≤ 40% of the layer) of travel from the
    start of a planned layer. Returns (cut_point, remaining_points); the
    remaining points keep their planning, except the first point's `dist`,
    which is recomputed from the cut."""
    if trim_length <= 1e-9 or len(points) < 2:
        first = points[0]
        return {'x': first['x'], 'y': first['y'], 'z': first['z']}, points

    total_length = sum(
        math.hypot(b['x'] - a['x'], b['y'] - a['y']) for a, b in zip(points[:-1], points[1:], strict=False)
    )
    trim_length = min(trim_length, total_length * 0.4)

    traveled = 0.0
    for idx in range(1, len(points)):
        a, b = points[idx - 1], points[idx]
        seg = math.hypot(b['x'] - a['x'], b['y'] - a['y'])
        if traveled + seg >= trim_length:
            frac = (trim_length - traveled) / seg if seg > 1e-9 else 0.0
            cut = {
                'x': a['x'] + (b['x'] - a['x']) * frac,
                'y': a['y'] + (b['y'] - a['y']) * frac,
                'z': b['z'],
            }
            remaining = list(points[idx:])
            remaining[0] = dict(remaining[0])
            remaining[0]['dist'] = math.hypot(remaining[0]['x'] - cut['x'], remaining[0]['y'] - cut['y'])
            return cut, remaining
        traveled += seg

    first = points[0]
    return {'x': first['x'], 'y': first['y'], 'z': first['z']}, points


# --- Per-layer building blocks -----------------------------------------------

def add_lead_in_anchor(raw_path, anchor_length, step=0.75):
    """Prepend a straight tangent lead-in of `anchor_length` mm pointing back
    along the first segment, subdivided into ~step-mm segments so the G-code
    has uniform resolution from the start."""
    if anchor_length <= 0 or len(raw_path) < 2:
        return raw_path
    dx = raw_path[1][0] - raw_path[0][0]
    dy = raw_path[1][1] - raw_path[0][1]
    angle_rad = math.atan2(dy, dx)
    cos_a, sin_a = math.cos(angle_rad), math.sin(angle_rad)
    n = max(2, int(math.ceil(anchor_length / step)))
    anchor_pts = []
    for k in range(n, 0, -1):
        frac = k / n
        ax = raw_path[0][0] - cos_a * anchor_length * frac
        ay = raw_path[0][1] - sin_a * anchor_length * frac
        anchor_pts.append((ax, ay))
    return anchor_pts + list(raw_path)


def add_arc_lead_in_anchor(raw_path, anchor_length, cx, cy, step=0.75):
    """Prepend an arc lead-in that follows the outermost ring's curvature
    backwards from the spiral start. For revolve shells this keeps the
    anchor on the shell surface instead of shooting off on a tangent."""
    if anchor_length <= 0 or len(raw_path) < 2:
        return raw_path
    x0, y0 = raw_path[0]
    r = math.hypot(x0 - cx, y0 - cy)
    if r < 1e-6:
        return add_lead_in_anchor(raw_path, anchor_length, step)
    a0 = math.atan2(y0 - cy, x0 - cx)
    sweep = anchor_length / r
    # Determine direction: the spiral moves CW or CCW from the start.
    # The anchor goes in the opposite direction.
    a1 = math.atan2(raw_path[1][1] - cy, raw_path[1][0] - cx)
    da = a1 - a0
    if da > math.pi:
        da -= 2 * math.pi
    elif da < -math.pi:
        da += 2 * math.pi
    # Anchor goes opposite to spiral direction
    if da >= 0:
        sweep = -sweep  # spiral goes CCW, anchor goes CW
    n = max(2, int(math.ceil(anchor_length / step)))
    anchor_pts = []
    for k in range(n, 0, -1):
        frac = k / n
        angle = a0 + sweep * frac
        ax = cx + r * math.cos(angle)
        ay = cy + r * math.sin(angle)
        anchor_pts.append((ax, ay))
    return anchor_pts + list(raw_path)


def _mark_anchor(points, n):
    """Flag the first n planned points as the lead-in anchor (`is_anchor`;
    the viewer colours them, nothing else reads it)."""
    for pt in points[:n]:
        pt['is_anchor'] = True


def plan_layer(raw_path, layer_z, layer_num, kin: KinematicsParams):
    """Speed-plan one layer's 2D path and tag every point with its layer."""
    layer_trajectory = calculate_trajectory(
        tuple(raw_path), kin.v_max, kin.v_min, kin.accel, kin.decel,
        kin.p_advance, z_height=layer_z, min_rad=kin.min_rad, pump_floor=kin.pump_floor,
        advance_table=kin.advance_table,
    )
    for pt in layer_trajectory:
        pt['layer'] = layer_num
        pt['z_layer'] = layer_z
    return layer_trajectory


def _unit_dir(a, b):
    """Unit XY direction a -> b, or None if they coincide."""
    dx, dy = b['x'] - a['x'], b['y'] - a['y']
    n = math.hypot(dx, dy)
    return (dx / n, dy / n) if n > 1e-9 else None


def _end_direction(points, span=1.0):
    """Unit XY direction over the last ~span mm of a planned layer."""
    ex, ey = points[-1]['x'], points[-1]['y']
    for pt in reversed(points[:-1]):
        dx, dy = ex - pt['x'], ey - pt['y']
        n = math.hypot(dx, dy)
        if n >= span:
            return (dx / n, dy / n)
    return None


def _ramp_min_run(layer_height, transition_angle_deg):
    """Horizontal run a layer ramp needs so no Hermite step climbs steeper
    than the transition angle (local XY speed dips to ~half the chord)."""
    angle = min(max(float(transition_angle_deg), 1.0), 89.0)
    return 2.0 * float(layer_height) / math.tan(math.radians(angle))


def _extend_head_for_run(prev_cut, first_cut, layer_trajectory, min_run, max_len):
    """Move the head cut further along the layer until the ramp's horizontal
    run from prev_cut is at least min_run (never past max_len of travel).
    The new cut may fall inside a long segment. Returns (first_cut,
    remaining points)."""
    px, py = prev_cut['x'], prev_cut['y']
    if min_run <= 0 or math.hypot(first_cut['x'] - px, first_cut['y'] - py) >= min_run:
        return first_cut, layer_trajectory
    travelled, last = 0.0, first_cut
    for k, pt in enumerate(layer_trajectory[:-1]):
        seg = math.hypot(pt['x'] - last['x'], pt['y'] - last['y'])
        # first fraction t of this segment whose point is >= min_run away
        steps = max(1, int(math.ceil(seg / 0.1)))
        for i in range(1, steps + 1):
            t = i / steps
            if travelled + seg * t > max_len:
                return first_cut, layer_trajectory
            x = last['x'] + (pt['x'] - last['x']) * t
            y = last['y'] + (pt['y'] - last['y']) * t
            if math.hypot(x - px, y - py) >= min_run:
                cut = {'x': x, 'y': y, 'z': pt['z']}
                remaining = list(layer_trajectory[k + (1 if t >= 1.0 else 0):])
                remaining[0] = dict(remaining[0])
                remaining[0]['dist'] = math.hypot(remaining[0]['x'] - x, remaining[0]['y'] - y)
                return cut, remaining
        travelled += seg
        last = pt
    return first_cut, layer_trajectory


def append_layer(trajectory, layer_trajectory, layer_num, seam_trim, ramp_speed, min_run=0.0):
    """Append one planned layer to `trajectory` (in place), bridged from the
    previous layer by a RAMPED Z-SEAM TRANSITION: trim `seam_trim` mm off the
    previous tail and this head, then fill the gap with a sloped move in
        steps = max(4 if Δz > 0 else 1, ⌈L/0.75⌉, ⌈|Δz|/0.05⌉)
    points at `ramp_speed`, e_mult 1. The last ramp point is `layer_start`.

    `min_run`: if the two cuts end up closer than this in XY (e.g. the layer
    starts where the last one ended but runs the other way), the head cut
    moves further into the layer (by up to two more seam-trims, within 40%
    of it) so the ramp is not steeper than the configured transition angle.

    Fibre can only be laid forward/up: callers must append layers strictly
    bottom to top.
    """
    if trajectory and layer_trajectory:
        prev_cut = trim_trajectory_tail(trajectory, seam_trim)
        layer_len = sum(float(p.get('dist', 0.0)) for p in layer_trajectory[1:])
        first_cut, layer_trajectory = trim_path_head(layer_trajectory, seam_trim)
        # Up to two more seam-trims: enough for short or reversed seams; a
        # ramp that needs more (e.g. thick layers at a shallow angle) would
        # shortcut across the layer, so it is left steep and reported.
        first_cut, layer_trajectory = _extend_head_for_run(
            prev_cut, first_cut, layer_trajectory, min_run,
            min(0.4 * layer_len - seam_trim, 2.0 * max(seam_trim, 1.0)))

        dx = first_cut['x'] - prev_cut['x']
        dy = first_cut['y'] - prev_cut['y']
        dzs = first_cut['z'] - prev_cut['z']
        seg_length = math.sqrt(dx ** 2 + dy ** 2 + dzs ** 2)
        steps = max(
            4 if abs(dzs) > 1e-9 else 1,
            int(math.ceil(seg_length / 0.75)),
            int(math.ceil(abs(dzs) / 0.05)),
        )
        # Cubic Hermite in XY, tangent-continuous with the trimmed tail and
        # the layer head (a straight chord leaves a kink at both joins that
        # can be tighter than min bend radius). Z stays linear.
        chord_xy = math.hypot(dx, dy)
        t0 = _unit_dir(trajectory[-2], trajectory[-1]) if len(trajectory) >= 2 else None
        t1 = _unit_dir(layer_trajectory[0], layer_trajectory[1]) if len(layer_trajectory) >= 2 else None
        # Tangent-continuous only when the seam turns <= 90 deg: for a
        # reversal (layer runs back the way the last one came) the Hermite
        # would hairpin within the chord and climb near-vertically there.
        reversal = t0 is not None and t1 is not None and t0[0] * t1[0] + t0[1] * t1[1] < 0.0
        if t0 is None or t1 is None or chord_xy < 1e-9 or reversal:
            t0 = t1 = None
        def ramp_xy(hermite):
            pts = []
            for step in range(1, steps + 1):
                s = step / steps
                if not hermite:
                    pts.append((prev_cut['x'] + dx * s, prev_cut['y'] + dy * s))
                    continue
                h00, h10 = 2 * s ** 3 - 3 * s ** 2 + 1, s ** 3 - 2 * s ** 2 + s
                h01, h11 = -2 * s ** 3 + 3 * s ** 2, s ** 3 - s ** 2
                m = chord_xy
                pts.append((h00 * prev_cut['x'] + h10 * m * t0[0] + h01 * first_cut['x'] + h11 * m * t1[0],
                            h00 * prev_cut['y'] + h10 * m * t0[1] + h01 * first_cut['y'] + h11 * m * t1[1]))
            return pts

        xy = ramp_xy(t0 is not None)
        if t0 is not None and min_run > 0:
            # A short Hermite between tangents that disagree with the chord
            # can slow to a crawl in XY and climb steeply there; a straight
            # ramp's slope is uniform and min_run keeps it within the angle.
            tan_limit = 2.0 * abs(dzs) / min_run
            lo = (prev_cut['x'], prev_cut['y'])
            for hx, hy in xy:
                if abs(dzs) / steps > tan_limit * math.hypot(hx - lo[0], hy - lo[1]):
                    xy = ramp_xy(False)
                    break
                lo = (hx, hy)
        for step, (px, py) in enumerate(xy, start=1):
            point = {'x': px, 'y': py, 'z': prev_cut['z'] + dzs * step / steps}
            prior = trajectory[-1]
            transition_dist = math.sqrt(
                (point['x'] - prior['x']) ** 2
                + (point['y'] - prior['y']) ** 2
                + (point['z'] - prior['z']) ** 2
            )
            trajectory.append({
                "x": point['x'], "y": point['y'], "z": point['z'],
                "dist": float(transition_dist), "v": ramp_speed,
                "e_mult": 1.0, "is_transition": True,
                "layer_start": step == steps, "layer": layer_num,
            })

    trajectory.extend(layer_trajectory)


# --- Layer loops -------------------------------------------------------------

def _skip_behind(prev_path, next_path, cx, cy, max_frac=0.3):
    """Drop the head of next_path while it still lies angularly BEHIND the
    end of prev_path (in the spiral's sense of rotation), so the connector is
    a short forward step instead of a ~360 deg lap. Never drops more than
    max_frac of the path."""
    tail = np.array(prev_path[-6:], dtype=np.float64) - (cx, cy)
    ang = np.unwrap(np.arctan2(tail[:, 1], tail[:, 0]))
    sense = 1.0 if ang[-1] - ang[0] >= 0 else -1.0
    a0 = ang[-1]
    nxt = np.asarray(next_path, dtype=np.float64) - (cx, cy)
    fwd = ((np.arctan2(nxt[:, 1], nxt[:, 0]) - a0) * sense) % (2.0 * math.pi)
    limit = max(1, int(len(next_path) * max_frac))
    for k in range(limit):
        if fwd[k] < math.pi:
            return list(next_path[k:])
    return list(next_path)


def _polar_profile(points, cx, cy, from_end):
    """r(theta) of the last (from_end) or first lap of `points` about
    (cx, cy), as a periodic interpolant. The lap is the run of points
    covering one full turn, so it lies on a single ring."""
    pts = np.asarray(points, dtype=np.float64) - (cx, cy)
    if from_end:
        pts = pts[::-1]
    ang = np.unwrap(np.arctan2(pts[:, 1], pts[:, 0]))
    swept = np.abs(ang - ang[0])
    n = int(np.searchsorted(swept, 2.0 * math.pi)) + 1
    lap, a = pts[:max(n, 3)], ang[:max(n, 3)]
    r = np.hypot(lap[:, 0], lap[:, 1])
    a = np.mod(a, 2.0 * math.pi)
    order = np.argsort(a)
    a, r = a[order], r[order]

    def radius(theta):
        return np.interp(np.mod(theta, 2.0 * math.pi), a, r, period=2.0 * math.pi)
    return radius


def _helical_connector(prev_path, next_start, cx, cy, step=0.75, next_path=None):
    """Seam between two revolve layers as ONE forward lap around the shell
    centre (vase-mode / spiralize style): keeps the spiral's sense of
    rotation, so the fibre never reverses, and moves radius from the old
    ring end to the new ring start with a smoothstep (zero radial slope at
    both ends). Z is ramped across the whole lap by the caller.

    Round shells lap on a circle. With `next_path` (non-round shells, e.g.
    the square tube) the lap instead follows the rings' own outline: at
    each angle the radius blends the previous ring's last lap into the next
    ring's first lap, so it stays between the rings instead of cutting the
    corners.

    Returns XY points strictly between prev_path[-1] and next_start."""
    ex, ey = prev_path[-1]
    sx, sy = next_start
    tail = np.array(prev_path[-6:], dtype=np.float64) - (cx, cy)
    ang = np.unwrap(np.arctan2(tail[:, 1], tail[:, 0]))
    sense = 1.0 if ang[-1] - ang[0] >= 0 else -1.0
    r0, r1 = math.hypot(ex - cx, ey - cy), math.hypot(sx - cx, sy - cy)
    a0, a1 = math.atan2(ey - cy, ex - cx), math.atan2(sy - cy, sx - cx)
    sweep = ((a1 - a0) * sense) % (2.0 * math.pi)
    r_mid = 0.5 * (r0 + r1)
    # Z already ramps over the whole ring + connector, so the connector only
    # needs to be long enough for a gentle radius change (slope <= ~0.25);
    # adding a full extra lap would retrace the ring (double fibre, and a
    # self-crossing warning on single-track shells).
    if sweep * r_mid < 4.0 * abs(r1 - r0):
        sweep += 2.0 * math.pi
    n = max(8, int(math.ceil(sweep * r_mid / step)))
    u = np.linspace(0.0, 1.0, n + 1)[1:-1]
    blend = 3.0 * u ** 2 - 2.0 * u ** 3
    theta = a0 + sense * sweep * u
    if next_path is None:
        radius = r0 + (r1 - r0) * blend
    else:
        r_prev = _polar_profile(prev_path, cx, cy, from_end=True)(theta)
        r_next = _polar_profile(next_path, cx, cy, from_end=False)(theta)
        radius = r_prev + (r_next - r_prev) * blend
    return list(zip((cx + radius * np.cos(theta)).tolist(),
                    (cy + radius * np.sin(theta)).tolist(), strict=False))


def _spiral_z(xy, section_starts, z_nodes):
    """Z per point. Section k spans point indices section_starts[k] up to
    section_starts[k+1] (the last one runs to the final point); inside it Z
    rises linearly with cumulative XY length from z_nodes[k] to
    z_nodes[k+1]. len(z_nodes) == len(section_starts) + 1."""
    pts = np.asarray(xy, dtype=np.float64)
    cum = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))])
    z = np.empty(len(pts))
    bounds = list(section_starts) + [len(pts) - 1]
    for k in range(len(section_starts)):
        a, b = bounds[k], bounds[k + 1]
        hi = b + 1 if k == len(section_starts) - 1 else b
        span = cum[b] - cum[a]
        frac = (cum[a:hi] - cum[a]) / span if span > 1e-9 else np.zeros(hi - a)
        z[a:hi] = z_nodes[k] + (z_nodes[k + 1] - z_nodes[k]) * frac
    return z.tolist()

@lru_cache(maxsize=2048)
def _archimedean_cached(cross_section_wkb, gap, min_rad, start_from_inside, polar_center=None):
    """Per-layer spiral for a revolve cross-section. Pure in its arguments, so
    changing speed / anchor / seam parameters re-slices without redoing the
    (dominant) polygon offsetting. Returns a tuple so the entry is immutable."""
    from shapely import wkb
    path, _ = geo.archimedean_path(
        wkb.loads(cross_section_wkb), gap, min_rad, track_count=None,
        start_from_inside=start_from_inside, polar_center=polar_center)
    return tuple(path)


def build_revolve_shell(config: ProcessConfig):
    """Z-revolve shell (sphere/dome/cylinder/tube/taper/cone) with spiral
    Z-ramping and arc seam transitions.

        num_layers = max(2, round(H / layer_height) + 1),  layer_height ≥ 0.05
        Layer i: Z ramps from z0 + H·i/(n-1) to z0 + H·(i+1)/(n-1)
        Last layer stays flat at z0 + H.

    Layer-to-layer transitions follow the shell circumference (arc interpolation
    around the shell center) instead of a straight chord.

    Returns (trajectory, target_shapes, max_z)."""
    g = config.geometry
    shape = g.shape
    cx, cy = float(g.start_x_mm), float(g.start_y_mm)
    z0 = float(config.z_offset_mm)
    outer_radius = max(float(g.base_radius_mm), 1.0)
    gap = float(config.track_gap_mm)
    min_rad = float(config.min_radius_mm)
    tracks = max(1, int(config.tracks_per_layer))
    shape_type = 'square' if shape == 'Hollow Square Tube (Z)' else 'round'
    profile = geo.revolve_radius_profile(shape, g.base_radius_mm, tracks, gap)
    kin = KinematicsParams.from_config(config)
    use_fermat = config.infill_type == FERMAT

    if shape == 'Hollow Sphere (Z)':
        height = 2.0 * outer_radius
    else:
        height = max(float(g.stem_height_mm), 1e-6)

    layer_height = max(float(config.layer_height_mm), 0.05)
    num_layers = max(2, round(height / layer_height) + 1)

    layers_raw = []
    target_shapes = []
    max_z = z0
    anchored = False
    anchor_n = 0
    fermat_last_pt = None

    for i in range(num_layers):
        progress = i / (num_layers - 1)
        layer_z = z0 + height * progress
        max_z = layer_z
        current_outer, current_inner = profile(progress)

        wall_depth = (tracks - 0.5) * gap

        # Skip layers where the cross-section is too small for the spiral
        # path to maintain min_radius curvature at every ring, including
        # the innermost ring and its transitions.
        skip_threshold = min_rad + gap if shape_type == 'square' else min_rad + (tracks + 0.5) * gap
        if current_outer < skip_threshold:
            cross_section = geo.revolve_cross_section(
                cx, cy, current_outer, current_inner, shape_type,
                min_bend_radius=min_rad, wall_depth=wall_depth,
            )
            target_shapes.append((layer_z, cross_section, i + 1))
            continue

        cross_section = geo.revolve_cross_section(
            cx, cy, current_outer, current_inner, shape_type,
            min_bend_radius=min_rad, wall_depth=wall_depth,
        )
        target_shapes.append((layer_z, cross_section, i + 1))
        if cross_section.is_empty or cross_section.area < 1e-6:
            continue

        is_outward = (i % 2 != 0)
        try:
            if use_fermat:
                fermat_hint = None
                if fermat_last_pt is not None:
                    ccx = cross_section.centroid.x
                    ccy = cross_section.centroid.y
                    fermat_hint = (2.0 * ccx - fermat_last_pt[0],
                                   2.0 * ccy - fermat_last_pt[1])
                raw_path = geo.fermat_spiral_path(
                    cross_section, gap, min_rad, track_count=None,
                    start_from_inside=is_outward, entry_hint=fermat_hint
                )
            else:
                raw_path = list(_archimedean_cached(
                    cross_section.wkb, gap, min_rad, is_outward,
                    (cx, cy) if shape_type == 'square' else None))
        except ValueError:
            continue
        if not raw_path:
            continue

        if not anchored:
            n_before = len(raw_path)
            if shape_type == 'round':
                raw_path = add_arc_lead_in_anchor(raw_path, float(config.anchor_length_mm), cx, cy)
            else:
                raw_path = add_lead_in_anchor(raw_path, float(config.anchor_length_mm))
            anchor_n = len(raw_path) - n_before
            anchored = True

        layers_raw.append((i + 1, layer_z, enforce_min_radius(raw_path, min_rad)))
        if use_fermat:
            fermat_last_pt = layers_raw[-1][2][-1]

    if not layers_raw:
        raise ValueError(
            "No layer of this shell could be filled: every cross-section is too small for "
            "the wall at this Track Gap and Min Bend Radius. Enlarge the part or reduce "
            "Tracks Per Layer, Track Gap or Min Bend Radius."
        )

    # One continuous stroke for the whole shell (vase-mode style): every ring
    # is followed by a forward helical lap into the next ring, speed is
    # planned once over the whole path (no stop at seams), and Z climbs
    # steadily over each ring + its connector lap, so there is no seam block.
    # A layer that is just one closed ring (single-track walls) IS its own
    # helical lap: drop the ring and let the connector carry radius and Z, so
    # each layer costs one lap instead of ring + retraced lap. The first
    # layer (anchor) and last layer (top) stay whole.
    # Optional staggered seams (round shells only: a rotated ring is the same
    # ring, so only the seam moves; the helical connector laps the extra arc).
    stagger = math.radians(float(config.seam_stagger_deg)) if shape_type == 'round' else 0.0
    if stagger:
        for k in range(1, len(layers_raw)):
            num, lz, path = layers_raw[k]
            c, s = math.cos(k * stagger), math.sin(k * stagger)
            layers_raw[k] = (num, lz, [(cx + (x - cx) * c - (y - cy) * s,
                                        cy + (x - cx) * s + (y - cy) * c) for x, y in path])

    if shape_type == 'round':
        for k in range(1, len(layers_raw) - 1):
            num, lz, path = layers_raw[k]
            rr = np.hypot(*(np.asarray(path, dtype=np.float64) - (cx, cy)).T)
            if rr.max() - rr.min() < 0.1 * gap:
                layers_raw[k] = (num, lz, path[:2])

    xy, starts, owner = [], [], []
    for k, (layer_num, _, path) in enumerate(layers_raw):
        starts.append(len(xy))
        xy.extend(path)
        owner.extend([layer_num] * len(path))
        if k + 1 < len(layers_raw):
            nxt = layers_raw[k + 1]
            trimmed = _skip_behind(path, nxt[2], cx, cy)
            layers_raw[k + 1] = (nxt[0], nxt[1], trimmed)
            link = _helical_connector(path, trimmed[0], cx, cy,
                                      next_path=trimmed if shape_type == 'square' else None)
            xy.extend(link)
            owner.extend([layer_num] * len(link))
    xy = enforce_min_radius(xy, min_rad)   # also rounds ring/connector joins
    z_nodes = [lz for _, lz, _ in layers_raw] + [layers_raw[-1][1]]
    zs = _spiral_z(xy, starts, z_nodes)

    layer_z_of = {n: lz for n, lz, _ in layers_raw}
    trajectory = plan_layer(xy, z0, owner[0], kin)
    for idx, pt in enumerate(trajectory):
        pt['z'] = zs[idx]
        pt['layer'] = owner[idx]
        pt['z_layer'] = layer_z_of[owner[idx]]
        pt['layer_start'] = False
    for s in starts:
        trajectory[s]['layer_start'] = True
    _mark_anchor(trajectory, anchor_n)
    return trajectory, target_shapes, max_z

def build_flat_laminate(config: ProcessConfig):
    """Flat multi-layer laminate:  z_i = z_offset + i·layer_height,
    fibre angle_i = layup[i mod len(layup)].

    Returns (trajectory, target_shapes, max_z). A ValueError from a layer's
    geometry is re-raised naming the layer."""
    g = config.geometry
    trajectory = []
    target_shapes = []
    max_z = config.z_offset_mm
    last_pt = last_dir = None
    layup_sequence = list(config.layup_deg) or [0.0]
    kin = KinematicsParams.from_config(config)
    seam_trim = max(0.0, float(config.seam_trim_mm))
    gap, r_min = config.track_gap_mm, config.min_radius_mm
    layer_height = max(float(config.layer_height_mm), 0.05)
    min_run = _ramp_min_run(layer_height, config.transition_angle_deg)

    tracks = config.tracks_per_layer
    if config.fill_solid:
        tracks = _fill_track_count(geo.base_polygon(g, gap, r_min), config, config.infill_type)
    first_infill = config.first_layer_infill or config.infill_type
    first_tracks = (_fill_track_count(geo.base_polygon(g, gap, r_min), config, first_infill)
                    if config.first_layer_solid else tracks)

    for i in range(config.layers):
        layer_z = config.z_offset_mm + (i * layer_height)
        current_angle = layup_sequence[i % len(layup_sequence)]
        max_z = layer_z

        try:
            base_poly = geo.base_polygon(g, gap, r_min)

            printable = geo.printable_boundary(base_poly, gap, r_min)
            raw_path = geo.laminate_layer(
                base_poly, gap, r_min, first_infill if i == 0 else config.infill_type,
                first_tracks if i == 0 else tracks,
                g.include_boundary, current_angle, layer_idx=_layer_index(i, first_infill, config),
                last_pt=last_pt,
                last_dir=last_dir, lead_in=_ring_lead_in(i, first_infill, config),
            )
            target_shapes.append((layer_z, printable, i + 1))
        except ValueError as exc:
            raise ValueError(f"Layer {i + 1}: {exc}") from exc

        anchor_n = 0
        if i == 0 and _ring_lead_in(i, first_infill, config):
            anchor_n = _points_within(raw_path, float(config.anchor_length_mm))
        elif i == 0:
            n_before = len(raw_path)
            raw_path = add_lead_in_anchor(raw_path, float(config.anchor_length_mm))
            anchor_n = len(raw_path) - n_before

        raw_path = enforce_min_radius(raw_path, r_min)
        layer_trajectory = plan_layer(raw_path, layer_z, i + 1, kin)
        _mark_anchor(layer_trajectory, anchor_n)
        append_layer(trajectory, layer_trajectory, i + 1, seam_trim, kin.v_min, min_run)

        if layer_trajectory:
            last_pt = (layer_trajectory[-1]['x'], layer_trajectory[-1]['y'])
            last_dir = _end_direction(layer_trajectory)

    return trajectory, target_shapes, max_z


_SPIRAL_INFILLS = (ARCHIMEDEAN, FERMAT, CORNER_STEP)
_ZIGZAGS = (ORIENTED_SERPENTINE, DIAGONAL_45)
_POLY_FILL_TRACKS = 100000   # Corner-Step on a polygon stops by itself when rings run out


def _layer_index(i, pattern, config: ProcessConfig):
    """Layer index handed to the layer generator (it sets the spiral's
    direction). A spiral FIRST layer under zigzag layers runs inside-out
    (as odd layers do), so it ends on the perimeter where the zigzag
    starts; ending in the centre, the next layer had to cut across its own
    lines to get out (up to 19 crossings)."""
    if i == 0 and pattern in _SPIRAL_INFILLS and config.infill_type in _ZIGZAGS:
        return 1
    return i


def _ring_lead_in(i, pattern, config: ProcessConfig):
    """Lead-in length to lay ON the innermost ring for an inside-out first
    layer (a straight anchor from the centre would be crossed by every
    ring); 0 when the usual straight anchor is used."""
    if _layer_index(i, pattern, config) == 1 and i == 0 and pattern in (ARCHIMEDEAN, CORNER_STEP) \
            and not config.geometry.include_boundary:
        return float(config.anchor_length_mm)
    return 0.0


def _points_within(path, length):
    """How many leading points of `path` lie within `length` mm of travel."""
    run = 0.0
    for k in range(1, len(path)):
        run += math.dist(path[k - 1], path[k])
        if run > length:
            return k
    return len(path)


def _fill_track_count(poly, config: ProcessConfig, pattern):
    """Track count for Fill Solid: None (as many as fit) for the spirals that
    stop by themselves; the exact fit for the analytic Corner-Step rectangle
    (0 if none); zigzags fill regardless."""
    if pattern == CORNER_STEP:
        if _is_rectangle(poly):
            return max_track_count(poly, config.track_gap_mm, config.min_radius_mm, CORNER_STEP,
                                   config.geometry.include_boundary) or 0
        return _POLY_FILL_TRACKS
    if pattern in (ARCHIMEDEAN, FERMAT):
        return None
    return config.tracks_per_layer


def _ring_loop(island, gap, r_min, cursor):
    """One closed lap of an island's wall centreline, from the point nearest
    `cursor` back to it: the fill for an island too thin for a Fermat
    spiral's two tracks (it enters and leaves at the same port)."""
    wall = geo.printable_boundary(island, gap, r_min)
    if wall.is_empty:
        return []
    wall = geo.largest_polygon(wall)
    if wall.geom_type != 'Polygon':
        return []
    return _ring_from(wall, _wall_seam(wall, cursor))


def _island_path(island, config: ProcessConfig, angle, layer_idx, cursor, fallbacks=None):
    """Path for one island of a multi-island slice that enters AND leaves on
    the island's perimeter, so the connector to the next island never
    crosses track already laid. Spiral infills use the Fermat double spiral
    (in and back out); line infills use the serpentine, run from whichever
    end is nearer the cursor. An island too narrow for the zigzag's U-turns
    gets the Fermat spiral, one too thin for that one lap of its wall (both
    counted in `fallbacks`)."""
    gap, r_min = config.track_gap_mm, config.min_radius_mm
    zigzag = config.infill_type in _ZIGZAGS and geo.zigzag_fits(island, gap, r_min)
    if not zigzag:
        tracks = None if config.fill_solid else config.tracks_per_layer
        path = geo.fermat_spiral_path(island, gap, r_min, track_count=tracks, entry_hint=cursor)
        if not path:
            path = _ring_loop(island, gap, r_min, cursor)
            if path and fallbacks is not None:
                fallbacks['lap'] += 1
        elif config.infill_type in _ZIGZAGS and fallbacks is not None:
            fallbacks['zigzag'] += 1
        return path
    if config.infill_type == DIAGONAL_45:
        angle = 45.0 if layer_idx % 2 == 0 else -45.0
    path = geo.oriented_serpentine(island, angle, gap, r_min)
    if cursor is not None and len(path) > 1:
        d0 = math.hypot(path[0][0] - cursor[0], path[0][1] - cursor[1])
        d1 = math.hypot(path[-1][0] - cursor[0], path[-1][1] - cursor[1])
        if d1 < d0:
            path = path[::-1]
    return path


_CONNECTOR_STEP_MM = 0.75


def _nearest_island(polys, point):
    pt = Point(point)
    return min(polys, key=lambda p: p.exterior.distance(pt))


def _boundary_point_toward(island, other):
    """Point on island's outline nearest the other geometry."""
    return nearest_points(island.exterior, other)[0].coords[0]


def _exit_route(island, end_pt, target, gap):
    """If the straight connector end_pt -> target would cut across the
    island's own laid track, retrace the outermost track ring to the point
    facing the target first (the route Fermat uses between its children)."""
    core = island.buffer(-gap, join_style=1)
    if core.is_empty or not LineString([end_pt, target]).intersects(core):
        return []
    ring = geo.largest_polygon(island.buffer(-gap / 2.0, join_style=1))
    if ring.is_empty or ring.geom_type != 'Polygon':
        return []
    exit_pt = nearest_points(ring.exterior, Point(target))[0].coords[0]
    return geo.arc_along_ring(ring, end_pt, exit_pt)[1:]


def _straight(p0, p1, step=_CONNECTOR_STEP_MM):
    """Points after p0 up to and including p1, ~step apart."""
    n = max(1, int(math.ceil(math.hypot(p1[0] - p0[0], p1[1] - p0[1]) / step)))
    return [(p0[0] + (p1[0] - p0[0]) * k / n, p0[1] + (p1[1] - p0[1]) * k / n) for k in range(1, n + 1)]


def _multi_island_layer(islands, config: ProcessConfig, angle, layer_idx, last_pt, fallbacks=None):
    """One stroke over every island: nearest island first, each filled from
    a port on its perimeter, joined by straight connectors (fibre bridges)
    densified to the usual point spacing so bridges can be flagged."""
    gap = config.track_gap_mm
    remaining = list(islands)
    if last_pt is not None:
        island = _nearest_island(remaining, last_pt)
        cursor = last_pt
    else:
        # First layer: start on the largest island, at the side facing the
        # island visited next, so the first connector is short.
        island = max(remaining, key=lambda p: p.area)
        others = [p for p in remaining if p is not island]
        nxt = min(others, key=lambda p: p.distance(island))
        cursor = _boundary_point_toward(island, nxt)
    path = [last_pt] if last_pt is not None else []
    while True:
        remaining.remove(island)
        sub = _island_path(island, config, angle, layer_idx, cursor, fallbacks)
        if sub:
            if path:
                path.extend(_straight(path[-1], sub[0]))
            path.extend(sub)
            cursor = sub[-1]
        if not remaining:
            break
        nxt = _nearest_island(remaining, cursor)
        if sub:
            target = nearest_points(nxt.exterior, Point(cursor))[0].coords[0]
            route = _exit_route(island, cursor, target, gap)
            path.extend(route)
            if route:
                cursor = route[-1]
        island = nxt
    return geo.dedupe_path(path)


def _stl_layer_path(islands, config: ProcessConfig, angle, layer_idx, last_pt, last_dir=None,
                    fallbacks=None, lead_in=0.0):
    """2D path for one sliced layer. A lone island goes through the normal
    flat-laminate generator. Where the chosen pattern cannot work on this
    slice it falls back instead of dropping the layer (each counted in
    `fallbacks`): a zigzag on walls too thin for its U-turns, a Fermat
    spiral with no room for its two tracks, or Corner-Step with no ring
    left, all become Archimedean rings; an Archimedean or Corner-Step track
    count that does not fit becomes as many rings as fit."""
    if fallbacks is None:
        fallbacks = Counter()
    if len(islands) > 1:
        return _multi_island_layer(islands, config, angle, layer_idx, last_pt, fallbacks)
    poly = islands[0]
    gap, r_min = config.track_gap_mm, config.min_radius_mm
    pattern = config.infill_type
    if pattern in _ZIGZAGS and not geo.zigzag_fits(poly, gap, r_min):
        pattern = ARCHIMEDEAN
        fallbacks['zigzag'] += 1
    tracks = (_fill_track_count(poly, config, pattern) if config.fill_solid
              else config.tracks_per_layer)

    def attempt(kind, count):
        return geo.laminate_layer(
            poly, gap, r_min, kind, count, config.geometry.include_boundary,
            angle, layer_idx=layer_idx, last_pt=last_pt, last_dir=last_dir,
            lead_in=lead_in if kind in (ARCHIMEDEAN, CORNER_STEP) else 0.0,
        )

    if pattern == ARCHIMEDEAN:
        try:
            return attempt(ARCHIMEDEAN, tracks)
        except ValueError:
            return attempt(ARCHIMEDEAN, None)
    if pattern == FERMAT:
        try:
            path = attempt(FERMAT, tracks)
        except ValueError:
            path = []
        if len(path) > 2:
            return path
        fallbacks['fermat'] += 1
        return attempt(ARCHIMEDEAN, None)
    if pattern == CORNER_STEP:
        for count in range(int(tracks or 0), 0, -1):
            try:
                path = attempt(CORNER_STEP, count)
            except ValueError:
                continue
            if path:
                return path
        fallbacks['corner'] += 1
        return attempt(ARCHIMEDEAN, None)
    return attempt(pattern, tracks)


_FALLBACK_NOTES = {
    'zigzag': "{n} slice(s) or island(s) are too narrow for the zigzag's U-turns (walls under "
              "{w:.1f} mm) and were filled with concentric rings instead.",
    'fermat': "{n} slice(s) have no room for the Fermat spiral's two tracks and were filled "
              "with Archimedean rings instead.",
    'corner': "{n} slice(s) fit no Corner-Step ring and were filled with Archimedean rings instead.",
    'lap': "{n} island(s) too thin for a spiral got one lap of their wall.",
}


def _flag_bridges(layer_trajectory, region, tol, skip=0):
    """Mark points whose incoming segment's midpoint lies outside the slice
    (inflated by `tol`): fibre spanning empty space between islands."""
    if len(layer_trajectory) < 2 or region.is_empty:
        return
    zone = region.buffer(tol)
    xy = np.array([(p['x'], p['y']) for p in layer_trajectory], dtype=np.float64)
    mid = 0.5 * (xy[1:] + xy[:-1])
    inside = shapely.contains_xy(zone, mid[:, 0], mid[:, 1])
    for k in np.nonzero(~inside)[0]:
        if k + 1 >= skip:
            layer_trajectory[k + 1]['is_bridge'] = True


def _mesh_report_notes(report, slices, layer_height):
    """Plain-language warnings / info from slice_mesh_regions' report."""
    warnings, info = [], []
    if report.get('stitched'):
        warnings.append(
            f"The mesh has holes: {report['stitched']} gap(s) in slice outlines were bridged "
            f"(Stitch Gap). Check or repair the model."
        )
    if report.get('open'):
        warnings.append(
            f"The mesh has holes too large to bridge: {report['open']} open outline(s) were "
            f"dropped, so parts of some slices are missing. Repair the model."
        )
    if slices:
        top = max(z for z, _ in slices)
        inside = [z for z in report.get('empty_planes', []) if z < top + layer_height]
        if inside:
            warnings.append(
                f"{len(inside)} slice(s) inside the model came out empty; the part has gaps "
                f"there (broken mesh, or separate bodies stacked with a gap)."
            )
    if report.get('bodies', 1) > 1:
        info.append(f"The mesh has {report['bodies']} separate bodies; they were merged where they overlap.")
    if report.get('subtractive'):
        info.append(f"{report['subtractive']} inward-facing body(ies) were treated as cavities.")
    return warnings, info


def build_stl_laminate(config: ProcessConfig):
    """STL-imported mesh: slice at layer_height intervals, generate infill
    per slice.

    `stl_islands="largest"` keeps one island per slice; `"all"` fills every
    island in one stroke, connector points flagged `is_bridge`.

    Returns (trajectory, target_shapes, max_z, warnings, info): warnings are
    problems found in the mesh or the fill (broken mesh, walls thinner than
    one track), info are neutral notes (several bodies merged)."""
    g = config.geometry
    mesh = load_stl(g.stl_path)
    tris = np.asarray(mesh.triangles, dtype=np.float64) * float(g.stl_scale)
    size = tris.reshape(-1, 3).max(axis=0) - tris.reshape(-1, 3).min(axis=0)
    gap, r_min = config.track_gap_mm, config.min_radius_mm
    if size[:2].max() < max(5.0, 2.0 * r_min + gap):
        raise ValueError(
            f"The model is only {size[0]:.3g} x {size[1]:.3g} x {size[2]:.3g} mm. If it was saved in "
            f"inches set STL Scale to 25.4, in cm to 10, in metres to 1000."
        )

    layer_height = max(float(config.layer_height_mm), 0.05)
    report: dict = {}
    slices = slice_mesh_regions(
        tris, layer_height, z_offset=float(config.z_offset_mm),
        closing_radius=float(g.stl_closing_radius_mm), stitch_mm=float(g.stl_stitch_mm),
        report=report,
    )

    if not slices:
        raise ValueError(
            f"STL mesh produced no valid slices (model {size[2]:.3g} mm tall, layer height "
            f"{layer_height:g} mm). Check the file, its scale (STL Scale) and the layer height."
        )
    warnings, info = _mesh_report_notes(report, slices, layer_height)
    if not mesh.is_watertight:
        warnings.insert(0,
            "The mesh is not watertight (it has open edges or missing faces), so its slices may "
            "not match the intended part, e.g. a missing wall reads as solid. Repair the model "
            "if the preview looks wrong."
        )

    trajectory: list[dict] = []
    target_shapes: list[tuple] = []
    max_z = float(config.z_offset_mm)
    last_pt = last_dir = None
    layup_sequence = list(config.layup_deg) or [0.0]
    kin = KinematicsParams.from_config(config)
    seam_trim = max(0.0, float(config.seam_trim_mm))
    gap, r_min = config.track_gap_mm, config.min_radius_mm
    # An island too small for one track turn cannot hold fibre.
    min_island_area = math.pi * (r_min + gap / 2.0) ** 2 * 0.25
    multi = g.stl_islands == "all"
    min_run = _ramp_min_run(layer_height, config.transition_angle_deg)
    thin_slices = 0
    fallbacks = Counter()

    for i, (z_print, region) in enumerate(slices):
        layer_z = z_print
        current_angle = layup_sequence[i % len(layup_sequence)]
        max_z = layer_z

        found = mesh_islands(region, min_island_area)
        if not found:
            continue
        # A wall narrower than one track: eroding half a track leaves ~nothing.
        if any(p.buffer(-0.5 * gap).area < 0.05 * p.area for p in found):
            thin_slices += 1
        islands = found if multi else found[:1]
        printable = unary_union([geo.printable_boundary(p, gap, r_min) for p in islands])
        target_shapes.append((layer_z, printable, i + 1))

        layer_cfg = config
        if not trajectory and (config.first_layer_solid or config.first_layer_infill):
            layer_cfg = config.model_copy(update=dict(
                fill_solid=config.fill_solid or config.first_layer_solid,
                infill_type=config.first_layer_infill or config.infill_type))
        try:
            idx = _layer_index(0, layer_cfg.infill_type, config) if not trajectory else i
            lead = _ring_lead_in(0, layer_cfg.infill_type, config) if not trajectory and len(islands) == 1 else 0.0
            raw_path = _stl_layer_path(islands, layer_cfg, current_angle, idx, last_pt, last_dir, fallbacks,
                                       lead_in=lead)
        except ValueError:
            continue
        if not raw_path:
            continue

        anchor_n = 0
        if not trajectory and lead:
            anchor_n = _points_within(raw_path, float(config.anchor_length_mm))
        elif not trajectory:
            n_before = len(raw_path)
            raw_path = add_lead_in_anchor(raw_path, float(config.anchor_length_mm))
            anchor_n = len(raw_path) - n_before

        raw_path = enforce_min_radius(raw_path, r_min)
        layer_trajectory = plan_layer(raw_path, layer_z, i + 1, kin)
        _mark_anchor(layer_trajectory, anchor_n)
        if len(islands) > 1:
            _flag_bridges(layer_trajectory, unary_union(islands), gap, skip=anchor_n + 1)
        append_layer(trajectory, layer_trajectory, i + 1, seam_trim, kin.v_min, min_run)

        if layer_trajectory:
            last_pt = (layer_trajectory[-1]['x'], layer_trajectory[-1]['y'])
            last_dir = _end_direction(layer_trajectory)

    if not trajectory:
        if thin_slices:
            raise ValueError(
                f"The model's walls are thinner than one {gap:g} mm track on {thin_slices} "
                f"slice(s), so no fibre fits. Thicken the walls, reduce Track Gap, or check "
                f"the model's scale (STL Scale)."
            )
        hint = (" The Fermat spiral needs room for two tracks (in and back out); Archimedean "
                "or Corner-Step fill single-track walls." if config.infill_type == FERMAT else "")
        raise ValueError(
            "No STL slice could be filled with these settings. Reduce Track Gap, "
            "Min Bend Radius or Tracks Per Layer, or check the model's scale (mm)." + hint
        )
    if thin_slices:
        warnings.append(
            f"{thin_slices} slice(s) have walls thinner than one {gap:g} mm track: the tow there is "
            f"wider than the wall (or the wall is skipped). Thicken the walls or reduce Track Gap."
        )
    for kind, n in sorted(fallbacks.items()):
        info.append(_FALLBACK_NOTES[kind].format(n=n, w=2.0 * max(r_min, gap) + gap))
    return trajectory, target_shapes, max_z, warnings, info
