"""The vectorised speed planner must match the legacy per-point loops.

`_legacy_calculate_trajectory` is a verbatim copy of the pre-Phase-1
implementation (minus Streamlit caching and the then-unused branches).
"""
import math

import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st

from cfrp_core.kinematics import calculate_trajectory, estimate_arc_radius

TOL = 1e-9


def _legacy_arc_radius(p0, p1, p2):
    ax, ay = p1[0] - p0[0], p1[1] - p0[1]
    bx, by = p2[0] - p1[0], p2[1] - p1[1]
    cross = ax * by - ay * bx
    if abs(cross) < 1e-9:
        return 9999.0
    mag_a = math.hypot(ax, ay)
    mag_b = math.hypot(bx, by)
    if mag_a < 1e-9 or mag_b < 1e-9:
        return 9999.0
    mag_c = math.hypot(p2[0] - p0[0], p2[1] - p0[1])
    return (mag_a * mag_b * mag_c) / (2.0 * abs(cross))


def _legacy_calculate_trajectory(path, v_max, v_min, accel, decel, advance_dist,
                                 z_height=0.0, min_rad=3.0, pump_floor=None):
    if len(path) < 3:
        return []
    n = len(path)
    path_arr = np.array(path)
    diffs = np.diff(path_arr, axis=0)
    distances = np.linalg.norm(diffs, axis=1)
    distances = np.append(distances, 0.0)
    target_v = np.full(n, float(v_max))
    for i in range(1, n - 1):
        radius = _legacy_arc_radius(path[i - 1], path[i], path[i + 1])
        if radius < min_rad * 3:
            scale = max(0.0, (radius - min_rad) / (min_rad * 2.0))
            target_v[i] = min(target_v[i], v_min + (v_max - v_min) * scale)
        incoming = path_arr[i] - path_arr[i - 1]
        outgoing = path_arr[i + 1] - path_arr[i]
        incoming_norm = np.linalg.norm(incoming)
        outgoing_norm = np.linalg.norm(outgoing)
        if incoming_norm > 1e-9 and outgoing_norm > 1e-9:
            heading_cosine = float(np.dot(incoming, outgoing) / (incoming_norm * outgoing_norm))
            heading_cosine = max(-1.0, min(1.0, heading_cosine))
            momentum_scale = 0.5 + 0.5 * heading_cosine
            target_v[i] = min(target_v[i], v_min + (v_max - v_min) * max(0.0, momentum_scale))
    final_v = np.copy(target_v)
    for i in range(n - 2, -1, -1):
        final_v[i] = min(final_v[i], math.sqrt(max(0.0, final_v[i + 1]**2 + 2 * decel * distances[i])))
    for i in range(1, n):
        final_v[i] = min(final_v[i], math.sqrt(max(0.0, final_v[i - 1]**2 + 2 * accel * distances[i - 1])))
    if pump_floor is None:
        pump_floor = v_min
    pump_floor_fraction = pump_floor / v_max if v_max > 0 else 0.0
    raw_extrusion_mult = final_v / v_max
    extrusion_mult = np.maximum(raw_extrusion_mult, pump_floor_fraction)
    shifted_extrusion = np.zeros(n)
    for i in range(n):
        dist_accum, idx = 0.0, i
        while idx < n - 1 and dist_accum < advance_dist:
            dist_accum += distances[idx]
            idx += 1
        shifted_extrusion[i] = extrusion_mult[idx]
    pump_floored_flags = extrusion_mult > raw_extrusion_mult
    return [
        {
            "x": float(path_arr[i][0]), "y": float(path_arr[i][1]), "z": float(z_height),
            "dist": float(distances[i]), "v": float(final_v[i]),
            "e_mult": float(shifted_extrusion[i]),
            "pump_floored": bool(pump_floored_flags[i]),
            "layer_start": (i == 0), "is_transition": False,
        }
        for i in range(n)
    ]


def _assert_same(new, old):
    assert len(new) == len(old)
    for a, b in zip(new, old, strict=True):
        assert a.keys() == b.keys()
        for key in ("x", "y", "z", "dist", "v", "e_mult"):
            assert abs(a[key] - b[key]) <= TOL, key
        for key in ("pump_floored", "layer_start", "is_transition"):
            assert a[key] == b[key], key


coord = st.floats(min_value=-200, max_value=200, allow_nan=False, allow_infinity=False)
# Grid-snapped coordinates make exact ties (collinear runs, repeated points,
# segment lengths summing exactly to the pressure-advance distance) common.
grid_coord = st.integers(min_value=-40, max_value=40).map(lambda k: k * 0.75)
paths = st.lists(st.tuples(coord, coord), min_size=0, max_size=60)
grid_paths = st.lists(st.tuples(grid_coord, grid_coord), min_size=3, max_size=60)
params = st.fixed_dictionaries({
    "v_max": st.floats(min_value=1.0, max_value=50.0),
    "v_min_frac": st.floats(min_value=0.0, max_value=1.0),
    "accel": st.floats(min_value=0.1, max_value=200.0),
    "decel": st.floats(min_value=0.1, max_value=200.0),
    "advance": st.sampled_from([0.0, 0.75, 1.5, 3.0]) | st.floats(min_value=0.0, max_value=10.0),
    "min_rad": st.sampled_from([0.0, 0.75, 3.0]) | st.floats(min_value=0.0, max_value=10.0),
    "floor_mode": st.sampled_from(["none", "equal_vmin", "custom"]),
    "floor_frac": st.floats(min_value=0.0, max_value=1.0),
})


def _run_both(path, p):
    v_min = p["v_max"] * p["v_min_frac"]
    floor = {"none": None, "equal_vmin": v_min, "custom": p["v_max"] * p["floor_frac"]}[p["floor_mode"]]
    args = (path, p["v_max"], v_min, p["accel"], p["decel"], p["advance"])
    kwargs = {"z_height": 1.5, "min_rad": p["min_rad"], "pump_floor": floor}
    return calculate_trajectory(*args, **kwargs), _legacy_calculate_trajectory(*args, **kwargs)


@given(paths, params)
@settings(max_examples=300, deadline=None)
def test_matches_legacy_on_random_paths(path, p):
    new, old = _run_both(path, p)
    _assert_same(new, old)


@given(grid_paths, params)
@settings(max_examples=300, deadline=None)
def test_matches_legacy_on_tie_heavy_grid_paths(path, p):
    new, old = _run_both(path, p)
    _assert_same(new, old)


def test_result_is_a_fresh_copy_each_call():
    """Callers mutate the returned dicts (layer tags); the cache must not
    hand out shared objects."""
    path = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    first = calculate_trajectory(path, 10, 5, 25, 15, 3)
    first[0]["layer"] = 99
    first[0]["x"] = -1.0
    second = calculate_trajectory(path, 10, 5, 25, 15, 3)
    assert "layer" not in second[0] and second[0]["x"] == 0.0


def test_estimate_arc_radius_matches_circle():
    r = 7.5
    pts = [(r * math.cos(a), r * math.sin(a)) for a in (0.0, 0.3, 0.6)]
    assert math.isclose(estimate_arc_radius(*pts), r, rel_tol=1e-12)
    assert estimate_arc_radius((0, 0), (1, 0), (2, 0)) == 9999.0
