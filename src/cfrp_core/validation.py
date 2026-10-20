"""Post-slice checks on the finished trajectory (spec §13)."""
from __future__ import annotations

import math

import numpy as np
from shapely.geometry import LineString, Polygon

from .config import ProcessConfig
from .constraints import cure_speed
from .geometry import dedupe_path
from .geometry._common import count_crossings, local_bend_radii
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


# Crossing counter shared with the zigzag generator (it picks the cleaner of
# two decompositions); see geometry._common.count_crossings.
_count_crossings = count_crossings


def _strict_bend_message(trajectory, keep, pts, tight, min_rad):
    """'Bend radius: N point(s) bend tighter than ... layer 3 (climb),
    layer 5 (turn)': each run of tight points named by what bent -- a
    climb (layer-to-layer ramp), a turn (the path turns back, 150° or
    more) or a join (any other kink)."""
    idx = np.nonzero(tight)[0]
    runs, cur = [], [idx[0]]
    for v in idx[1:]:
        if v - cur[-1] <= 3:
            cur.append(v)
        else:
            runs.append(cur)
            cur = [v]
    runs.append(cur)
    seg = np.diff(pts, axis=0)
    heading = np.arctan2(seg[:, 1], seg[:, 0])
    named = []
    for run in runs:
        pts_k = [keep[v + 1] for v in run]
        layer = trajectory[pts_k[0]].get('layer', 1)
        if any(trajectory[k].get('is_transition') for k in pts_k):
            kind = "climb"
        else:
            a, b = max(run[0] - 3, 0), min(run[-1] + 4, len(heading) - 1)
            turn = np.abs(np.diff(np.unwrap(heading[a:b + 1]))).sum() if b > a else 0.0
            kind = "turn" if math.degrees(turn) >= 150.0 else "join"
        item = f"layer {layer} ({kind})"
        if item not in named:
            named.append(item)
    shown = ", ".join(named[:8]) + (f" and {len(named) - 8} more" if len(named) > 8 else "")
    return (f"Bend radius (strict): {len(idx)} point(s) bend tighter than the {min_rad:g} mm min bend "
            f"radius: {shown}.")


def validate_trajectory(trajectory, config: ProcessConfig, machine: MachineProfile = DEFAULT_MACHINE,
                        target_shapes=None) -> dict:
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

    # 0b. UV cure (Phase 45): no point faster than the dose allows
    cure = cure_speed(config)
    if cure is not None:
        fast = [pt for pt in trajectory if float(pt.get('v', 0.0)) > cure * 1.01]
        if fast:
            layers = sorted(set(pt.get('layer', 1) for pt in fast))
            errors.append(
                f"UV cure: {len(fast)} point(s) on layer(s) {layers} run faster than the "
                f"{cure:.2f} mm/s at which the resin still gets its cure dose (up to "
                f"{max(float(pt['v']) for pt in fast):.2f} mm/s)."
            )

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
        g = config.geometry
        strict = bool(config.strict_bend_radius) and not g.is_revolve and not g.is_specialty
        tight = local_bend_radii(pts) < min_rad * 0.98 if len(pts) > 2 else np.zeros(0, bool)
        if strict and tight.any():
            # Strict Bend Radius: every bend counts, climbs included.
            errors.append(_strict_bend_message(trajectory, keep, pts, tight, min_rad))
            tight = np.zeros(0, bool)
        tight = tight & ~transition if len(tight) else tight
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

    # 6b. Fibre over a hole: the part's holes per layer (target_shapes carry
    #     them) must stay clear of the tow (ramps and the lead-in anchor,
    #     which lies on the bed outside the part, excepted).
    #     The layer outlines are the wall centreline (half a gap inside the
    #     part), so their holes are the part's holes grown by gap/2: shrink
    #     back to the real hole. Not for Z shells, whose climb between layers
    #     passes inside the bore of the (wider) layer below.
    if target_shapes and not config.geometry.is_revolve:
        import shapely
        holes_by_layer = {}
        shrink = gap / 2.0 + 0.1
        for _z, shape, layer in target_shapes:
            hs = [Polygon(r).buffer(-shrink) for g in getattr(shape, 'geoms', [shape])
                  if g.geom_type == 'Polygon' for r in g.interiors]
            hs = [h for h in hs if not h.is_empty]
            if hs:
                holes_by_layer[layer] = shapely.union_all(hs)
        over = {}
        for k in range(1, len(trajectory)):
            a, b = trajectory[k - 1], trajectory[k]
            layer = b.get('layer', 1)
            zone = holes_by_layer.get(layer)
            if zone is None or b.get('is_transition') or b.get('is_anchor') or b.get('is_bridge'):
                continue
            seg = LineString([(a['x'], a['y']), (b['x'], b['y'])])
            if seg.intersects(zone):
                over[layer] = over.get(layer, 0.0) + seg.intersection(zone).length
        if over:
            shown = sorted(over)[:8]
            errors.append(f"Fibre over a hole: {sum(over.values()):.1f} mm of tow crosses a hole of the part "
                          f"on layer(s) {shown}.")

    # 6c. Z never goes down (the nozzle would plough through printed fibre).
    drop, drop_layer = 0.0, None
    for k in range(1, len(trajectory)):
        dz = trajectory[k - 1]['z'] - trajectory[k]['z']
        if dz > drop + 1e-6:
            drop, drop_layer = dz, trajectory[k].get('layer', 1)
    if drop > 1e-4:
        errors.append(f"Z goes down by up to {drop:.2f} mm (layer {drop_layer}): the nozzle would drag "
                      f"through fibre already laid.")

    # 6d. Layer-to-layer climbs over empty space: a ramp must lie on the
    #     layer below or the layer it climbs to (a ramp across a hole or past
    #     the outline spans air). Z shells climb along their own wall.
    if target_shapes and not config.geometry.is_revolve:
        import shapely
        region_of = {}
        for _z, shape, layer in target_shapes:
            if shape is not None and not shape.is_empty:
                region_of[layer] = shape.buffer(gap / 2.0 + 0.3)
        void = {}
        for k in range(1, len(trajectory)):
            b = trajectory[k]
            if not b.get('is_transition'):
                continue
            a = trajectory[k - 1]
            layer = b.get('layer', 1)
            zones = [region_of[n] for n in (layer - 1, layer) if n in region_of]
            if not zones:
                continue
            seg = LineString([(a['x'], a['y']), (b['x'], b['y'])])
            out = seg.difference(shapely.union_all(zones)).length
            if out > 0.05:
                void[layer] = void.get(layer, 0.0) + out
        if void:
            errors.append(f"Layer climb over empty space: {sum(void.values()):.1f} mm of a layer-to-layer ramp "
                          f"crosses a hole or leaves the part on layer(s) {sorted(void)[:8]}.")

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
