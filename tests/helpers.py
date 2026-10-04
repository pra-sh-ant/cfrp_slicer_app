"""Shared config builders for tests.

Every field the pipelines actually read from `sys_config` needs a sane
default here -- these mirror what the sidebar sends, trimmed to what
`resolve_process_constraints` / `_run_slicing_pipeline` / `_run_revolve_pipeline`
require, so tests read as "what's different about this case" instead of
repeating the whole config each time.
"""
from cfrp_core import DEFAULT_MACHINE, from_legacy_dict, slice_part


def run_slice(cfg, layup=(0.0,)):
    """Slice a legacy-style config dict; returns the legacy pipeline tuple
    (trajectory, target_shapes, gcode, validation, max_z)."""
    result = slice_part(from_legacy_dict(cfg, layup=list(layup)), DEFAULT_MACHINE)
    return result.trajectory, result.target_shapes, result.gcode, result.validation, result.max_z


def base_2d_config(**overrides):
    cfg = {
        "shape": "Square/Rectangle", "width": 100.0, "height": 15.0, "thickness": 10.0,
        "invert_bends": False, "track_gap": 1.2, "min_radius": 2.0,
        "start_x": 26.0, "start_y": 40.0, "z_offset": 0.2,
        "layer_height": 0.2, "layers": 3, "anchor_length": 5.0,
        "tracks_per_layer": 3, "infill_type": "Archimedean (Concentric Spiral)",
        "include_boundary": False,
        "v_max": 10.0, "v_min": 5.0, "accel": 25.0, "decel": 15.0, "p_advance": 3.0,
        "pump_stall_floor": 5.0, "transition_angle": 30.0, "seam_trim_length": 3.0,
        "e_const": 0.05, "purge_f": 100.0, "purge_e": 1.0, "travel_f": 1000.0,
        "uv": 225, "dwell": 0.1,
    }
    cfg.update(overrides)
    return cfg


def base_revolve_config(shape="Hollow Sphere (Z)", **overrides):
    cfg = {
        "shape": shape, "start_x": 90.0, "start_y": 110.0, "z_offset": 0.2,
        "stem_height": 20.0, "wall_thickness": 2.5, "track_gap": 1.2,
        "base_radius": 18.0, "v_max": 10.0, "v_min": 5.0, "v_z": 5.0, "v_overhang": 5.0,
        "dwell": 0.1, "min_radius": 2.0, "uv": 225, "e_const": 0.05,
        "purge_f": 100.0, "purge_e": 1.0, "travel_f": 1000.0,
        "layer_height": 1.0, "layers": 1, "anchor_length": 5.0, "tracks_per_layer": 4,
        "accel": 25.0, "decel": 15.0, "p_advance": 3.0, "pump_stall_floor": 5.0,
        "transition_angle": 30.0, "seam_trim_length": 3.0,
    }
    cfg.update(overrides)
    return cfg


def max_transition_angle_deg(trajectory):
    """The steepest Z-vs-XY climb angle among every interpolated ramp step."""
    import math

    max_angle = 0.0
    for i in range(1, len(trajectory)):
        a, b = trajectory[i - 1], trajectory[i]
        if not b.get("is_transition"):
            continue
        dz = abs(b["z"] - a["z"])
        if dz < 1e-9:
            continue
        dxy = math.hypot(b["x"] - a["x"], b["y"] - a["y"])
        angle = math.degrees(math.atan2(dz, dxy)) if dxy > 1e-9 else 90.0
        max_angle = max(max_angle, angle)
    return max_angle


def layer_xy_bounds(trajectory):
    """{layer_number: [min_x, max_x, min_y, max_y]} across the trajectory."""
    bounds = {}
    for pt in trajectory:
        layer = pt.get("layer")
        b = bounds.setdefault(layer, [1e9, -1e9, 1e9, -1e9])
        b[0] = min(b[0], pt["x"])
        b[1] = max(b[1], pt["x"])
        b[2] = min(b[2], pt["y"])
        b[3] = max(b[3], pt["y"])
    return bounds
