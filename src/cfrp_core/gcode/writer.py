"""G-code writer (spec §12). Marlin-style output with the custom UV command
`M1995 P47 S{uv}`; dialects arrive in Phase 5. Speeds are mm/s in the core
and converted to mm/min (F) only here.

Arc detection (G2/G3): consecutive coplanar points that fit a common circle
within ARC_TOLERANCE are emitted as a single arc command, reducing file size
by 5-20x for circular paths."""
from __future__ import annotations

import math

from ..config import ProcessConfig

ARC_TOLERANCE = 0.02
MIN_ARC_POINTS = 4


def _circumcircle(ax, ay, bx, by, cx, cy):
    """Center and radius of the circle through three 2D points. Returns None if collinear."""
    D = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(D) < 1e-12:
        return None
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / D
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / D
    r = math.hypot(ax - ux, ay - uy)
    return ux, uy, r


def _arc_direction(pts):
    """Returns 'G2' for CW, 'G3' for CCW based on signed area of the point sequence."""
    area = 0.0
    for i in range(len(pts) - 1):
        area += pts[i][0] * pts[i + 1][1] - pts[i + 1][0] * pts[i][1]
    return "G2" if area < 0 else "G3"


def _detect_arcs(trajectory):
    """Partition trajectory into linear segments and arc segments.

    Returns a list of entries. Each entry is either:
      ('line', pt_index)  — emit as G1
      ('arc', start_idx, end_idx, cx, cy, cmd)  — emit as G2/G3
    """
    n = len(trajectory)
    if n < 2:
        return [('line', i) for i in range(n)]

    result = []
    i = 1

    while i < n:
        pt = trajectory[i]
        if pt.get('is_transition') or pt.get('layer_start') or pt.get('comment') or pt['dist'] <= 0:
            result.append(('line', i))
            i += 1
            continue

        j = i
        while (j < n and not trajectory[j].get('is_transition') and not trajectory[j].get('layer_start')
               and not trajectory[j].get('comment') and trajectory[j]['dist'] > 0):
            j += 1

        run_len = j - i
        if run_len < MIN_ARC_POINTS:
            for k in range(i, j):
                result.append(('line', k))
            i = j
            continue

        arc_start = i
        while arc_start < j:
            if arc_start + MIN_ARC_POINTS - 1 >= j:
                for k in range(arc_start, j):
                    result.append(('line', k))
                arc_start = j
                break

            prev = trajectory[arc_start - 1] if arc_start > 0 else trajectory[arc_start]
            mid_idx = arc_start + (min(j, arc_start + MIN_ARC_POINTS) - arc_start) // 2
            cc = _circumcircle(
                prev['x'], prev['y'],
                trajectory[mid_idx]['x'], trajectory[mid_idx]['y'],
                trajectory[min(j - 1, arc_start + MIN_ARC_POINTS - 1)]['x'],
                trajectory[min(j - 1, arc_start + MIN_ARC_POINTS - 1)]['y'],
            )
            if cc is None:
                result.append(('line', arc_start))
                arc_start += 1
                continue

            cx, cy, r = cc
            if r > 500 or r < 0.5:
                result.append(('line', arc_start))
                arc_start += 1
                continue

            arc_end = arc_start
            for k in range(arc_start, j):
                dev = abs(math.hypot(trajectory[k]['x'] - cx, trajectory[k]['y'] - cy) - r)
                z_dev = abs(trajectory[k]['z'] - trajectory[arc_start]['z'])
                if dev > ARC_TOLERANCE or z_dev > 0.01:
                    break
                arc_end = k

            arc_len = arc_end - arc_start + 1
            if arc_len >= MIN_ARC_POINTS:
                pts_2d = [(trajectory[k]['x'], trajectory[k]['y']) for k in range(arc_start - 1 if arc_start > 0 else arc_start, arc_end + 1)]
                cmd = _arc_direction(pts_2d)
                result.append(('arc', arc_start, arc_end, cx, cy, cmd))
                arc_start = arc_end + 1
            else:
                result.append(('line', arc_start))
                arc_start += 1

        i = j

    return result


