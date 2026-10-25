"""Layer building blocks shared by every layer loop: kinematics parameters,
lead-in anchors, per-layer speed planning, seam trims and the ramped
Z-seam transition between layers (spec §8-§9)."""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..config import ProcessConfig
from ..kinematics import calculate_trajectory


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


def append_layer(trajectory, layer_trajectory, layer_num, seam_trim, ramp_speed, min_run=0.0, strict=None):
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

    `strict` (r_min, zone, gap): Strict Bend Radius. The climb is a curve that
    never bends under r_min (strict_climb) and stays in `zone`; the head
    cut moves further into the layer until one fits.
    """
    if trajectory and layer_trajectory and strict is not None and strict[0] > 0:
        prev_cut, xy, layer_trajectory = strict_climb(trajectory, layer_trajectory, seam_trim, min_run,
                                                      strict[0], strict[1], strict[2])
        _append_ramp(trajectory, prev_cut, xy, layer_trajectory[0]['z'], layer_num, ramp_speed,
                     step=min(0.75, 0.5 * strict[0]))
        trajectory.extend(layer_trajectory)
        return
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


# --- Strict bend radius (Phase 43) -------------------------------------------

def _append_ramp(trajectory, prev_cut, xy, z_to, layer_num, ramp_speed, step=0.75):
    """Append a climb along the XY polyline `xy` (points after prev_cut,
    ending where the layer resumes), resampled evenly by length (at most
    `step` mm apart: chords much over half the bend radius read a climb's
    arc ~2 % tight) so its slope is uniform; Z rises linearly to z_to."""
    import numpy as np
    pts = np.asarray([(prev_cut['x'], prev_cut['y'])] + list(xy), dtype=np.float64)
    seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    length = float(cum[-1])
    dzs = z_to - prev_cut['z']
    steps = max(4 if abs(dzs) > 1e-9 else 1, int(math.ceil(length / step)), int(math.ceil(abs(dzs) / 0.05)))
    s = np.linspace(0.0, length, steps + 1)[1:]
    xs, ys = np.interp(s, cum, pts[:, 0]), np.interp(s, cum, pts[:, 1])
    for step in range(1, steps + 1):
        point = {'x': float(xs[step - 1]), 'y': float(ys[step - 1]), 'z': prev_cut['z'] + dzs * step / steps}
        prior = trajectory[-1]
        trajectory.append({
            "x": point['x'], "y": point['y'], "z": point['z'],
            "dist": float(math.sqrt((point['x'] - prior['x']) ** 2 + (point['y'] - prior['y']) ** 2
                                    + (point['z'] - prior['z']) ** 2)),
            "v": ramp_speed, "e_mult": 1.0, "is_transition": True,
            "layer_start": step == steps, "layer": layer_num,
        })


def _tail_cuts(trajectory, lo, hi):
    """Where the previous layer's tail may be cut for a strict climb, from
    lo to hi mm before its end (within 40 % of that layer's run): at
    vertices, or inside straight segments longer than 3 mm at least 1.5 mm
    from their ends (a cut mid-arc leaves a short chord that reads ~2 %
    under the arc's radius). [(t, index, point or None for the vertex)],
    nearest the end first; at most 10."""
    target_layer = trajectory[-1].get('layer')
    run_start = len(trajectory) - 1
    while run_start > 0:
        pt = trajectory[run_start - 1]
        if pt.get('layer') != target_layer or pt.get('is_transition'):
            break
        run_start -= 1
    total = sum(math.hypot(b['x'] - a['x'], b['y'] - a['y'])
                for a, b in zip(trajectory[run_start:-1], trajectory[run_start + 1:], strict=False))
    hi = min(hi, 0.4 * total)
    lo = min(lo, hi)
    out, travelled = [], 0.0
    for idx in range(len(trajectory) - 1, run_start, -1):
        a, b = trajectory[idx - 1], trajectory[idx]
        seg = math.hypot(b['x'] - a['x'], b['y'] - a['y'])
        if seg >= 3.0:
            t = 1.5
            while t <= seg - 1.5:
                if lo <= travelled + t <= hi:
                    f = t / seg
                    out.append((travelled + t, idx, (b['x'] + (a['x'] - b['x']) * f, b['y'] + (a['y'] - b['y']) * f)))
                t += 0.5
        travelled += seg
        if lo - 1e-9 <= travelled <= hi:
            out.append((travelled, idx - 1, None))
        if travelled > hi:
            break
    if len(out) > 10:
        step = (len(out) - 1) / 9.0
        out = [out[round(i * step)] for i in range(10)]
    return out


def _apply_tail_cut(trajectory, cut):
    """Cut the trajectory's tail at a _tail_cuts entry; returns the cut
    point {x, y, z}."""
    _, idx, point = cut
    if point is None:
        del trajectory[idx + 1:]
        last = trajectory[-1]
        return {'x': last['x'], 'y': last['y'], 'z': last['z']}
    a, b = trajectory[idx - 1], trajectory[idx]
    del trajectory[idx + 1:]
    trajectory[idx] = dict(b, x=point[0], y=point[1], dist=math.hypot(point[0] - a['x'], point[1] - a['y']))
    return {'x': point[0], 'y': point[1], 'z': b['z']}


def _head_cuts(layer_trajectory, lo, hi):
    """Distances along the layer where a strict climb may land: vertices,
    and points of straight segments longer than 3 mm at least 1.5 mm from
    their ends (every 0.5 mm), between lo and hi; at most ~150."""
    out, s = [], 0.0
    for k in range(1, len(layer_trajectory)):
        a, b = layer_trajectory[k - 1], layer_trajectory[k]
        seg = math.hypot(b['x'] - a['x'], b['y'] - a['y'])
        if seg >= 3.0:
            t = 1.5
            while t <= seg - 1.5:
                if lo <= s + t <= hi:
                    out.append(s + t)
                t += 0.5
        s += seg
        if lo <= s <= hi:
            out.append(s)
        if s > hi:
            break
    if len(out) > 150:
        step = len(out) / 150.0
        out = [out[int(i * step)] for i in range(150)]
    return out


def _cut_at(layer_trajectory, d):
    """(cut point, remaining points) d mm into the layer; a cut on a vertex
    keeps the points after it."""
    s = 0.0
    for k in range(1, len(layer_trajectory)):
        a, b = layer_trajectory[k - 1], layer_trajectory[k]
        seg = math.hypot(b['x'] - a['x'], b['y'] - a['y'])
        if abs(s + seg - d) < 1e-9:
            return {'x': b['x'], 'y': b['y'], 'z': b['z']}, list(layer_trajectory[k + 1:])
        if s + seg > d:
            break
        s += seg
    return trim_path_head(layer_trajectory, d)


_CLIMB_PAIRS = 1500      # (tail cut, head cut) pairs a strict climb tries at most


def strict_climb(trajectory, layer_trajectory, seam_trim, min_run, r_min, zone, gap=1.0):
    """Join the trajectory's end to `layer_trajectory` for Strict Bend
    Radius: cut the tail (trajectory, in place) and the layer's head, and
    lay a Dubins curve at ARC_FACTOR·r_min (or 1.005·r_min, which fits
    between zigzag lines exactly 2·r_min apart) between the two cuts, along
    their headings. Pairs of cuts (_tail_cuts from seam_trim to 30 mm
    further, _head_cuts within 40 % of the layer), least fibre dropped
    first, are tried until a curve stays in `zone`, keeps every bend of the
    join at r_min, crosses none of the layer's remaining fibre at over 5°
    on its upper half (at the new layer's height), and is long enough for the transition angle (min_run); else the best
    found. Returns (tail cut point, climb XY points ending at the head cut,
    the layer's remaining
    points)."""
    import numpy as np
    import shapely
    from shapely.geometry import LineString
    from shapely.prepared import prep

    from ..geometry._common import local_bend_radii
    from ..geometry.strict import ARC_FACTOR, BEND_GUARD, TIGHT, dubins_curves, walk

    xy = np.asarray([(p['x'], p['y']) for p in layer_trajectory], dtype=np.float64)
    tails = _tail_cuts(trajectory, seam_trim, seam_trim + 30.0)
    if len(xy) < 3 or len(trajectory) < 2 or not tails:
        prev_cut = trim_trajectory_tail(trajectory, seam_trim)
        cut, rest = trim_path_head(layer_trajectory, seam_trim)
        return prev_cut, [(cut['x'], cut['y'])], rest
    seg = np.linalg.norm(np.diff(xy, axis=0), axis=1)
    layer_len = float(seg.sum())
    # The layer's own repair left its bends up to BEND_GUARD past its first
    # point (after the kept head) to the climb: the bend check runs past
    # them, plus a bend window. (Checking 3 mm past a cut on the hop to the
    # first point missed a 90° corner 6 mm on.)
    guarded = float(seg[0]) + BEND_GUARD + 1.5
    # The crossing index on the layer cut into pieces of at most 3 mm: a
    # long diagonal zigzag line has a box that every query hits.
    pieces = [xy[:1]]
    for a, b, length in zip(xy[:-1], xy[1:], seg, strict=True):
        n = max(1, int(math.ceil(length / 3.0)))
        pieces.append(a + (b - a) * (np.arange(1, n + 1, dtype=np.float64) / n)[:, None])
    dense = np.vstack(pieces)
    dseg = np.linalg.norm(np.diff(dense, axis=0), axis=1)
    cum = np.concatenate([[0.0], np.cumsum(dseg)])
    tree = shapely.STRtree(shapely.linestrings(np.stack([dense[:-1], dense[1:]], axis=1)))
    dirs = np.diff(dense, axis=0) / np.maximum(dseg, 1e-12)[:, None]
    area = prep(zone) if zone is not None and not zone.is_empty else None
    heads = _head_cuts(layer_trajectory, seam_trim, max(0.4 * layer_len, seam_trim))

    def tail_geometry(cut):
        """(point, heading, the 3 mm before it) of a tail cut."""
        _, idx, point = cut
        if point is None:
            p0, k = (trajectory[idx]['x'], trajectory[idx]['y']), idx - 1
        else:
            p0, k = point, idx - 1
        back = [p0] + [(trajectory[j]['x'], trajectory[j]['y']) for j in range(k, max(k - 60, -1), -1)]
        back = [q for n, q in enumerate(back) if n == 0 or math.dist(q, back[n - 1]) > 1e-9]
        if len(back) < 2:
            return None
        h0 = math.atan2(p0[1] - back[1][1], p0[0] - back[1][0])
        return p0, h0, walk(back, 3.0)[::-1][:-1]

    def head_geometry(d):
        cut, rest = _cut_at(layer_trajectory, d)
        if len(rest) < 2:
            return None
        p1 = (cut['x'], cut['y'])
        ahead = [p1] + [(p['x'], p['y']) for p in rest]
        ahead = [q for n, q in enumerate(ahead) if n == 0 or math.dist(q, ahead[n - 1]) > 1e-9]
        if len(ahead) < 2:
            return None
        return p1, math.atan2(ahead[1][1] - p1[1], ahead[1][0] - p1[0]), walk(ahead, max(3.0, guarded - d)), rest

    landed = {}

    def landing_crossings(d, head):
        """Crossings of the landing segment's kept piece with the layer's
        later track: a cut on the straight hop from the last layer's end to
        where this one starts keeps the rest of that hop, which can cross
        the layer's lines."""
        if d not in landed:
            piece = np.asarray(head[:2], dtype=np.float64)
            length = float(np.linalg.norm(piece[1] - piece[0]))
            j = tree.query(shapely.linestrings(piece), predicate='crosses')
            landed[d] = int(np.count_nonzero(cum[j] > d + length + 1e-6))
        return landed[d]

    pairs = sorted(((t[0] + d, ti, d) for ti, t in enumerate(tails) for d in heads), key=lambda q: q[0])
    tail_geo, head_geo = {}, {}
    best = None
    for _, ti, d in pairs[:_CLIMB_PAIRS]:
        if ti not in tail_geo:
            tail_geo[ti] = tail_geometry(tails[ti])
        if d not in head_geo:
            head_geo[d] = head_geometry(d)
        tg, hg = tail_geo[ti], head_geo[d]
        if tg is None or hg is None:
            continue
        p0, h0, tail_pts = tg
        p1, h1, head, rest = hg
        curves = dubins_curves(p0, h0, p1, h1, ARC_FACTOR * r_min) + dubins_curves(p0, h0, p1, h1, 1.005 * r_min)
        for curve in curves:
            # Checks cheapest first; a candidate that can no longer beat the
            # best one so far is dropped as soon as that is clear.
            arr = np.asarray([p0] + curve, dtype=np.float64)
            line = shapely.linestrings(arr)
            outside = int((area is not None and not area.covers(line)) or not line.is_simple)
            if best is not None and (outside,) > best[0][:1]:
                continue
            joined = np.asarray(tail_pts + [p0] + curve + head[1:], dtype=np.float64)
            tight = int(bool((local_bend_radii(joined) < TIGHT * r_min).any()))
            if best is not None and (outside, tight) > best[0][:2]:
                continue
            i, j = tree.query(shapely.linestrings(np.stack([arr[:-1], arr[1:]], axis=1)), predicate='crosses')
            # Only the climb's upper part is at the new layer's height (Z
            # rises with length); the new layer crossing its lower half lies
            # on it as on the layer below (the audit, too, counts crossings
            # only within half a layer height). 45 % kept as margin.
            run = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(arr, axis=0), axis=1))])
            later = (cum[j] >= d - 1e-6) & (run[i + 1] >= 0.45 * run[-1])
            crossings = 0
            if later.any():
                # Any crossing over 5°: a loop that grazes a line crosses it at
                # ~15° and the audit, on resampled points, can read it over
                # 20°; a climb laid along the track it joins (a spiral's first
                # lap closing over its start) crosses it numerically at ~0°.
                dd = np.diff(arr, axis=0)
                dd /= np.maximum(np.linalg.norm(dd, axis=1), 1e-12)[:, None]
                cos = np.abs(np.einsum('ij,ij->i', dd[i[later]], dirs[j[later]]))
                crossings = int(np.count_nonzero(cos < math.cos(math.radians(5.0))))
            crossings += int(line.crosses(LineString(head[:2])))     # the landing segment's kept piece
            crossings += landing_crossings(d, head)
            short = int(line.length < min_run - 1e-6)
            score = (outside, tight, crossings, short)
            if best is None or score < best[0]:
                best = (score, ti, curve, rest)
            if score == (0, 0, 0, 0):
                break
        if best is not None and best[0] == (0, 0, 0, 0):
            break
    if best is None:
        prev_cut = trim_trajectory_tail(trajectory, seam_trim)
        cut, rest = trim_path_head(layer_trajectory, seam_trim)
        return prev_cut, [(cut['x'], cut['y'])], rest
    _, ti, curve, rest = best
    return _apply_tail_cut(trajectory, tails[ti]), curve, rest


