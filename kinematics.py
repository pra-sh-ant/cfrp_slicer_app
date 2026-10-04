"""Speed planning: cornering-speed limits, jerk/accel-limited ramps, and pump-floor extrusion."""
import math
import numpy as np
import streamlit as st


class KinematicsEngine:

    @staticmethod
    def estimate_arc_radius(p0, p1, p2):
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
        radius = (mag_a * mag_b * mag_c) / (2.0 * abs(cross))
        return radius

    @staticmethod
    @st.cache_data(show_spinner=False)
    def calculate_trajectory(path: tuple, v_max, v_min, accel, decel, advance_dist,
                             z_height=0.0, min_rad=3.0, pump_floor=None):
        """Plan speed and extrusion along an already fiber-safe 2D path.

        Corner rounding is the geometry generators' job (offset fillets,
        serpentine U-turns); this only limits speed through whatever corners
        remain, so it never alters the path's shape."""
        if len(path) < 3:
            return []

        n = len(path)
        path_arr = np.array(path)

        diffs = np.diff(path_arr, axis=0)
        distances = np.linalg.norm(diffs, axis=1)
        distances = np.append(distances, 0.0)

        target_v = np.full(n, float(v_max))

        for i in range(1, n - 1):
            radius = KinematicsEngine.estimate_arc_radius(path[i - 1], path[i], path[i + 1])
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
                # Momentum is preserved by allowing high speed on a straight run
                # and easing into reversals before the geometric corner arrives.
                momentum_scale = 0.5 + 0.5 * heading_cosine
                target_v[i] = min(
                    target_v[i],
                    v_min + (v_max - v_min) * max(0.0, momentum_scale)
                )

        final_v = np.copy(target_v)

        for i in range(n - 2, -1, -1):
            final_v[i] = min(final_v[i], math.sqrt(max(0.0, final_v[i + 1]**2 + 2 * decel * distances[i])))
        for i in range(1, n):
            final_v[i] = min(final_v[i], math.sqrt(max(0.0, final_v[i - 1]**2 + 2 * accel * distances[i - 1])))

        # v_min (above) only governs how much the NOZZLE geometrically slows for tight
        # corners -- it is a path-smoothness parameter, not a physical constraint.
        #
        # pump_floor is a SEPARATE, physically-measured constraint: the minimum pump
        # flow (in the same mm/s-equivalent units as v_min) below which shear stress
        # on the fiber drops below the threshold needed to keep pulling it through the
        # nozzle (fiber stall / dry-fiber breakthrough / under-wetting at corners).
        # Until this is measured (Week 1-2 pump step-response test under back-pressure),
        # it defaults equal to v_min, which means: NO real floor is enforced yet, and
        # this code is not protecting you from corner stalls. Update pump_floor with
        # your measured value as soon as you have it.
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
                "layer_start": (i == 0), "is_transition": False
            }
            for i in range(n)
        ]
