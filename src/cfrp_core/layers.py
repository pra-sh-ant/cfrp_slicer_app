"""Layer assembly: lead-in anchor, per-layer speed planning, seam-trimmed
ramps between layers (spec §8–§9), and the flat-laminate and revolve-shell
layer loops (spec §7–§8).

A trajectory is a list of point dicts:
    x, y, z (mm), dist (mm from the previous point), v (mm/s),
    e_mult (extrusion factor), layer, layer_start, is_transition,
    pump_floored, z_layer, cure_dwell (not every point has every key).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from . import geometry as geo
from .config import FERMAT, ProcessConfig
from .geometry._common import enforce_min_radius
from .geometry.mesh_slicer import load_stl, slice_mesh
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
        )


# --- Seam trimming -----------------------------------------------------------

def trim_trajectory_tail(trajectory, trim_length):
    """Remove up to `trim_length` mm of travel from the end of `trajectory`
    (mutates it in place), never crossing into an earlier layer or a prior
    ramp, and never more than 40% of the last layer's run. Returns the exact
    point at the new end (interpolated if the cut falls mid-segment)."""
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
            del trajectory[idx:]
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


def plan_layer(raw_path, layer_z, layer_num, kin: KinematicsParams):
    """Speed-plan one layer's 2D path and tag every point with its layer."""
    layer_trajectory = calculate_trajectory(
        tuple(raw_path), kin.v_max, kin.v_min, kin.accel, kin.decel,
        kin.p_advance, z_height=layer_z, min_rad=kin.min_rad, pump_floor=kin.pump_floor,
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


def append_layer(trajectory, layer_trajectory, layer_num, seam_trim, ramp_speed):
    """Append one planned layer to `trajectory` (in place), bridged from the
    previous layer by a RAMPED Z-SEAM TRANSITION: trim `seam_trim` mm off the
    previous tail and this head, then fill the gap with a sloped move in
        steps = max(4 if Δz > 0 else 1, ⌈L/0.75⌉, ⌈|Δz|/0.05⌉)
    points at `ramp_speed`, e_mult 1. The last ramp point is `layer_start`.

    Fibre can only be laid forward/up: callers must append layers strictly
    bottom to top.
    """
    if trajectory and layer_trajectory:
        prev_cut = trim_trajectory_tail(trajectory, seam_trim)
        first_cut, layer_trajectory = trim_path_head(layer_trajectory, seam_trim)

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
        if t0 is None or t1 is None or chord_xy < 1e-9:
            t0 = t1 = None
        for step in range(1, steps + 1):
            progress = step / steps
            if t0 is None:
                px, py = prev_cut['x'] + dx * progress, prev_cut['y'] + dy * progress
            else:
                s = progress
                h00, h10 = 2 * s ** 3 - 3 * s ** 2 + 1, s ** 3 - 2 * s ** 2 + s
                h01, h11 = -2 * s ** 3 + 3 * s ** 2, s ** 3 - s ** 2
                m = chord_xy
                px = h00 * prev_cut['x'] + h10 * m * t0[0] + h01 * first_cut['x'] + h11 * m * t1[0]
                py = h00 * prev_cut['y'] + h10 * m * t0[1] + h01 * first_cut['y'] + h11 * m * t1[1]
            point = {'x': px, 'y': py, 'z': prev_cut['z'] + dzs * progress}
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


def _helical_connector(prev_path, next_start, cx, cy, step=0.75):
    """Seam between two revolve layers as ONE forward lap around the shell
    centre (vase-mode / spiralize style): keeps the spiral's sense of
    rotation, so the fibre never reverses, and moves radius from the old
    ring end to the new ring start with a smoothstep (zero radial slope at
    both ends). Z is ramped across the whole lap by the caller.

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
    radius = r0 + (r1 - r0) * (3.0 * u ** 2 - 2.0 * u ** 3)
    theta = a0 + sense * sweep * u
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
def _archimedean_cached(cross_section_wkb, gap, min_rad, start_from_inside):
    """Per-layer spiral for a revolve cross-section. Pure in its arguments, so
    changing speed / anchor / seam parameters re-slices without redoing the
    (dominant) polygon offsetting. Returns a tuple so the entry is immutable."""
    from shapely import wkb
    path, _ = geo.archimedean_path(
        wkb.loads(cross_section_wkb), gap, min_rad, track_count=None,
        start_from_inside=start_from_inside)
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
                    cross_section.wkb, gap, min_rad, is_outward))
        except ValueError:
            continue
        if not raw_path:
            continue

        if not anchored:
            if shape_type == 'round':
                raw_path = add_arc_lead_in_anchor(raw_path, float(config.anchor_length_mm), cx, cy)
            else:
                raw_path = add_lead_in_anchor(raw_path, float(config.anchor_length_mm))
            anchored = True

        layers_raw.append((i + 1, layer_z, enforce_min_radius(raw_path, min_rad)))
        if use_fermat:
            fermat_last_pt = layers_raw[-1][2][-1]

    if not layers_raw:
        return [], target_shapes, max_z

    # One continuous stroke for the whole shell (vase-mode style): every ring
    # is followed by a forward helical lap into the next ring, speed is
    # planned once over the whole path (no stop at seams), and Z climbs
    # steadily over each ring + its connector lap, so there is no seam block.
    # A layer that is just one closed ring (single-track walls) IS its own
    # helical lap: drop the ring and let the connector carry radius and Z, so
    # each layer costs one lap instead of ring + retraced lap. The first
    # layer (anchor) and last layer (top) stay whole.
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
            link = _helical_connector(path, trimmed[0], cx, cy)
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
    last_pt = None
    layup_sequence = list(config.layup_deg) or [0.0]
    kin = KinematicsParams.from_config(config)
    seam_trim = max(0.0, float(config.seam_trim_mm))
    gap, r_min = config.track_gap_mm, config.min_radius_mm
    layer_height = max(float(config.layer_height_mm), 0.05)

    for i in range(config.layers):
        layer_z = config.z_offset_mm + (i * layer_height)
        current_angle = layup_sequence[i % len(layup_sequence)]
        max_z = layer_z

        try:
            base_poly = geo.base_polygon(g, gap, r_min)

            printable = geo.printable_boundary(base_poly, gap, r_min)
            raw_path = geo.laminate_layer(
                base_poly, gap, r_min, config.infill_type, config.tracks_per_layer,
                g.include_boundary, current_angle, layer_idx=i, last_pt=last_pt,
            )
            target_shapes.append((layer_z, printable, i + 1))
        except ValueError as exc:
            raise ValueError(f"Layer {i + 1}: {exc}") from exc

        if i == 0:
            raw_path = add_lead_in_anchor(raw_path, float(config.anchor_length_mm))

        raw_path = enforce_min_radius(raw_path, r_min)
        layer_trajectory = plan_layer(raw_path, layer_z, i + 1, kin)
        append_layer(trajectory, layer_trajectory, i + 1, seam_trim, kin.v_min)

        if layer_trajectory:
            last_pt = (layer_trajectory[-1]['x'], layer_trajectory[-1]['y'])

    return trajectory, target_shapes, max_z


def build_stl_laminate(config: ProcessConfig):
    """STL-imported mesh: slice at layer_height intervals, generate infill
    per slice using the existing laminate_layer pipeline.

    Returns (trajectory, target_shapes, max_z)."""
    g = config.geometry
    triangles = load_stl(g.stl_path)

    layer_height = max(float(config.layer_height_mm), 0.05)
    slices = slice_mesh(triangles, layer_height, z_offset=float(config.z_offset_mm))

    if not slices:
        raise ValueError("STL mesh produced no valid slices. Check the file and layer height.")

    trajectory: list[dict] = []
    target_shapes: list[tuple] = []
    max_z = float(config.z_offset_mm)
    last_pt = None
    layup_sequence = list(config.layup_deg) or [0.0]
    kin = KinematicsParams.from_config(config)
    seam_trim = max(0.0, float(config.seam_trim_mm))
    gap, r_min = config.track_gap_mm, config.min_radius_mm

    for i, (z_print, poly) in enumerate(slices):
        layer_z = z_print
        current_angle = layup_sequence[i % len(layup_sequence)]
        max_z = layer_z

        printable = geo.printable_boundary(poly, gap, r_min)
        target_shapes.append((layer_z, printable, i + 1))

        try:
            raw_path = geo.laminate_layer(
                poly, gap, r_min, config.infill_type, config.tracks_per_layer,
                g.include_boundary, current_angle, layer_idx=i, last_pt=last_pt,
            )
        except ValueError:
            continue

        if not raw_path:
            continue

        if i == 0:
            raw_path = add_lead_in_anchor(raw_path, float(config.anchor_length_mm))

        raw_path = enforce_min_radius(raw_path, r_min)
        layer_trajectory = plan_layer(raw_path, layer_z, i + 1, kin)
        append_layer(trajectory, layer_trajectory, i + 1, seam_trim, kin.v_min)

        if layer_trajectory:
            last_pt = (layer_trajectory[-1]['x'], layer_trajectory[-1]['y'])

    return trajectory, target_shapes, max_z