class StrictBends:
    """Strict Bend Radius for one layer loop: repairs each layer's path
    (geometry.strict.strict_path), the zone a climb must stay in, and the
    area left empty because of the bend radius, per layer."""

    def __init__(self, config: ProcessConfig):
        self.on = bool(config.strict_bend_radius) and float(config.min_radius_mm) > 0
        self.r_min = float(config.min_radius_mm)
        self.gap = float(config.track_gap_mm)
        self.lost: dict[int, float] = {}

    def allowed(self, region, path):
        """Where a repair may lay fibre: the layer's region, 0.4 gap in from
        its edges and holes, plus a corridor round the path's own bridges
        (segments outside the region, between islands)."""
        import numpy as np
        import shapely
        zone = region.buffer(-0.4 * self.gap, join_style=1)
        pts = np.asarray(path, dtype=np.float64).reshape(-1, 2)
        if len(pts) > 1:
            mid = 0.5 * (pts[1:] + pts[:-1])
            out = ~shapely.contains_xy(region.buffer(0.5 * self.gap), mid[:, 0], mid[:, 1])
            if out.any():
                k = np.nonzero(out)[0]
                bridges = shapely.linestrings(np.stack([pts[k], pts[k + 1]], axis=1))
                zone = zone.union(shapely.union_all(shapely.buffer(bridges, 2.0 * self.r_min)))
        return zone

    def repair(self, path, region, keep_head=0):
        """The path with no bend under r_min (a StrictPath; `lost_area`
        set), or the path unchanged when Strict Bend Radius is off."""
        from ..geometry.strict import StrictPath, lost_area, strict_path
        if not self.on or not path or len(path) < 3:
            return path
        fixed = strict_path(path, self.r_min, self.allowed(region, path), self.gap, keep_head)
        fixed.lost_area = lost_area(path, fixed, region, self.gap) if fixed.repairs else 0.0
        return fixed if isinstance(fixed, StrictPath) else path

    def record(self, layer, path):
        area = float(getattr(path, 'lost_area', 0.0) or 0.0)
        if self.on and area > 0.5:
            self.lost[layer] = self.lost.get(layer, 0.0) + area

    def climb(self, region):
        """`strict` argument for append_layer: (r_min, zone, gap), or None."""
        if not self.on:
            return None
        return self.r_min, region, self.gap

    def note(self) -> str:
        """'Left empty because of the bend radius: 12 mm² on layer 2, ...' or ''."""
        if not self.lost:
            return ""
        items = sorted(self.lost.items())
        shown = ", ".join(f"{a:.0f} mm² on layer {k}" for k, a in items[:8])
        more = f" and {len(items) - 8} more layer(s)" if len(items) > 8 else ""
        return (f"Left empty because of the bend radius: {shown}{more} (no curve at the "
                f"{self.r_min:g} mm min bend radius fits there).")

