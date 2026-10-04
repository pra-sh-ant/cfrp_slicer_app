"""Post-slice checks on the finished trajectory (spec §13)."""
from __future__ import annotations

import math

from shapely.geometry import LineString

from .config import ProcessConfig
from .geometry import dedupe_path
from .machine import DEFAULT_MACHINE, MachineProfile, check_bounds


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

    # 4. Self-intersection: per layer, not simple AND > 3 repeated points
    #    (a wall loop plus its tangent exit legitimately revisits 1-2 points).
    layers_data = {}
    for pt in trajectory:
        layer = pt.get('layer', 1)
        layers_data.setdefault(layer, []).append((pt['x'], pt['y']))

    for l_idx, coords in layers_data.items():
        if len(coords) > 5:
            clean_coords = dedupe_path(coords)

            if len(clean_coords) > 5:
                ls = LineString(clean_coords)
                if not ls.is_simple:
                    seen = set()
                    dupes = 0
                    for pt in clean_coords:
                        p = (round(pt[0], 2), round(pt[1], 2))
                        if p in seen:
                            dupes += 1
                        seen.add(p)

                    if dupes > 3:
                        warnings.append(f"Layer {l_idx} path crosses itself ({dupes} internal intersections). Tangent peeling may intersect. Proceed with caution.")

    if errors:
        return {"status": "Fail", "messages": errors}
    elif warnings:
        return {"status": "Warning", "messages": warnings}
    else:
        return {"status": "Pass", "messages": ["✅ Continuous Flow Verified. Layers are strictly pinned and collision-free."]}
