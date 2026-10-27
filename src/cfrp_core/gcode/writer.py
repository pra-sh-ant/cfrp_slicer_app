"""G-code writer (spec §12). Marlin-style output with the custom UV command
`M1995 P47 S{uv}`; dialects arrive in Phase 5. Speeds are mm/s in the core
and converted to mm/min (F) only here.

Arc detection (G2/G3): consecutive coplanar points that fit a common circle
within ARC_TOLERANCE are emitted as a single arc command, reducing file size
by 5-20x for circular paths. An arc turns one way, less than half a turn (so
its end alone fixes it: an arc ending where it starts is a full circle), and
its centre sits at one radius from its start and its end (firmware that
checks the two -- GRBL-style -- refuses arcs off by more than ~0.005 mm)."""
from __future__ import annotations

import math

from ..config import ProcessConfig

ARC_TOLERANCE = 0.02
MIN_ARC_POINTS = 4
MAX_ARC_SWEEP = math.pi


def _circumcircle(ax, ay, bx, by, cx, cy):
    """Center and radius of the circle through three 2D points. Returns None if collinear."""
    D = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(D) < 1e-12:
        return None
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / D
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / D
    r = math.hypot(ax - ux, ay - uy)
    return ux, uy, r


def _arc_direction(pts, cx, cy):
    """'G2' (clockwise) or 'G3' (anticlockwise): the way the points turn
    round the arc's centre (cx, cy). (The open chain's shoelace sum measured
    the turn round the machine origin instead, and wrote ~40 % of arcs the
    wrong way: firmware and G-code viewers then went the long way round.)"""
    turn = 0.0
    for (ax, ay), (bx, by) in zip(pts[:-1], pts[1:], strict=True):
        turn += (ax - cx) * (by - cy) - (ay - cy) * (bx - cx)
    return "G2" if turn < 0 else "G3"


def _centre_through(ax, ay, bx, by, cx, cy):
    """The point on the perpendicular bisector of a-b nearest (cx, cy): a
    centre at one radius from both ends."""
    mx, my = 0.5 * (ax + bx), 0.5 * (ay + by)
    dx, dy = bx - ax, by - ay
    length = math.hypot(dx, dy)
    if length < 1e-12:
        return cx, cy
    nx, ny = -dy / length, dx / length
    t = (cx - mx) * nx + (cy - my) * ny
    return mx + t * nx, my + t * ny


def _fits(pts, cx, cy):
    """All of pts within ARC_TOLERANCE of the circle about (cx, cy) through pts[0]."""
    r = math.hypot(pts[0][0] - cx, pts[0][1] - cy)
    return all(abs(math.hypot(x - cx, y - cy) - r) <= ARC_TOLERANCE for x, y in pts)


def _steps(trajectory):
    """Length of the move INTO each point (from the one before), from the
    coordinates. A point's `dist` is not that: the speed planner stores the
    step to the NEXT point (kinematics: d_i = |p_{i+1} - p_i|), the Mushroom
    and the ink paths the step from the last. Read as the move into the
    point, the planner's value gave each move the next one's resin (a long
    move starved, the short one after it a blob) and dropped every move whose
    NEXT point sat on top of it -- an arc after one then started from where
    the head was not."""
    out = [0.0]
    for a, b in zip(trajectory[:-1], trajectory[1:], strict=True):
        out.append(math.dist((a['x'], a['y'], a['z']), (b['x'], b['y'], b['z'])))
    return out


