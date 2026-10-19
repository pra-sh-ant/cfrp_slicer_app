"""G-code for the DIW head (Marlin-style). Travel G0 with no flow; each
extrusion run opens the valve (pneumatic: dispense_on + dwell) or advances
E by the bead volume (volumetric); tool offset applied to every move."""
from __future__ import annotations

from .config import DiwConfig


def compile_diw_gcode(trajectory, cfg: DiwConfig, name="board", standalone=True) -> str:
    ox, oy, oz = cfg.tool_offset
    lw, lh = cfg.line_width_mm, cfg.layer_height_mm
    out = [
        "; ==========================================",
        "; DIW STRUCTURAL ELECTRONICS",
        f"; Design          : {name}",
        f"; Nozzle / bead   : {cfg.nozzle_mm:g} mm / {lw:g} x {lh:g} mm",
        f"; Layers          : dielectric {cfg.dielectric_layers if cfg.dielectric else 0}, "
        f"conductor {cfg.conductor_layers}, encapsulation {cfg.encapsulation_layers if cfg.encapsulate else 0}",
        f"; Dispense        : {cfg.dispense}",
        "; ==========================================",
    ]
    if standalone:
        out += ["G28 ; Home all axes", "G90 ; Absolute positioning"]
    if cfg.pause_before:
        out.append("M0 Cut the fibre tow and mount the DIW head, then resume ; PAUSE")
    if cfg.tool_select:
        out.append(f"{cfg.tool_select} ; DIW head")
    out.append("G90")
    if cfg.dispense == "volumetric":
        out += ["M83 ; relative E", "G92 E0"]
    flowing = False
    layer = None
    feature = None
    for p in trajectory:
        x, y, z = p['x'] + ox, p['y'] + oy, p['z'] + oz
        if p['extruding']:
            if p['layer'] != layer or p['feature'] != feature:
                out.append(f"; LAYER {p['layer']} {p['feature']}")
                layer, feature = p['layer'], p['feature']
            if not flowing and cfg.dispense == "pneumatic":
                out.append(f"{cfg.dispense_on} ; ink on")
                if cfg.on_delay_ms:
                    out.append(f"G4 P{cfg.on_delay_ms}")
            flowing = True
            f = p['v'] * 60.0
            if cfg.dispense == "volumetric":
                e = p['dist'] * lw * lh * cfg.e_per_mm3
                out.append(f"G1 F{f:.0f} X{x:.3f} Y{y:.3f} Z{z:.3f} E{e:.4f}")
            else:
                out.append(f"G1 F{f:.0f} X{x:.3f} Y{y:.3f} Z{z:.3f}")
        else:
            if flowing and cfg.dispense == "pneumatic":
                out.append(f"{cfg.dispense_off} ; ink off")
                if cfg.off_delay_ms:
                    out.append(f"G4 P{cfg.off_delay_ms}")
            flowing = False
            out.append(f"G0 F{p['v'] * 60.0:.0f} X{x:.3f} Y{y:.3f} Z{z:.3f}")
    if flowing and cfg.dispense == "pneumatic":
        out.append(f"{cfg.dispense_off} ; ink off")
    top = max((p['z'] for p in trajectory), default=0.0) + oz
    out += ["; --- DIW END ---", f"G0 Z{top + 10.0:.3f} ; Safe lift", "G0 X0 Y0 F3000 ; Park gantry",
            "M84 X Y Z E ; Disable steppers"]
    return "\n".join(out) + "\n"
