"""Layer assembly: lead-in anchor, per-layer speed planning, seam-trimmed
ramps between layers (spec §8–§9), and the flat-laminate and revolve-shell
layer loops (spec §7–§8).

A trajectory is a list of point dicts:
    x, y, z (mm), dist (mm from the previous point), v (mm/s),
    e_mult (extrusion factor), layer, layer_start, is_transition,
    pump_floored, z_layer, cure_dwell, is_bridge, is_anchor (not every point
    has every key).
"""
from .common import (
    KinematicsParams,
    add_arc_lead_in_anchor,
    add_lead_in_anchor,
    append_layer,
    plan_layer,
    trim_path_head,
    trim_trajectory_tail,
)
from .flat import build_flat_laminate
from .planning import LayerPlan, plan_layers, skin_summary, stl_skins
from .revolve import build_revolve_shell
from .stl import build_stl_laminate

__all__ = [
    "KinematicsParams", "LayerPlan", "add_arc_lead_in_anchor", "add_lead_in_anchor", "append_layer",
    "build_flat_laminate", "build_revolve_shell", "build_stl_laminate", "plan_layer", "plan_layers",
    "skin_summary", "stl_skins", "trim_path_head", "trim_trajectory_tail",
]
