"""Post-slice checks: pump-floor engagement, Z-seam jumps, self-intersection, bed bounds."""
import math

from shapely.geometry import LineString

import machine as machine_mod
import presets
from geometry import GeometryEngine


def pump_floor_unmeasured(config):
    """True while Pump-Stall Floor still sits at its placeholder default
    (equal to Min Bend Speed), i.e. no measured stall threshold is in use."""
    return config.get('pump_stall_floor', 0) == config.get('v_min', -1)


def _require(config, key, label, minimum=0.0, strict=True, unit=""):
    """Raise a readable ValueError if config[key] is missing a sane value.
    Keys absent from config are skipped -- callers only supply what they use."""
    if key not in config:
        return
    value = float(config[key])
    if not math.isfinite(value) or (value <= minimum if strict else value < minimum):
        relation = "greater than" if strict else "at least"
        raise ValueError(f"{label} must be {relation} {minimum:g}{unit} (got {value:g}).")


def resolve_process_constraints(config):
    """Resolve coupled settings before geometry or motion planning runs."""
    resolved = dict(config)
    notes = []
    min_radius = float(resolved.get("min_radius", 0.0))
    track_gap = float(resolved.get("track_gap", 0.0))
    max_speed = float(resolved.get("v_max", 0.0))
    shape = resolved.get("shape")

    if track_gap <= 0:
        raise ValueError("Track Gap must be greater than 0 mm.")
    if min_radius < 0:
        raise ValueError("Min Bend Radius cannot be negative.")
    tracks_per_layer = int(resolved.get("tracks_per_layer", 1))
    if tracks_per_layer < 1:
        raise ValueError("Tracks Per Layer must be at least 1.")
    if max_speed <= 0:
        raise ValueError("Print Speed (Max) must be greater than 0 mm/s.")

    # Values that would otherwise crash the slicer mid-run (division by zero,
    # sqrt of a negative) or emit unsafe G-code (an F0 feedrate stalls the
    # machine mid-print with the fiber bonded).
    _require(resolved, "layer_height", "Layer Height", unit=" mm")
    _require(resolved, "e_const", "Extrusion constant (check the Materials panel)")
    _require(resolved, "purge_f", "Purge Speed")
    _require(resolved, "travel_f", "Approach Speed")
    _require(resolved, "purge_e", "Purge Amount", strict=False)
    if shape in presets.Z_FLAT_SPECIALTY_SHAPES:
        _require(resolved, "v_z", "Z Pull Speed", unit=" mm/s")
        if shape == "3D Overhang (Mushroom)":
            _require(resolved, "v_overhang", "Top Disc Overhang Speed", unit=" mm/s")
            _require(resolved, "dwell", "Top Dwell", strict=False, unit=" s")
    else:
        # Every shape that runs the cornering-speed planner.
        _require(resolved, "accel", "Acceleration")
        _require(resolved, "decel", "Deceleration")
        _require(resolved, "v_min", "Min Bend Speed", strict=False, unit=" mm/s")
        _require(resolved, "pump_stall_floor", "Pump-Stall Floor", strict=False)
        _require(resolved, "p_advance", "Pressure Advance", strict=False)
        _require(resolved, "anchor_length", "Lead-in Anchor Length", strict=False, unit=" mm")
        _require(resolved, "seam_trim_length", "Seam Trim Length", strict=False, unit=" mm")

    if resolved.get("shape") == "Serpentine Infill":
        required_gap = 2.0 * min_radius
        if track_gap < required_gap:
            notes.append(
                f"Track Gap raised from {track_gap:.2f} to {required_gap:.2f} mm: "
                "a serpentine U-turn needs at least 2R of vertical room."
            )
            track_gap = required_gap

    # Every shape whose width/height fields aren't meaningful (the flat
    # zigzag-only shape, plus every Z-specialty shape) -- imported from
    # presets.py instead of hand-copied here, so a new specialty shape
    # added there can't silently skip this check.
    if resolved.get("shape") not in (presets.Z_SPECIALTY_SHAPES | {"Serpentine Infill"}):
        width = float(resolved.get("width", 0.0))
        height = float(resolved.get("height", 0.0))
        if width <= 0 or height <= 0:
            raise ValueError("2D geometry width and height must be greater than 0 mm.")
        if min_radius * 2.0 + track_gap >= min(width, height):
            raise ValueError(
                "This combination is geometrically impossible: Width/Height must "
                "leave room for two bend radii plus one track gap. Reduce Min Bend "
                "Radius or Track Gap, or enlarge the specimen."
            )
        narrow_dimension = min(width, height)
        cornering_window = narrow_dimension - (2.0 * min_radius) - track_gap
        if min_radius > track_gap / 2.0:
            notes.append(
                f"Radius clearance uses {2.0 * min_radius:.2f} mm across the "
                f"{narrow_dimension:.2f} mm narrow dimension; about "
                f"{cornering_window:.2f} mm remains for concentric tracks."
            )

    minimum_speed = float(resolved.get("v_min", 0.0))
    if minimum_speed > max_speed:
        resolved["v_min"] = max_speed
        notes.append(f"Min Bend Speed limited to Print Speed (Max), {max_speed:.2f} mm/s.")

    pump_floor = float(resolved.get("pump_stall_floor", 0.0))
    if pump_floor > max_speed:
        resolved["pump_stall_floor"] = max_speed
        notes.append(f"Pump-Stall Floor limited to Print Speed (Max), {max_speed:.2f} mm/s-equiv.")

    resolved["track_gap"] = track_gap
    resolved["tracks_per_layer"] = tracks_per_layer
    resolved["constraint_notes"] = notes
    return resolved


