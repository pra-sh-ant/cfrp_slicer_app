"""G-code writer (spec §12). Marlin-style output with the custom UV command
`M1995 P47 S{uv}`; dialects arrive in Phase 5. Speeds are mm/s in the core
and converted to mm/min (F) only here."""
from __future__ import annotations

from ..config import ProcessConfig


def compile_gcode(trajectory, config: ProcessConfig) -> str:
    """Render a planned trajectory as G-code text ('\\n' line endings).

    E is cumulative:  E += dist · e_const · e_mult_point.
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

    for pt in trajectory[1:]:
        if pt.get('layer_start', False):
            layer_count += 1
            gcode.append(f"\n; --- STARTING LAYER {layer_count} (Z={pt['z']:.3f}) ---")

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

        # Only the Mushroom overhang test marks cure-dwell points.
        if pt.get('cure_dwell', False):
            gcode.append(f"G4 P{int(k.dwell_s * 1000)} ; === UV DWELL TO CURE STEM ===")

    gcode.extend([
        "",
        "; --- 5. END SEQUENCE ---",
        "M1995 P47 S0 ; UV OFF",
        f"G1 F1200 Z{trajectory[-1]['z'] + 15.0:.3f} ; Safe lift",
        "G0 X0 Y0 F3000 ; Park gantry",
        "M84 X Y Z E ; Disable steppers"
    ])

    return "\n".join(gcode)
