"""Post-slice checks on the finished trajectory (spec §13)."""
from __future__ import annotations

import math

import numpy as np
from shapely import STRtree
from shapely.geometry import LineString

from .config import ProcessConfig
from .geometry import dedupe_path
from .geometry._common import local_bend_radii
from .machine import DEFAULT_MACHINE, MachineProfile, check_bounds


def _z_chunks(pts3, z_tol):
    """Split consecutive (x, y, z) points into runs whose Z span stays
    within z_tol; yields lists of (x, y)."""
    chunk, z_lo, z_hi = [], None, None
    for x, y, z in pts3:
        if chunk and max(z_hi, z) - min(z_lo, z) > z_tol:
            yield chunk
            chunk, z_lo, z_hi = [], None, None
        chunk.append((x, y))
        z_lo = z if z_lo is None else min(z_lo, z)
        z_hi = z if z_hi is None else max(z_hi, z)
    if chunk:
        yield chunk


# Two tows of width w meeting at angle a share w / sin(a) of length: ~3.5 w
# at 20 deg (a crossing), ~11 w at 5 deg (side-by-side overlap, e.g. a
# helical connector lapping its own ring at a slightly higher Z).
_CROSSING_MIN_ANGLE_DEG = 20.0


def _count_crossings(coords):
    """Pairs of non-adjacent segments that properly cross at more than
    _CROSSING_MIN_ANGLE_DEG. Touching, retracing or near-parallel overlap at
    a seam (numerically an X at ~0 deg) is not a crossing; tow laid on tow is
    measured by the coverage metric instead."""
    pts = np.asarray(coords, dtype=np.float64)
    d = np.diff(pts, axis=0)
    d /= np.maximum(np.linalg.norm(d, axis=1), 1e-12)[:, None]
    cos_min = math.cos(math.radians(_CROSSING_MIN_ANGLE_DEG))
    segs = [LineString(coords[i:i + 2]) for i in range(len(coords) - 1)]
    tree = STRtree(segs)
    count = 0
    for i, seg in enumerate(segs):
        for j in tree.query(seg, predicate='crosses'):
            if j > i + 1 and abs(float(d[i] @ d[j])) < cos_min:
                count += 1
    return count


