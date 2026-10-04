"""Fixed-speed specialty generators (spec §11): the Mushroom overhang test,
Torus and Conical Dome. No speed planner and no layer ramps.

Phase 10 decides the fate of Torus and Conical Dome (see ARCHITECTURE.md,
known issues); until then they are kept behaviour-identical.
"""
from __future__ import annotations

import copy
import math
from functools import lru_cache

import numpy as np

from .config import ProcessConfig


def _add_pt(trajectory, x, y, z, v, e_m, cure_dwell=False):
    dist = math.hypot(
        x - trajectory[-1]['x'], y - trajectory[-1]['y'], z - trajectory[-1]['z']
    ) if trajectory else 0.0
    trajectory.append({
        "x": float(x), "y": float(y), "z": float(z),
        "dist": float(dist), "v": float(v), "e_mult": float(e_m),
        "layer_start": False, "layer": 1, "cure_dwell": cure_dwell
    })


def overhang_test(config: ProcessConfig) -> list[dict]:
    """Mushroom: an inward base spiral, a vertical stem with a UV cure dwell
    at the top, then an outward overhanging top-disc spiral.

        base:  r(t) = base_r − gap·t/(2π),  t ∈ (0, 2π(base_r − r_min)/gap]
        top:   r(t) = r_min + gap·t/(2π)
        36 steps per turn (at least 1). The spiral starts at the angle that
        points toward the machine origin, θ0 = atan2(−cy, −cx).
        Stem top is the ABSOLUTE height `stem_height` (not offset by Z-offset).
    """
    g, k = config.geometry, config.kinematics
    cx, cy = g.start_x_mm, g.start_y_mm
    base_r, top_r = g.base_radius_mm, g.disc_radius_mm
    gap, h = config.track_gap_mm, g.stem_height_mm
    v_base, v_z, v_overhang = k.v_max_mm_s, k.v_z_mm_s, k.v_overhang_mm_s
    z_offset, min_rad = config.z_offset_mm, config.min_radius_mm

    trajectory = []

    theta_0 = math.atan2(-cy, -cx)

    if base_r > min_rad:
        spiral_dist = base_r - min_rad
        max_t = (spiral_dist / gap) * 2 * math.pi
        # At least one step: a base just barely wider than min_rad gave
        # steps=0 and a ZeroDivisionError in the linspace start below.
        steps = max(1, int((spiral_dist / gap) * 36))
        start_x = cx + base_r * math.cos(theta_0)
        start_y = cy + base_r * math.sin(theta_0)
        _add_pt(trajectory, start_x, start_y, z_offset, v_base, 1.0)

        t_array = np.linspace(max_t / steps, max_t, steps)
        r_array = base_r - (gap * (t_array / (2 * math.pi)))
        theta_array = theta_0 + t_array
        x_array = cx + r_array * np.cos(theta_array)
        y_array = cy + r_array * np.sin(theta_array)

        for i in range(steps):
            _add_pt(trajectory, x_array[i], y_array[i], z_offset, v_base, 1.0)
        current_theta = theta_array[-1]
    else:
        _add_pt(trajectory, cx + base_r * math.cos(theta_0), cy + base_r * math.sin(theta_0),
                z_offset, v_base, 1.0)
        current_theta = theta_0

    _add_pt(trajectory, cx, cy, z_offset, v_z, 1.0)
    _add_pt(trajectory, cx, cy, h, v_z, 1.0, cure_dwell=True)

    if top_r > min_rad:
        spiral_dist = top_r - min_rad
        max_t_top = (spiral_dist / gap) * 2 * math.pi
        steps_top = max(1, int((spiral_dist / gap) * 36))
        t_top_array = np.linspace(max_t_top / steps_top, max_t_top, steps_top)
        r_top_array = min_rad + (gap * (t_top_array / (2 * math.pi)))
        theta_top_array = current_theta + t_top_array
        x_top_array = cx + r_top_array * np.cos(theta_top_array)
        y_top_array = cy + r_top_array * np.sin(theta_top_array)
        v_mask = r_top_array < (min_rad + 2 * gap)

        for i in range(steps_top):
            current_v = v_z if v_mask[i] else v_overhang
            _add_pt(trajectory, x_top_array[i], y_top_array[i], h, current_v, 1.0)

    return trajectory


@lru_cache(maxsize=64)
def _specialty_shape(config: ProcessConfig) -> list[dict]:
    g, k = config.geometry, config.kinematics
    shape = g.shape
    if shape == '3D Overhang (Mushroom)':
        return overhang_test(config)

    cx, cy = float(g.start_x_mm), float(g.start_y_mm)
    z0 = float(config.z_offset_mm)
    height = float(g.stem_height_mm)
    wall_t = float(g.wall_thickness_mm)
    gap = max(float(config.track_gap_mm), 0.5)
    v_z = float(k.v_z_mm_s)
    trajectory = []

    if shape == 'Torus (Z)':
        # Decorative looping curve (the 1.7 factor), not a geometric torus.
        major_r = max(float(g.base_radius_mm), 8.0)
        minor_r = max(float(wall_t), 2.0)
        ring_count = max(10, int(height / max(gap, 1.0)) + 1)
        for i in range(ring_count):
            z_level = z0 + (height * (i / ring_count))
            for a in np.linspace(0.0, 2.0 * math.pi, 40, endpoint=False):
                x = cx + (major_r + minor_r * math.cos(a)) * math.cos(a * 1.7)
                y = cy + (major_r + minor_r * math.cos(a)) * math.sin(a * 1.7)
                _add_pt(trajectory, x, y, z_level, v_z, 1.0)
    elif shape == 'Conical Dome (Z)':
        # Stacked circles r = R(1 − t); steps vertically between rings.
        radius = max(float(g.base_radius_mm), 6.0)
        heights = max(4, int(height / max(gap, 1.0)) + 1)
        for i in range(heights):
            t = i / max(1, heights - 1)
            z_level = z0 + height * t
            current_r = radius * (1.0 - t)
            theta = np.linspace(0.0, 2.0 * math.pi, 40, endpoint=False)
            for a in theta:
                x = cx + current_r * math.cos(a)
                y = cy + current_r * math.sin(a)
                _add_pt(trajectory, x, y, z_level, v_z, 1.0)
    else:
        raise ValueError(f"No specialty generator for shape {shape!r}.")

    return trajectory


def specialty_shape(config: ProcessConfig) -> list[dict]:
    """Trajectory for a flat-speed specialty shape (cached per config; the
    caller gets its own copy, safe to mutate)."""
    return copy.deepcopy(_specialty_shape(config))
