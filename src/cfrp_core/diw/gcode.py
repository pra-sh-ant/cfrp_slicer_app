"""G-code for the DIW head (Marlin-style). Travel G0 with no flow; each
extrusion run opens the valve (pneumatic: dispense_on + dwell) or advances
E by the bead volume (volumetric); tool offset applied to every move."""
from __future__ import annotations

from .config import DiwConfig


def compile_diw_gcode(trajectory, cfg: DiwConfig, name="board", standalone=True) -> str:
    ox, oy, oz = cfg.tool_offset
    lw, lh = cfg.line_width_mm, cfg.layer_height_mm
    counts = {}
    for p in trajectory:
        if p.get('extruding'):
            counts.setdefault(p['feature'], set()).add(p['layer'])
    cure_after = {p['layer'] for p in trajectory if p.get('cure_after')}
    out = [
        "; ==========================================",
        "; DIW STRUCTURAL ELECTRONICS",
        f"; Design          : {name}",
        f"; Nozzle / bead   : {cfg.nozzle_mm:g} mm / {lw:g} x {lh:g} mm",
        f"; Layers          : dielectric {len(counts.get('dielectric', ()))}, "
        f"conductor {len(counts.get('conductor', ()))}, encapsulation {len(counts.get('encapsulation', ()))}"
        + (f", crossover {len(counts['crossover'])}" if counts.get('crossover') else ""),
        f"; Dispense        : {cfg.dispense}",
        *([f"; Bead spread     : x{cfg.spread_factor:g} (printed {cfg.bead_mm:g} mm)"] if cfg.spread_factor != 1.0
          else []),
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
    # Remaining run length at every point (for closing the valve early).
    remaining = [0.0] * len(trajectory)
    run = 0.0
    for i in range(len(trajectory) - 1, -1, -1):
        p = trajectory[i]
        if not p['extruding']:
            run = 0.0
            continue
        remaining[i] = run
        run += p['dist']
    flowing = False
    valve_open = False
    layer = None
    feature = None
    for i, p in enumerate(trajectory):
        x, y, z = p['x'] + ox, p['y'] + oy, p['z'] + oz
        if p['extruding']:
            if p['layer'] != layer or p['feature'] != feature:
                if layer is not None and p['layer'] != layer and (cfg.uv_cure_between_layers
                                                                  or layer in cure_after):
                    out += [f"{cfg.uv_cure_on} ; UV cure", f"G4 P{int(cfg.uv_cure_s * 1000)}",
                            f"{cfg.uv_cure_off}"]
                if layer is not None and p['layer'] != layer and cfg.layer_wait_s > 0:
                    out.append(f"G4 P{int(round(cfg.layer_wait_s * 1000))} ; let the ink layer dry")
                out.append(f"; LAYER {p['layer']} {p['feature']}")
                layer, feature = p['layer'], p['feature']
            if not flowing:
                if cfg.dispense == "pneumatic":
                    out.append(f"{cfg.dispense_on} ; ink on")
                    valve_open = True
                    if cfg.on_delay_ms:
                        out.append(f"G4 P{cfg.on_delay_ms}")
                elif cfg.prime_e:
                    out.append(f"G1 E{cfg.prime_e:.4f} ; prime")
            flowing = True
            f = p['v'] * 60.0
            if cfg.dispense == "volumetric":
                e = p['dist'] * lw * lh * cfg.e_per_mm3
                out.append(f"G1 F{f:.0f} X{x:.3f} Y{y:.3f} Z{z:.3f} E{e:.4f}")
            else:
                r1, r0 = remaining[i], remaining[i] + p['dist']
                if valve_open and cfg.stop_early_mm > 0 and r1 < cfg.stop_early_mm <= r0 and i > 0:
                    # close the valve exactly stop_early before the line end
                    q = trajectory[i - 1]
                    t = (r0 - cfg.stop_early_mm) / p['dist'] if p['dist'] > 1e-9 else 0.0
                    mx, my, mz = (q['x'] + (p['x'] - q['x']) * t + ox, q['y'] + (p['y'] - q['y']) * t + oy,
                                  q['z'] + (p['z'] - q['z']) * t + oz)
                    if t > 1e-6:
                        out.append(f"G1 F{f:.0f} X{mx:.3f} Y{my:.3f} Z{mz:.3f}")
                    out.append(f"{cfg.dispense_off} ; ink off (early)")
                    valve_open = False
                out.append(f"G1 F{f:.0f} X{x:.3f} Y{y:.3f} Z{z:.3f}")
        else:
            if flowing:
                if cfg.dispense == "pneumatic":
                    if valve_open:
                        out.append(f"{cfg.dispense_off} ; ink off")
                        valve_open = False
                    if cfg.off_delay_ms:
                        out.append(f"G4 P{cfg.off_delay_ms}")
                elif cfg.retract_e:
                    out.append(f"G1 E-{cfg.retract_e:.4f} ; retract")
            flowing = False
            out.append(f"G0 F{p['v'] * 60.0:.0f} X{x:.3f} Y{y:.3f} Z{z:.3f}")
    if flowing and cfg.dispense == "pneumatic" and valve_open:
        out.append(f"{cfg.dispense_off} ; ink off")
    if layer is not None and (cfg.uv_cure_between_layers or layer in cure_after):
        out += [f"{cfg.uv_cure_on} ; UV cure", f"G4 P{int(cfg.uv_cure_s * 1000)}", f"{cfg.uv_cure_off}"]
    top = max((p['z'] for p in trajectory), default=0.0) + oz
    out += ["; --- DIW END ---", f"G0 Z{top + 10.0:.3f} ; Safe lift", "G0 X0 Y0 F3000 ; Park gantry",
            "M84 X Y Z E ; Disable steppers"]
    return "\n".join(out) + "\n"
