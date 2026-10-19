"""DIW flow start/stop calibration coupon.

Six 25 mm lines, 4 mm apart, each printed with a different "close valve
early" distance (pneumatic) or retract amount (volumetric). After curing, the
line whose end is neither blobbed (too little) nor short (too much) gives the
value to enter in the ink settings.
"""
from __future__ import annotations

from .config import DiwConfig
from .gcode import compile_diw_gcode

PNEUMATIC_STEPS = (0.0, 0.3, 0.6, 1.0, 1.5, 2.0)        # stop_early_mm
VOLUMETRIC_STEPS = (0.0, 0.01, 0.02, 0.04, 0.06, 0.08)  # retract_e


def flow_coupon(cfg: DiwConfig, x0=20.0, y0=20.0, z=None) -> str:
    """G-code for the coupon (standalone program)."""
    z = cfg.layer_height_mm if z is None else z
    pneu = cfg.dispense == "pneumatic"
    steps = PNEUMATIC_STEPS if pneu else VOLUMETRIC_STEPS
    out = ["; ==========================================",
           "; DIW FLOW START/STOP COUPON",
           "; Lines from the front: " + ", ".join(
               (f"close early {v:g} mm" if pneu else f"retract {v:g}") for v in steps),
           "; Pick the line with the cleanest start and end and enter its value in",
           "; Ink settings > " + ("Close valve early" if pneu else "Retract at end") + ".",
           "; ==========================================",
           "G28 ; Home all axes", "G90"]
    for k, v in enumerate(steps):
        y = y0 + 4.0 * k
        traj = []
        for i, (x, zz, ext) in enumerate(((x0, z + cfg.z_hop_mm, False), (x0, z, False),
                                          (x0 + 25.0, z, True))):
            prev = traj[-1] if traj else None
            dist = 0.0 if prev is None else ((x - prev['x']) ** 2 + (zz - prev['z']) ** 2) ** 0.5
            traj.append({"x": x, "y": y, "z": zz, "dist": dist, "extruding": ext, "layer": 1,
                         "feature": "conductor" if ext else "travel",
                         "v": cfg.speed_conductor_mm_s if ext else cfg.travel_speed_mm_s})
            if i == 2:
                # split the line into 1 mm steps so the early close lands where asked
                n = 25
                traj.pop()
                for j in range(1, n + 1):
                    traj.append({"x": x0 + 25.0 * j / n, "y": y, "z": z, "dist": 25.0 / n, "extruding": True,
                                 "layer": 1, "feature": "conductor", "v": cfg.speed_conductor_mm_s})
        line_cfg = cfg.model_copy(update=dict(stop_early_mm=v) if pneu else dict(retract_e=v),)
        line_cfg = line_cfg.model_copy(update=dict(pause_before=False, tool_select="", uv_cure_between_layers=False))
        body = compile_diw_gcode(traj, line_cfg, f"line {k + 1}", standalone=False)
        keep = [ln for ln in body.splitlines()
                if not ln.startswith((";", "G90", "M84", "G0 X0 Y0")) and "Safe lift" not in ln]
        out.append(f"; --- line {k + 1}: " + (f"close early {v:g} mm" if pneu else f"retract {v:g}"))
        out += keep
    out += [f"G0 Z{z + 10:.3f} ; Safe lift", "G0 X0 Y0 F3000", "M84 X Y Z E"]
    return "\n".join(out) + "\n"
