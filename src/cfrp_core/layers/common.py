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
