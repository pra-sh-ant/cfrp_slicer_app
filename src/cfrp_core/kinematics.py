"""Speed planning (spec §6): corner-speed limits, accel/decel-limited
ramps, pump-floor extrusion and pressure-advance look-ahead.

Corner/heading limits, extrusion and the pressure-advance search are
vectorised with numpy; the accel/decel passes are exact sequential scans
(see `_limit_accel`). tests/test_kinematics.py compares against a copy of
the legacy per-point implementation.
"""
from __future__ import annotations

import math
from functools import lru_cache

import numpy as np

_COLLINEAR_RADIUS = 9999.0


def estimate_arc_radius(p0, p1, p2) -> float:
    """Radius of the circle through three points (circumradius):
        R = |a|·|b|·|c| / (2·|a × b|),  a = p1 − p0, b = p2 − p1, c = p2 − p0.
    Returns 9999 for collinear or degenerate input."""
    radii = _arc_radii(np.asarray([p0, p1, p2], dtype=np.float64))
    return float(radii[0])


def _arc_radii(path: np.ndarray) -> np.ndarray:
    """Circumradius at every interior vertex of an (n, 2) path -> (n-2,)."""
    p0, p1, p2 = path[:-2], path[1:-1], path[2:]
    ax, ay = p1[:, 0] - p0[:, 0], p1[:, 1] - p0[:, 1]
    bx, by = p2[:, 0] - p1[:, 0], p2[:, 1] - p1[:, 1]
    cross = ax * by - ay * bx
    mag_a = np.hypot(ax, ay)
    mag_b = np.hypot(bx, by)
    mag_c = np.hypot(p2[:, 0] - p0[:, 0], p2[:, 1] - p0[:, 1])
    degenerate = (np.abs(cross) < 1e-9) | (mag_a < 1e-9) | (mag_b < 1e-9)
    with np.errstate(divide="ignore", invalid="ignore"):
        radius = (mag_a * mag_b * mag_c) / (2.0 * np.abs(cross))
    return np.where(degenerate, _COLLINEAR_RADIUS, radius)


def _limit_accel(target_v: np.ndarray, distances: np.ndarray, accel: float, decel: float) -> np.ndarray:
    """Apply v² = v₀² + 2·a·s limits:
        backward  v_i = min(v_i, √(v_{i+1}² + 2·decel·d_i))   (brake in time)
        forward   v_i = min(v_i, √(v_{i−1}² + 2·accel·d_{i−1})) (accelerate no faster)

    These are sequential scans and are kept as exact loops over Python
    floats on purpose. An equivalent closed form (cumulative minimum of
    v² ± 2·a·S) is vectorisable but rounds differently by ~1 ulp; at speeds
    exactly equal to v_min that flips the pump-floor comparison
    (raw = v/v_max vs floor/v_max), changing validation results.
    """
    v = target_v.tolist()
    d = distances.tolist()
    sqrt = math.sqrt
    n = len(v)
    two_decel = 2 * decel
    for i in range(n - 2, -1, -1):
        cand = sqrt(max(0.0, v[i + 1] ** 2 + two_decel * d[i]))
        if cand < v[i]:
            v[i] = cand
    two_accel = 2 * accel
    for i in range(1, n):
        cand = sqrt(max(0.0, v[i - 1] ** 2 + two_accel * d[i - 1]))
        if cand < v[i]:
            v[i] = cand
    return np.array(v, dtype=np.float64)


def _advance_indices(distances: np.ndarray, advance_dist: float) -> np.ndarray:
    """For each i, the first index j ≥ i whose accumulated travel from i
    reaches `advance_dist` (or n−1). Legacy loop:
        acc = 0; j = i
        while j < n−1 and acc < advance_dist: acc += d[j]; j += 1
    The vectorised search uses prefix sums; near-exact ties (where prefix
    differences and the loop's running sum could round differently) are
    re-resolved with the loop itself so results are identical.
    """
    n = len(distances)
    s = np.concatenate(([0.0], np.cumsum(distances[:-1])))
    idx = np.searchsorted(s, s + advance_dist, side="left")
    idx = np.clip(np.maximum(idx, np.arange(n)), 0, n - 1)

    reach = s[idx] - s
    prev = s[np.maximum(idx - 1, 0)] - s
    tie = (np.abs(reach - advance_dist) < 1e-9) | (np.abs(prev - advance_dist) < 1e-9)
    for i in np.flatnonzero(tie):
        acc, j = 0.0, int(i)
        while j < n - 1 and acc < advance_dist:
            acc += distances[j]
            j += 1
        idx[i] = j
    return idx


