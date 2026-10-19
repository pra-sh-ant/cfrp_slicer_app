"""Flat multi-layer laminates (built-in outlines and Custom Outline)."""

from __future__ import annotations

from .. import geometry as geo
from ..config import ProcessConfig
from ..geometry._common import enforce_min_radius_clear, hole_keepouts
from .common import (
    KinematicsParams,
    _end_direction,
    _mark_anchor,
    _ramp_min_run,
    add_lead_in_anchor,
    append_layer,
    plan_layer,
)
from .planning import _fill_track_count, _points_within, plan_layers


def build_flat_laminate(config: ProcessConfig):
    """Flat multi-layer laminate:  z_i = z_offset + i·layer_height,
    fibre angle_i = layup[i mod len(layup)]; each layer's pattern, fill and
    direction from plan_layers (solid skins, spiral/zigzag hand-overs).

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

    base_poly = geo.base_polygon(g, gap, r_min)
    printable = geo.printable_boundary(base_poly, gap, r_min)
    tracks = {}
    for plan in plan_layers(config, config.layers):
        key = (plan.pattern, plan.solid)
        if key not in tracks:
            tracks[key] = (_fill_track_count(base_poly, config, plan.pattern) if plan.solid
                           else config.tracks_per_layer)

    for i, plan in enumerate(plan_layers(config, config.layers)):
        layer_z = config.z_offset_mm + (i * layer_height)
        current_angle = layup_sequence[i % len(layup_sequence)]
        max_z = layer_z

        try:
            raw_path = geo.laminate_layer(
                base_poly, gap, r_min, plan.pattern, tracks[plan.pattern, plan.solid],
                g.include_boundary, current_angle, layer_idx=plan.index,
                last_pt=last_pt, last_dir=last_dir, lead_in=plan.lead_in,
            )
            target_shapes.append((layer_z, printable, i + 1))
        except ValueError as exc:
            raise ValueError(f"Layer {i + 1}: {exc}") from exc

        anchor_n = 0
        if i == 0 and plan.lead_in:
            anchor_n = _points_within(raw_path, float(config.anchor_length_mm))
        elif i == 0:
            n_before = len(raw_path)
            raw_path = add_lead_in_anchor(raw_path, float(config.anchor_length_mm))
            anchor_n = len(raw_path) - n_before

        raw_path = enforce_min_radius_clear(raw_path, r_min, hole_keepouts(base_poly), gap * 0.25)
        layer_trajectory = plan_layer(raw_path, layer_z, i + 1, kin)
        _mark_anchor(layer_trajectory, anchor_n)
        append_layer(trajectory, layer_trajectory, i + 1, seam_trim, kin.v_min, min_run)

        if layer_trajectory:
            last_pt = (layer_trajectory[-1]['x'], layer_trajectory[-1]['y'])
            last_dir = _end_direction(layer_trajectory)

    return trajectory, target_shapes, max_z
