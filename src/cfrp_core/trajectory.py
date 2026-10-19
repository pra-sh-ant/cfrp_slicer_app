"""The trajectory point: every key a point may carry, and a checker.

A trajectory stays a `list[dict]` (G-code, validation and the viewer read
it); `TrajectoryPoint` documents the keys for type checkers and
`check_trajectory` catches a misspelled or missing key, which a plain dict
would otherwise turn silently into "absent".
"""
from __future__ import annotations

import math
from typing import NotRequired, TypedDict


class TrajectoryPoint(TypedDict):
    x: float
    y: float
    z: float
    dist: float                       # mm from the previous point
    v: float                          # planned speed, mm/s
    e_mult: NotRequired[float]        # extrusion factor (fibre); 1.0 for DIW
    layer: NotRequired[int]
    layer_start: NotRequired[bool]
    is_transition: NotRequired[bool]  # layer-to-layer ramp
    pump_floored: NotRequired[bool]
    z_layer: NotRequired[float]
    cure_dwell: NotRequired[bool]
    is_bridge: NotRequired[bool]      # fibre spanning a gap between islands
    is_anchor: NotRequired[bool]      # lead-in anchor
    comment: NotRequired[str]         # G-code comment (calibration coupons)
    # DIW points:
    feature: NotRequired[str]         # dielectric | conductor | crossover | encapsulation | travel
    extruding: NotRequired[bool]
    width: NotRequired[float]
    height: NotRequired[float]


REQUIRED = ("x", "y", "z", "dist", "v")
KNOWN = set(TrajectoryPoint.__annotations__)
_NUMERIC = {"x", "y", "z", "dist", "v", "e_mult", "z_layer", "width", "height"}
_BOOL = {"layer_start", "is_transition", "pump_floored", "cure_dwell", "is_bridge", "is_anchor", "extruding"}
DIW_FEATURES = {"dielectric", "conductor", "crossover", "encapsulation", "travel"}


def check_trajectory(points, limit=20) -> list[str]:
    """Problems with a trajectory (empty list = fine): missing required keys,
    unknown (misspelled) keys, non-finite numbers, wrong types."""
    problems = []
    for i, p in enumerate(points):
        if len(problems) >= limit:
            problems.append("… (more)")
            break
        missing = [k for k in REQUIRED if k not in p]
        if missing:
            problems.append(f"point {i}: missing {', '.join(missing)}")
        unknown = sorted(set(p) - KNOWN)
        if unknown:
            problems.append(f"point {i}: unknown key(s) {', '.join(unknown)}")
        for k in _NUMERIC & set(p):
            v = p[k]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                problems.append(f"point {i}: {k}={v!r} is not a finite number")
        for k in _BOOL & set(p):
            if not isinstance(p[k], (bool, int)):
                problems.append(f"point {i}: {k}={p[k]!r} is not a flag")
        if "layer" in p and (isinstance(p["layer"], bool) or not isinstance(p["layer"], int)):
            problems.append(f"point {i}: layer={p['layer']!r} is not an integer")
        if "feature" in p and p["feature"] not in DIW_FEATURES:
            problems.append(f"point {i}: feature={p['feature']!r} is not one of {sorted(DIW_FEATURES)}")
    return problems