def _own_line(pt, step):
    """A point that never joins an arc: a layer climb or start, a comment, a
    hold point (its dwell / pause follows its move), or no move into it."""
    return bool(pt.get('is_transition') or pt.get('layer_start') or pt.get('comment') or pt.get('hold_s')
                or pt.get('hold_cut') or pt.get('clean_s') or step <= 1e-9)


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
    steps = _steps(trajectory)
    i = 1

    while i < n:
        pt = trajectory[i]
        if _own_line(pt, steps[i]):
            result.append(('line', i))
            i += 1
            continue

        j = i
        while j < n and not _own_line(trajectory[j], steps[j]):
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
            turn = 0.0
            px, py = prev['x'] - cx, prev['y'] - cy
            for k in range(arc_start, j):
                qx, qy = trajectory[k]['x'] - cx, trajectory[k]['y'] - cy
                dev = abs(math.hypot(qx, qy) - r)
                z_dev = abs(trajectory[k]['z'] - trajectory[arc_start]['z'])
                step = math.atan2(px * qy - py * qx, px * qx + py * qy)
                if dev > ARC_TOLERANCE or z_dev > 0.01 or step * turn < 0 or abs(turn + step) >= MAX_ARC_SWEEP:
                    break
                turn += step
                px, py = qx, qy
                arc_end = k

            # the centre at one radius from both ends, the arc shortened until
            # its points still fit that circle
            first = arc_start - 1 if arc_start > 0 else arc_start
            while arc_end - arc_start + 1 >= MIN_ARC_POINTS:
                pts_2d = [(trajectory[k]['x'], trajectory[k]['y']) for k in range(first, arc_end + 1)]
                ex, ey = _centre_through(*pts_2d[0], *pts_2d[-1], cx, cy)
                if _fits(pts_2d, ex, ey):
                    break
                arc_end -= 1

            arc_len = arc_end - arc_start + 1
            if arc_len >= MIN_ARC_POINTS:
                cmd = _arc_direction(pts_2d, ex, ey)
                result.append(('arc', arc_start, arc_end, ex, ey, cmd))
                arc_start = arc_end + 1
            else:
                result.append(('line', arc_start))
                arc_start += 1

        i = j

    return result


def compile_gcode(trajectory, config: ProcessConfig) -> str:
    """Render a planned trajectory as G-code text ('\\n' line endings).

    E is cumulative:  E += step · e_const · e_mult_point, `step` the length
    of the move into the point (_steps). A point's optional
    'comment' is written as a G-code comment before its move (calibration
    coupons label their test bands this way). After a hold point's move
    (holds.py): `G4` for its `hold_s` (UV on, the tow cures in place) and,
    with `hold_cut`, UV off, `M0` for the operator to cut the fibre, UV back
    on and the start's tack dwell. After a cleaning pause's point
    (`clean_s`): UV off, `G4` for that long (the head stays put), UV back on
    and the tack dwell.
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
    steps = _steps(trajectory)

    for seg in segments:
        if seg[0] == 'line':
            pt = trajectory[seg[1]]

            if pt.get('layer_start', False):
                layer_count += 1
                gcode.append(f"\n; --- STARTING LAYER {layer_count} (Z={pt['z']:.3f}) ---")
            if pt.get('comment'):
                gcode.append(f"; {pt['comment']}")

            if steps[seg[1]] > 1e-9:
                current_e += steps[seg[1]] * e_const * pt['e_mult']
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
            if pt.get('hold_s'):
                gcode.append(f"G4 P{int(round(pt['hold_s'] * 1000))} ; HOLD {pt['hold_s']:g} s at Z={pt['z']:.3f}: "
                             f"the tow cures in place")
            if pt.get('hold_cut'):
                gcode.extend([
                    "M1995 P47 S0 ; UV OFF for the pause",
                    f"M0 Hold at Z={pt['z']:.3f}: cut the fibre here if needed, then resume ; PAUSE",
                    f"M1995 P47 S{gc.uv_pwm} ; UV ON",
                    "G4 P500 ; DWELL: Flash UV to tack",
                ])
            if pt.get('clean_s'):
                gcode.extend([
                    "M1995 P47 S0 ; UV OFF: clean the nozzle",
                    f"G4 P{int(round(pt['clean_s'] * 1000))} ; CLEANING PAUSE {pt['clean_s']:g} s at Z={pt['z']:.3f}: "
                    f"the head stays put",
                    f"M1995 P47 S{gc.uv_pwm} ; UV ON",
                    "G4 P500 ; DWELL: Flash UV to tack",
                ])

        elif seg[0] == 'arc':
            _, start_idx, end_idx, cx, cy, cmd = seg
            for k_idx in range(start_idx, end_idx + 1):
                current_e += steps[k_idx] * e_const * trajectory[k_idx]['e_mult']

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