def validate_trajectory(trajectory, config: ProcessConfig, machine: MachineProfile = DEFAULT_MACHINE) -> dict:
    """Check bed bounds, pump-floor engagement, seam jumps, ramp angle and
    self-crossing. Returns {"status": "Pass"|"Warning"|"Fail", "messages": [...]}
    (Fail if any error, else Warning if any warning, else Pass)."""
    if not trajectory:
        return {"status": "Fail", "messages": ["Validation Error: Trajectory is completely empty."]}

    warnings = []
    errors = []
    gap = config.track_gap_mm

    # 0. Print-volume bounds
    errors.extend(f"Out-of-bounds: {msg}" for msg in check_bounds(trajectory, machine))

    # 1. Pump-stall floor engagement
    floored_pts = [pt for pt in trajectory if pt.get('pump_floored')]
    if floored_pts:
        floored_layers = sorted(set(pt.get('layer', 1) for pt in floored_pts))
        if config.kinematics.pump_floor_unmeasured:
            warnings.append(
                f"Pump-Stall Floor engaged at {len(floored_pts)} point(s) on layer(s) "
                f"{floored_layers}, but the floor value is still UNMEASURED (defaults to "
                f"Min Bend Speed). These points are not actually protected against fiber "
                f"stall -- run the pump step-response test and update the floor value."
            )
        else:
            warnings.append(
                f"Pump-Stall Floor engaged at {len(floored_pts)} point(s) on layer(s) "
                f"{floored_layers}: pump flow was held above the geometric cornering speed "
                f"to stay above your measured shear-stall threshold."
            )

    # 2. Z-transition jump: lateral XY move at each layer start (after the first) > gap
    layer_start_indices = [i for i, pt in enumerate(trajectory) if pt.get('layer_start')]
    for i in layer_start_indices[1:]:
        pt_curr = trajectory[i]
        pt_prev = trajectory[i - 1]
        dist_xy = math.hypot(pt_curr['x'] - pt_prev['x'], pt_curr['y'] - pt_prev['y'])

        if dist_xy > gap:
            errors.append(f"Disjointed Z-Seam on Layer {pt_curr.get('layer', 'Unknown')}! Nozzle jumps {dist_xy:.2f}mm laterally during lift, risking fiber break.")

    # 3. Layer-transition ramp angle: atan(Δz/Δxy) > limit + 2° slack
    transition_limit = float(config.transition_angle_deg)
    angle_tolerance = 2.0  # deg of slack for step discretization
    steep_layers = set()
    for i in range(1, len(trajectory)):
        pt = trajectory[i]
        if not pt.get('is_transition'):
            continue
        prev = trajectory[i - 1]
        dz = abs(pt['z'] - prev['z'])
        if dz < 1e-9:
            continue
        dxy = math.hypot(pt['x'] - prev['x'], pt['y'] - prev['y'])
        angle_deg = math.degrees(math.atan2(dz, dxy)) if dxy > 1e-9 else 90.0
        if angle_deg > transition_limit + angle_tolerance:
            steep_layers.add(pt.get('layer', 'Unknown'))
    if steep_layers:
        warnings.append(
            f"Layer transition ramp exceeded the configured {transition_limit:.1f} deg limit on "
            f"layer(s) {sorted(steep_layers)}: the part footprint is too small to fit a full "
            f"ramp at this layer height, so the climb is steeper than requested there. Reduce "
            f"layer height, enlarge the part, or increase the transition angle allowance."
        )

    # 4. Self-intersection: per layer, segments that properly cross (seam
    #    touches and retraced points are not crossings; see _count_crossings).
    #    Z-aware: a spiral-ramped layer climbs while it circles, so laps that
    #    overlap in XY at a different Z are not crossings. Each layer is cut
    #    into chunks spanning < half a layer height of Z and only crossings
    #    inside one chunk count.
    z_tol = 0.5 * float(config.layer_height_mm)
    layers_data = {}
    for pt in trajectory:
        layer = pt.get('layer', 1)
        layers_data.setdefault(layer, []).append((pt['x'], pt['y'], pt['z']))

    for l_idx, pts3 in layers_data.items():
        crossings = 0
        for chunk in _z_chunks(pts3, z_tol):
            clean_coords = dedupe_path(chunk)
            if len(clean_coords) > 5 and not LineString(clean_coords).is_simple:
                crossings += _count_crossings(clean_coords)
        if crossings:
            warnings.append(f"Layer {l_idx} path crosses itself ({crossings} internal intersections). Tangent peeling may intersect. Proceed with caution.")

    # 5. Curvature: non-transition vertices tighter than the min bend radius
    #    (checked on the final, bed-fitted points the G-code is written from).
    min_rad = float(config.min_radius_mm)
    if min_rad > 0 and len(trajectory) > 2:
        # Collapse sub-0.05 mm slivers first (as dedupe_path does for paths):
        # a near-duplicate point turns a tiny kink into a fake tight bend.
        keep, lx, ly = [0], trajectory[0]['x'], trajectory[0]['y']
        for k in range(1, len(trajectory)):
            p = trajectory[k]
            if math.hypot(p['x'] - lx, p['y'] - ly) > 0.05:
                keep.append(k)
                lx, ly = p['x'], p['y']
        pts = np.array([(trajectory[k]['x'], trajectory[k]['y']) for k in keep], dtype=np.float64)
        transition = np.array([bool(trajectory[k].get('is_transition')) for k in keep[1:-1]], dtype=bool)
        # 2% slack: fillets are arcs at exactly min_rad; sampling them, and
        # seam cut points inserted mid-arc, read up to ~1.7% under.
        tight = (local_bend_radii(pts) < min_rad * 0.98) & ~transition if len(pts) > 2 else np.zeros(0, bool)
        if tight.any():
            tight_layers = sorted({trajectory[keep[i + 1]].get('layer', 1) for i in np.nonzero(tight)[0]})
            shown = tight_layers[:8] + (["..."] if len(tight_layers) > 8 else [])
            warnings.append(
                f"Curvature: {int(tight.sum())} point(s) are tighter than the {min_rad:.1f} mm min "
                f"bend radius on layer(s) {shown}; these are speed-limited, not geometrically safe."
            )

    # 6. Island bridges: fibre laid across empty space between islands of
    #    one slice (multi-island STL). On the first layer it lies on the bed
    #    and is trimmed after cure; above that it spans unsupported.
    bridge_len = {}
    for pt in trajectory:
        if pt.get('is_bridge'):
            layer = pt.get('layer', 1)
            bridge_len[layer] = bridge_len.get(layer, 0.0) + float(pt.get('dist', 0.0))
    if bridge_len:
        first_layer = trajectory[0].get('layer', 1)
        upper = {k: v for k, v in bridge_len.items() if k != first_layer}
        msg = (
            f"Island bridges: {sum(bridge_len.values()):.1f} mm of fibre spans empty space "
            f"between islands on {len(bridge_len)} layer(s)"
        )
        if upper:
            worst = max(upper, key=upper.get)
            msg += (f"; {sum(upper.values()):.1f} mm is unsupported above the first layer "
                    f"(worst: layer {worst}, {upper[worst]:.1f} mm). Trim the ties after cure.")
        else:
            msg += " (first layer only, lying on the bed). Trim the ties after cure."
        warnings.append(msg)

    # 7. Layers that produced no path (e.g. a slice too small for one track
    #    turn): the part has a gap there.
    present = sorted({pt.get('layer', 1) for pt in trajectory})
    missing = sorted(set(range(present[0], present[-1] + 1)) - set(present))
    if missing:
        shown = missing[:8] + (["..."] if len(missing) > 8 else [])
        warnings.append(
            f"No toolpath on layer(s) {shown}: those slices were too small or failed to fill, "
            f"so the part has a gap there."
        )

    if errors:
        return {"status": "Fail", "messages": errors}
    elif warnings:
        return {"status": "Warning", "messages": warnings}
    else:
        return {"status": "Pass", "messages": ["✅ Continuous Flow Verified. Layers are strictly pinned and collision-free."]}