@lru_cache(maxsize=512)
def _plan(path_bytes: bytes, n: int, v_max, v_min, accel, decel, advance_dist, min_rad, pump_floor):
    path = np.frombuffer(path_bytes, dtype=np.float64).reshape(n, 2)

    distances = np.append(np.linalg.norm(np.diff(path, axis=0), axis=1), 0.0)
    target_v = np.full(n, float(v_max))

    # Corner radius limit: speed falls linearly from v_max at R = 3·r_min to
    # v_min at R = r_min.
    radius = _arc_radii(path)
    tight = radius < min_rad * 3
    if np.any(tight):
        scale = np.maximum(0.0, (radius[tight] - min_rad) / (min_rad * 2.0))
        inner = target_v[1:-1]
        inner[tight] = np.minimum(inner[tight], v_min + (v_max - v_min) * scale)

    # Heading limit: 0.5 + 0.5·cosθ -> v_max straight, ½ at 90°, v_min reversing.
    incoming = path[1:-1] - path[:-2]
    outgoing = path[2:] - path[1:-1]
    in_norm = np.linalg.norm(incoming, axis=1)
    out_norm = np.linalg.norm(outgoing, axis=1)
    ok = (in_norm > 1e-9) & (out_norm > 1e-9)
    if np.any(ok):
        dot = incoming[ok, 0] * outgoing[ok, 0] + incoming[ok, 1] * outgoing[ok, 1]
        cosine = np.clip(dot / (in_norm[ok] * out_norm[ok]), -1.0, 1.0)
        momentum = 0.5 + 0.5 * cosine
        inner = target_v[1:-1]
        inner[ok] = np.minimum(inner[ok], v_min + (v_max - v_min) * np.maximum(0.0, momentum))

    final_v = _limit_accel(target_v, distances, accel, decel)

    # v_min governs geometric corner slowing only; pump_floor is the
    # separate, physically-measured stall threshold (UNMEASURED until the
    # pump step-response test: it then defaults to v_min, i.e. no real floor).
    if pump_floor is None:
        pump_floor = v_min
    pump_floor_fraction = pump_floor / v_max if v_max > 0 else 0.0
    raw_extrusion = final_v / v_max
    extrusion = np.maximum(raw_extrusion, pump_floor_fraction)
    shifted = extrusion[_advance_indices(distances, advance_dist)]
    floored = extrusion > raw_extrusion

    for arr in (distances, final_v, shifted, floored):
        arr.flags.writeable = False
    return distances, final_v, shifted, floored


def calculate_trajectory(path, v_max, v_min, accel, decel, advance_dist,
                         z_height=0.0, min_rad=3.0, pump_floor=None) -> list[dict]:
    """Plan speed (mm/s) and extrusion factor along an already fiber-safe 2D
    path. Corner rounding is the geometry generators' job; this only limits
    speed through whatever corners remain. Returns one dict per point."""
    if len(path) < 3:
        return []
    arr = np.ascontiguousarray(np.asarray(path, dtype=np.float64).reshape(-1, 2))
    n = len(arr)
    distances, final_v, shifted, floored = _plan(
        arr.tobytes(), n, float(v_max), float(v_min), float(accel), float(decel),
        float(advance_dist), float(min_rad), None if pump_floor is None else float(pump_floor),
    )
    xs, ys = arr[:, 0].tolist(), arr[:, 1].tolist()
    ds, vs, es, fs = distances.tolist(), final_v.tolist(), shifted.tolist(), floored.tolist()
    z = float(z_height)
    return [
        {
            "x": xs[i], "y": ys[i], "z": z,
            "dist": ds[i], "v": vs[i],
            "e_mult": es[i],
            "pump_floored": fs[i],
            "layer_start": (i == 0), "is_transition": False,
        }
        for i in range(n)
    ]
