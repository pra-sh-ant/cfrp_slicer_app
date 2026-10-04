"""Compiles a planned trajectory into machine G-code."""
from validation import pump_floor_unmeasured


class GCodeCompiler:
    @staticmethod
    def compile(trajectory, config):
        if not trajectory:
            return "; Trajectory failed to generate."

        z_off = config.get('z_offset', 0.0)
        start_pt = trajectory[0]

        gcode = [
            "; ==========================================",
            ";        CONTINUOUS FIBER SLICER PRO        ",
            ";        (Seamless Validation Engine)       ",
            ";              VERSION v5.0                 ",
            "; ==========================================",
            f"; Target Geometry : {config['shape']}",
            f"; Total Layers    : {config.get('layers', 1)}",
            f"; Infill Type     : {config.get('infill_type', 'N/A')}",
            f"; Layup Sequence  : {config.get('layup_seq', '0')}",
            f"; Track Gap       : {config['track_gap']} mm",
            f"; Min Bend Radius : {config['min_radius']} mm",
            f"; Layer Height    : {config['layer_height']} mm",
            f"; Anchor Tail Len : {config.get('anchor_length', 0)} mm",
            f"; Calc E-Constant : {config['e_const']:.5f}",
            f"; Pump-Stall Floor: {config.get('pump_stall_floor', 0):.2f} mm/s-equiv"
            + (
                "  [!! UNMEASURED -- equals Min Bend Speed, no real floor enforced yet !!]"
                if pump_floor_unmeasured(config)
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
            f"G1 F{config['purge_f']:.0f} E{config['purge_e']:.3f} ; SLOW PRIME",
            "G4 P1500 ; DWELL: Stabilize droplet",
            "G92 E0 ; RESET EXTRUDER",
            "",
            "; --- 3. APPROACH & LOCK ---",
            f"G0 F{config['travel_f']:.0f} X{start_pt['x']:.3f} Y{start_pt['y']:.3f} ; SLOW APPROACH",
            f"M1995 P47 S{config['uv']} ; UV ON",
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
                current_e += pt['dist'] * config['e_const'] * pt['e_mult']
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
                gcode.append(f"G4 P{int(config.get('dwell', 0.0) * 1000)} ; === UV DWELL TO CURE STEM ===")

        gcode.extend([
            "",
            "; --- 5. END SEQUENCE ---",
            "M1995 P47 S0 ; UV OFF",
            f"G1 F1200 Z{trajectory[-1]['z'] + 15.0:.3f} ; Safe lift",
            "G0 X0 Y0 F3000 ; Park gantry",
            "M84 X Y Z E ; Disable steppers"
        ])

        return "\n".join(gcode)