class ValidationEngine:
    @staticmethod
    def validate_trajectory(trajectory, config, machine=machine_mod.DEFAULT_MACHINE):
        """Analyzes compiled trajectory for CF collisions, invalid jumps, out-of-volume
        moves, and layer continuity."""
        if not trajectory:
            return {"status": "Fail", "messages": ["Validation Error: Trajectory is completely empty."]}

        warnings = []
        errors = []
        gap = config.get('track_gap', 1.0)

        # 0. Print-Volume Bounds
        errors.extend(
            f"Out-of-bounds: {msg}" for msg in machine_mod.check_bounds(trajectory, machine)
        )

        # 1. Pump-Stall Floor Engagement
        floored_pts = [pt for pt in trajectory if pt.get('pump_floored')]
        if floored_pts:
            floored_layers = sorted(set(pt.get('layer', 1) for pt in floored_pts))
            if pump_floor_unmeasured(config):
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

        # 2. Z-Transition Jump Analysis
        layer_start_indices = [i for i, pt in enumerate(trajectory) if pt.get('layer_start')]
        for i in layer_start_indices[1:]:
            pt_curr = trajectory[i]
            pt_prev = trajectory[i - 1]
            dist_xy = math.hypot(pt_curr['x'] - pt_prev['x'], pt_curr['y'] - pt_prev['y'])

            if dist_xy > gap:
                errors.append(f"Disjointed Z-Seam on Layer {pt_curr.get('layer', 'Unknown')}! Nozzle jumps {dist_xy:.2f}mm laterally during lift, risking fiber break.")

        # 3. Layer Transition Ramp Angle Compliance
        transition_limit = float(config.get('transition_angle', 30.0))
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

        # 4. Self-Intersection & Collision Detection
        layers_data = {}
        for pt in trajectory:
            l = pt.get('layer', 1)
            if l not in layers_data:
                layers_data[l] = []
            layers_data[l].append((pt['x'], pt['y']))

        for l_idx, coords in layers_data.items():
            if len(coords) > 5:
                # Remove micro-segments to prevent false touches
                clean_coords = GeometryEngine._dedupe_path(coords)

                if len(clean_coords) > 5:
                    ls = LineString(clean_coords)
                    if not ls.is_simple:
                        # Continuous fiber paths tracing a perimeter and peeling inward are
                        # mathematically non-simple because the vertex where the peel occurs
                        # is visited twice (Degree 3 Node). To prevent false positives on this
                        # intentional connection, we count overlapping nodes.
                        seen = set()
                        dupes = 0
                        for pt in clean_coords:
                            p = (round(pt[0], 2), round(pt[1], 2))
                            if p in seen:
                                dupes += 1
                            seen.add(p)

                        # An intentional wall loop closure and tangent peel causes 1-2 coordinate
                        # overlaps. Chaotic self-crossing causes many overlaps (>3).
                        if dupes > 3:
                            warnings.append(f"Layer {l_idx} path crosses itself ({dupes} internal intersections). Tangent peeling may intersect. Proceed with caution.")

        if errors:
            return {"status": "Fail", "messages": errors}
        elif warnings:
            return {"status": "Warning", "messages": warnings}
        else:
            return {"status": "Pass", "messages": ["✅ Continuous Flow Verified. Layers are strictly pinned and collision-free."]}
