"""Runs one golden case through the slicer and flattens the result into
comparable arrays. This adapter is the only file that must change when the
pipeline's entry point moves (e.g. into cfrp_core in Phase 1).
"""
import numpy as np

import machine as machine_mod
import presets
from app import _run_slicing_pipeline
from validation import resolve_process_constraints

FLOAT_FIELDS = ("x", "y", "z", "dist", "v", "e_mult")
INT_FIELDS = ("layer",)
BOOL_FIELDS = ("is_transition", "layer_start", "pump_floored", "cure_dwell")


def run_case(case):
    """Returns (gcode_text, arrays: dict[str, np.ndarray], meta: dict)."""
    cfg = resolve_process_constraints(dict(case["config"]))
    is_3d = cfg["shape"] in presets.Z_SPECIALTY_SHAPES
    trajectory, target_shapes, gcode, val, max_z = _run_slicing_pipeline(
        cfg, is_3d, list(case["layup"]), machine_mod.DEFAULT_MACHINE
    )
    return gcode, flatten(trajectory, target_shapes), {
        "status": val["status"],
        "messages": list(val["messages"]),
        "max_z": float(max_z),
        "constraint_notes": list(cfg["constraint_notes"]),
    }


def flatten(trajectory, target_shapes):
    arrays = {}
    for key in FLOAT_FIELDS:
        arrays[key] = np.array([float(pt[key]) for pt in trajectory], dtype=np.float64)
    for key in INT_FIELDS:
        arrays[key] = np.array([int(pt.get(key, 0)) for pt in trajectory], dtype=np.int64)
    for key in BOOL_FIELDS:
        arrays[key] = np.array([bool(pt.get(key, False)) for pt in trajectory], dtype=bool)

    arrays["ts_z"] = np.array([float(z) for z, _, _ in target_shapes], dtype=np.float64)
    arrays["ts_layer"] = np.array([int(layer) for _, _, layer in target_shapes], dtype=np.int64)
    arrays["ts_area"] = np.array([float(s.area) for _, s, _ in target_shapes], dtype=np.float64)
    arrays["ts_bounds"] = np.array(
        [s.bounds if not s.is_empty else (np.nan,) * 4 for _, s, _ in target_shapes],
        dtype=np.float64,
    ).reshape(-1, 4)
    return arrays
