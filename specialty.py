"""Specialty 3D research geometry: mushroom overhangs and shell-based Z-prints."""
import math
import numpy as np
import streamlit as st


class SpecialtyEngine:
    @staticmethod
    def _add_pt(trajectory, x, y, z, v, e_m, cure_dwell=False):
        dist = math.hypot(
            x - trajectory[-1]['x'], y - trajectory[-1]['y'], z - trajectory[-1]['z']
        ) if trajectory else 0.0
        trajectory.append({
            "x": float(x), "y": float(y), "z": float(z),
            "dist": float(dist), "v": float(v), "e_mult": float(e_m),
            "layer_start": False, "layer": 1, "cure_dwell": cure_dwell
        })

    @staticmethod
    def generate_overhang_test(config):
        cx, cy = config['start_x'], config['start_y']
        base_r, top_r = config['base_radius'], config['disc_radius']
        gap, h = config['track_gap'], config['stem_height']
        v_base, v_z, v_overhang = config['v_max'], config['v_z'], config['v_overhang']
        z_offset, min_rad = config['z_offset'], config['min_radius']

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
            SpecialtyEngine._add_pt(trajectory, start_x, start_y, z_offset, v_base, 1.0)

            t_array = np.linspace(max_t / steps, max_t, steps)
            r_array = base_r - (gap * (t_array / (2 * math.pi)))
            theta_array = theta_0 + t_array
            x_array = cx + r_array * np.cos(theta_array)
            y_array = cy + r_array * np.sin(theta_array)

            for i in range(steps):
                SpecialtyEngine._add_pt(trajectory, x_array[i], y_array[i], z_offset, v_base, 1.0)
            current_theta = theta_array[-1]
        else:
            SpecialtyEngine._add_pt(
                trajectory,
                cx + base_r * math.cos(theta_0),
                cy + base_r * math.sin(theta_0),
                z_offset,
                v_base,
                1.0
            )
            current_theta = theta_0

        SpecialtyEngine._add_pt(trajectory, cx, cy, z_offset, v_z, 1.0)
        SpecialtyEngine._add_pt(trajectory, cx, cy, h, v_z, 1.0, cure_dwell=True)

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
                SpecialtyEngine._add_pt(trajectory, x_top_array[i], y_top_array[i], h, current_v, 1.0)

        return trajectory

    @staticmethod
    @st.cache_data(show_spinner=False)
    def generate_specialty_shape(config):
        """Single cached entry point for every flat-speed specialty shape."""
        shape = config.get('shape', '3D Overhang (Mushroom)')
        if shape == '3D Overhang (Mushroom)':
            return SpecialtyEngine.generate_overhang_test(config)

        cx, cy = float(config.get('start_x', 0.0)), float(config.get('start_y', 0.0))
        z0 = float(config.get('z_offset', 0.0))
        height = float(config.get('stem_height', 25.0))
        wall_t = float(config.get('wall_thickness', 2.5))
        gap = max(float(config.get('track_gap', 1.2)), 0.5)
        v_print = float(config.get('v_max', 10.0))
        v_z = float(config.get('v_z', v_print))
        trajectory = []

        if shape == 'Torus (Z)':
            major_r = max(float(config.get('base_radius', 20.0)), 8.0)
            minor_r = max(float(wall_t), 2.0)
            ring_count = max(10, int(height / max(gap, 1.0)) + 1)
            for i in range(ring_count):
                z_level = z0 + (height * (i / ring_count))
                for a in np.linspace(0.0, 2.0 * math.pi, 40, endpoint=False):
                    x = cx + (major_r + minor_r * math.cos(a)) * math.cos(a * 1.7)
                    y = cy + (major_r + minor_r * math.cos(a)) * math.sin(a * 1.7)
                    SpecialtyEngine._add_pt(trajectory, x, y, z_level, v_z, 1.0)
        elif shape == 'Conical Dome (Z)':
            radius = max(float(config.get('base_radius', 18.0)), 6.0)
            heights = max(4, int(height / max(gap, 1.0)) + 1)
            for i in range(heights):
                t = i / max(1, heights - 1)
                z_level = z0 + height * t
                current_r = radius * (1.0 - t)
                theta = np.linspace(0.0, 2.0 * math.pi, 40, endpoint=False)
                for a in theta:
                    x = cx + current_r * math.cos(a)
                    y = cy + current_r * math.sin(a)
                    SpecialtyEngine._add_pt(trajectory, x, y, z_level, v_z, 1.0)
        else:
            raise ValueError(f"No specialty generator for shape {shape!r}.")

        return trajectory
