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

from . import geometry as geo
from .config import FERMAT, ProcessConfig
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

def add_lead_in_anchor(raw_path, anchor_length):
    """Prepend a straight lead-in of `anchor_length` mm in the −Y direction.

    The anchor always approaches from below (outside the part boundary) so it
    can be physically cut away after printing.  A single point is enough to
    mark the anchor start; the planner then ramps speed from rest."""
    if anchor_length <= 0 or len(raw_path) < 1:
        return raw_path
    p0 = raw_path[0]
    return [(p0[0], p0[1] - anchor_length)] + list(raw_path)


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
        for step in range(1, steps + 1):
            progress = step / steps
            point = {
                'x': prev_cut['x'] + dx * progress,
                'y': prev_cut['y'] + dy * progress,
                'z': prev_cut['z'] + dzs * progress,
            }
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

def _apply_spiral_z(layer_trajectory, z_start, z_end):
    """Reassign Z to ramp linearly from z_start to z_end based on cumulative
    path distance, so the layer transition is invisible."""
    if not layer_trajectory:
        return
    cum = 0.0
    cum_dists = [0.0]
    for i in range(1, len(layer_trajectory)):
        cum += layer_trajectory[i]['dist']
        cum_dists.append(cum)
    total = cum_dists[-1]
    if total < 1e-9:
        for pt in layer_trajectory:
            pt['z'] = z_start
        return
    for i, pt in enumerate(layer_trajectory):
        pt['z'] = z_start + (z_end - z_start) * (cum_dists[i] / total)


def build_revolve_shell(config: ProcessConfig):
    """Z-revolve shell (sphere/dome/cylinder/tube/taper/cone) with spiral
    Z-ramping: Z increases continuously along each layer's path so there
    is no sudden ramp between layers.

        num_layers = max(2, round(H / layer_height) + 1),  layer_height ≥ 0.05
        Layer i: Z ramps from z0 + H·i/(n-1) to z0 + H·(i+1)/(n-1)
        Last layer stays flat at z0 + H.

    Returns (trajectory, target_shapes, max_z)."""
    g = config.geometry
    shape = g.shape
    cx, cy = float(g.start_x_mm), float(g.start_y_mm)
    z0 = float(config.z_offset_mm)
    outer_radius = max(float(g.base_radius_mm), 1.0)
    gap = float(config.track_gap_mm)
    tracks = max(1, int(config.tracks_per_layer))
    shape_type = 'square' if shape == 'Hollow Square Tube (Z)' else 'round'
    profile = geo.revolve_radius_profile(shape, g.base_radius_mm, tracks, gap)
    kin = KinematicsParams.from_config(config)
    seam_trim = max(0.0, float(config.seam_trim_mm))
    use_fermat = config.infill_type == FERMAT

    if shape == 'Hollow Sphere (Z)':
        height = 2.0 * outer_radius
    else:
        height = max(float(g.stem_height_mm), 1e-6)

    layer_height = max(float(config.layer_height_mm), 0.05)
    num_layers = max(2, round(height / layer_height) + 1)

    trajectory = []
    target_shapes = []
    max_z = z0
    anchored = False
    fermat_last_pt = None   # end of the previous revolve layer, for seam phase-shifting

    for i in range(num_layers):
        progress = i / (num_layers - 1)
        layer_z = z0 + height * progress
        max_z = layer_z
        current_outer, current_inner = profile(progress)

        cross_section = geo.revolve_cross_section(cx, cy, current_outer, current_inner, shape_type)
        target_shapes.append((layer_z, cross_section, i + 1))
        if cross_section.is_empty or cross_section.area < 1e-6:
            continue

        is_outward = (i % 2 != 0)
        try:
            if use_fermat:
                # Phase-shift seam ~180° each layer so the Z-ramp has a long XY run.
                fermat_hint = None
                if fermat_last_pt is not None:
                    ccx = cross_section.centroid.x
                    ccy = cross_section.centroid.y
                    fermat_hint = (2.0 * ccx - fermat_last_pt[0],
                                   2.0 * ccy - fermat_last_pt[1])
                raw_path = geo.fermat_spiral_path(
                    cross_section, gap, float(config.min_radius_mm), track_count=None,
                    start_from_inside=is_outward, entry_hint=fermat_hint
                )
            else:
                raw_path, _ = geo.archimedean_path(
                    cross_section, gap, float(config.min_radius_mm), track_count=None,
                    start_from_inside=is_outward
                )
        except ValueError:
            continue
        if not raw_path:
            continue

        if not anchored:
            raw_path = add_lead_in_anchor(raw_path, float(config.anchor_length_mm))
            anchored = True

        layer_trajectory = plan_layer(raw_path, layer_z, i + 1, kin)

        # Spiral Z: ramp Z linearly from this layer's nominal Z to the next.
        if i < num_layers - 1:
            z_end = z0 + height * ((i + 1) / (num_layers - 1))
        else:
            z_end = layer_z
        _apply_spiral_z(layer_trajectory, layer_z, z_end)

        if trajectory and layer_trajectory:
            trim_trajectory_tail(trajectory, seam_trim)
            first_cut, layer_trajectory = trim_path_head(layer_trajectory, seam_trim)
            prev_end = trajectory[-1]
            dx = first_cut['x'] - prev_end['x']
            dy = first_cut['y'] - prev_end['y']
            dz = first_cut['z'] - prev_end['z']
            seg_len = math.sqrt(dx ** 2 + dy ** 2 + dz ** 2)
            steps = max(1, int(math.ceil(seg_len / 0.75)))
            for step in range(1, steps + 1):
                frac = step / steps
                px = prev_end['x'] + dx * frac
                py = prev_end['y'] + dy * frac
                pz = prev_end['z'] + dz * frac
                prior = trajectory[-1]
                d = math.sqrt(
                    (px - prior['x']) ** 2
                    + (py - prior['y']) ** 2
                    + (pz - prior['z']) ** 2
                )
                trajectory.append({
                    "x": px, "y": py, "z": pz,
                    "dist": float(d), "v": kin.v_min,
                    "e_mult": 1.0, "is_transition": True,
                    "layer_start": step == steps, "layer": i + 1,
                })

        trajectory.extend(layer_trajectory)

        if use_fermat and layer_trajectory:
            fermat_last_pt = (layer_trajectory[-1]['x'], layer_trajectory[-1]['y'])

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

            if g.shape == "Serpentine Infill":
                # base_poly IS the exact zigzag print extent; no wall to inset.
                printable = base_poly
                raw_path = geo.serpentine_infill(
                    g.width_mm, g.height_mm, r_min, gap, g.invert_bends,
                    current_angle, g.start_x_mm, g.start_y_mm,
                )
            else:
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

        layer_trajectory = plan_layer(raw_path, layer_z, i + 1, kin)
        append_layer(trajectory, layer_trajectory, i + 1, seam_trim, kin.v_min)

        if layer_trajectory:
            last_pt = (layer_trajectory[-1]['x'], layer_trajectory[-1]['y'])

    return trajectory, target_shapes, max_z