def compile_gcode(trajectory, config: ProcessConfig) -> str:
    """Render a planned trajectory as G-code text ('\\n' line endings).

    E is cumulative:  E += dist · e_const · e_mult_point. A point's optional
    'comment' is written as a G-code comment before its move (calibration
    coupons label their test bands this way).
    """
    if not trajectory:
        return "; Trajectory failed to generate."

    g, k, gc = config.geometry, config.kinematics, config.gcode
    e_const = config.e_const
    z_off = config.z_offset_mm
    start_pt = trajectory[0]

    gcode = [
        "; ==========================================",
        ";        CONTINUOUS FIBER SLICER PRO        ",
        ";        (Seamless Validation Engine)       ",
        ";              VERSION v5.0                 ",
        "; ==========================================",
        f"; Target Geometry : {g.shape}",
        f"; Total Layers    : {config.layers}",
        f"; Infill Type     : {config.infill_type}",
        f"; Layup Sequence  : {config.layup_label}",
        f"; Track Gap       : {config.track_gap_mm} mm",
        f"; Min Bend Radius : {config.min_radius_mm} mm",
        f"; Layer Height    : {config.layer_height_mm} mm",
        f"; Anchor Tail Len : {config.anchor_length_mm} mm",
        f"; Calc E-Constant : {e_const:.5f}",
        f"; Pump-Stall Floor: {k.pump_stall_floor_mm_s:.2f} mm/s-equiv"
        + (
            "  [!! UNMEASURED -- equals Min Bend Speed, no real floor enforced yet !!]"
            if k.pump_floor_unmeasured
            else ""
        ),
        "",
        "; --- 1. INITIALIZATION ---",
        "G28 ; Home all axes",
        "G92 E0 ; Zero extruder",
        "G90 ; Absolute positioning",
        "G0 F1200 Z15.000 ; Safe Z lift",
        "",
        "; --- 2. PRE-PRINT RITUAL ---",
        f"G1 F{gc.purge_f_mm_min:.0f} E{gc.purge_e:.3f} ; SLOW PRIME",
        "G4 P1500 ; DWELL: Stabilize droplet",
        "G92 E0 ; RESET EXTRUDER",
        "",
        "; --- 3. APPROACH & LOCK ---",
        f"G0 F{gc.travel_f_mm_min:.0f} X{start_pt['x']:.3f} Y{start_pt['y']:.3f} ; SLOW APPROACH",
        f"M1995 P47 S{gc.uv_pwm} ; UV ON",
        f"G1 F300 Z{z_off:.3f} ; PLUNGE TO SOD",
        "G4 P500 ; DWELL: Flash UV to tack",
        ""
    ]

    current_e = 0.0
    layer_count = 0

    segments = _detect_arcs(trajectory)

    for seg in segments:
        if seg[0] == 'line':
            pt = trajectory[seg[1]]

            if pt.get('layer_start', False):
                layer_count += 1
                gcode.append(f"\n; --- STARTING LAYER {layer_count} (Z={pt['z']:.3f}) ---")
            if pt.get('comment'):
                gcode.append(f"; {pt['comment']}")

            if pt['dist'] > 0:
                current_e += pt['dist'] * e_const * pt['e_mult']
                v_f = pt['v'] * 60

                if pt.get('is_transition', False):
                    gcode.append(
                        f"G1 F{v_f:.0f} X{pt['x']:.3f} Y{pt['y']:.3f} Z{pt['z']:.3f} E{current_e:.4f} ; STRICT Z-SEAM LIFT"
                    )
                else:
                    gcode.append(
                        f"G1 F{v_f:.0f} X{pt['x']:.3f} Y{pt['y']:.3f} Z{pt['z']:.3f} E{current_e:.4f}"
                    )

            if pt.get('cure_dwell', False):
                gcode.append(f"G4 P{int(k.dwell_s * 1000)} ; === UV DWELL TO CURE STEM ===")

        elif seg[0] == 'arc':
            _, start_idx, end_idx, cx, cy, cmd = seg
            for k_idx in range(start_idx, end_idx + 1):
                pt = trajectory[k_idx]
                current_e += pt['dist'] * e_const * pt['e_mult']

            end_pt = trajectory[end_idx]
            start_pt_arc = trajectory[start_idx - 1] if start_idx > 0 else trajectory[start_idx]
            i_off = cx - start_pt_arc['x']
            j_off = cy - start_pt_arc['y']
            v_f = trajectory[start_idx]['v'] * 60

            gcode.append(
                f"{cmd} F{v_f:.0f} X{end_pt['x']:.3f} Y{end_pt['y']:.3f} Z{end_pt['z']:.3f}"
                f" I{i_off:.3f} J{j_off:.3f} E{current_e:.4f}"
            )

    gcode.extend([
        "",
        "; --- 5. END SEQUENCE ---",
        "M1995 P47 S0 ; UV OFF",
        f"G1 F1200 Z{trajectory[-1]['z'] + 15.0:.3f} ; Safe lift",
        "G0 X0 Y0 F3000 ; Park gantry",
        "M84 X Y Z E ; Disable steppers"
    ])

    return "\n".join(gcode)
