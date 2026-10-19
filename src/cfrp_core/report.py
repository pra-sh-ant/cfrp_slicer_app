"""Print report as one self-contained HTML page: what will be printed,
how long it takes, materials, checks, coverage per layer, and for a
structural PCB the ink, its checks and every wire's resistance."""
from __future__ import annotations

import base64
import datetime
import html

from .coverage import layer_coverage


def _t(seconds):
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m {s:02d}s"


def _rows(rows):
    return "".join(f"<tr><th>{html.escape(str(k))}</th><td>{html.escape(str(v))}</td></tr>" for k, v in rows)


def _issues(validation):
    items = [m for m in validation.get("messages", []) if not m.startswith("✅")]
    if not items:
        return "<p class='ok'>All checks passed.</p>"
    cls = "bad" if validation.get("status") == "Fail" else "warn"
    return "<ul>" + "".join(f"<li class='{cls}'>{html.escape(m)}</li>" for m in items) + "</ul>"


def html_report(fibre=None, board=None, image_png: bytes | None = None, title="Print report") -> str:
    parts = []
    if image_png:
        parts.append(f"<img src='data:image/png;base64,{base64.b64encode(image_png).decode()}' alt='3D view'>")
    if board is not None:
        fibre = board.fibre
        parts.append(f"<h2>Board</h2><table>{_rows([('Print mode', board.mode), ('Board origin on bed', f'X {board.offset[0]:.2f}  Y {board.offset[1]:.2f} mm')] + [('Note', n) for n in board.notes])}</table>")
    if fibre is not None and fibre.trajectory:
        c, st = fibre.config, fibre.stats or {}
        rows = [("Shape", c.geometry.shape), ("Pattern", c.infill_type), ("Layers", c.layers),
                ("Tracks per layer", "fill" if c.fill_solid else c.tracks_per_layer),
                ("First layer", "solid" + (f" ({c.first_layer_infill})" if c.first_layer_infill else "")
                 if c.first_layer_solid else "as the others"),
                ("Track gap / bend radius", f"{c.track_gap_mm:g} mm / {c.min_radius_mm:g} mm"),
                ("Height", f"{fibre.max_z:.2f} mm"), ("Print time", _t(st.get('print_time_s', 0))),
                ("Fibre length", f"{st.get('fiber_length_mm', 0) / 1000:.2f} m"),
                ("Fibre mass", f"{st.get('fiber_mass_g', 0) * 1000:.0f} mg")]
        if "coverage" in st:
            rows += [("Fibre coverage", f"{st['coverage'] * 100:.0f} % (first layer {st.get('first_coverage', 0) * 100:.0f} %)"),
                     ("Tow overlap", f"{st['overlap'] * 100:.1f} %")]
        parts.append(f"<h2>Fibre laminate <span class='{fibre.validation['status'].lower()}'>"
                     f"{fibre.validation['status']}</span></h2><table>{_rows(rows)}</table>")
        parts.append(_issues(fibre.validation))
        if fibre.target_shapes:
            try:
                cov = layer_coverage(fibre.trajectory, fibre.target_shapes, fibre.config.track_gap_mm)
            except Exception:
                cov = {}
            if cov:
                bars = "".join(f"<tr><th>Layer {k}</th><td><div class='bar' style='width:{v['coverage'] * 100:.0f}%'>"
                               f"{v['coverage'] * 100:.0f} %</div></td></tr>" for k, v in sorted(cov.items())[:200])
                parts.append(f"<h3>Coverage per layer</h3><table class='cov'>{bars}</table>")
        if fibre.constraint_notes or fibre.info:
            parts.append("<h3>Notes</h3><ul>" + "".join(f"<li>{html.escape(n)}</li>" for n in
                                                       list(fibre.constraint_notes) + list(fibre.info)) + "</ul>")
    up = getattr(board, "fibre_upper", None) if board is not None else None
    if up is not None:
        parts.append(f"<h2>Upper laminate <span class='{up.validation['status'].lower()}'>{up.validation['status']}"
                     f"</span></h2><table>{_rows([('Layers', up.config.layers), ('Print time', _t((up.stats or {}).get('print_time_s', 0)))])}</table>")
        parts.append(_issues(up.validation))
    diw = board.diw if board is not None else None
    if diw is not None:
        st = diw.stats
        rows = [("Ink layers", ", ".join(layer.feature for layer in diw.layers)),
                ("Ink time", _t(st["print_time_s"])), ("Ink starts", st["starts"])]
        rows += [(f"{k} ink", f"{v / 1000:.2f} m, {st['ink_volume_mm3'].get(k, 0):.1f} mm³")
                 for k, v in st["ink_length_mm"].items()]
        parts.append(f"<h2>Electronics (DIW) <span class='{diw.validation['status'].lower()}'>"
                     f"{diw.validation['status']}</span></h2><table>{_rows(rows)}</table>")
        parts.append(_issues(diw.validation))
        if diw.validation.get("traces"):
            tr = "".join(f"<tr><td>{html.escape(t['name'])}</td><td>{html.escape(t['net'])}</td>"
                         f"<td>{t['length_mm']:.1f}</td><td>{t['width_mm']:g}</td>"
                         f"<td>{t['resistance_ohm'] * 1000:.1f}</td></tr>" for t in diw.validation["traces"])
            parts.append("<h3>Wires</h3><table class='grid'><tr><th>Wire</th><th>Net</th><th>Length mm</th>"
                         f"<th>Width mm</th><th>R mΩ</th></tr>{tr}</table>")
    when = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 920px; margin: 24px auto; color: #1d2030; }}
h1 {{ margin-bottom: 0; }} .when {{ color: #778; margin-top: 2px; }}
table {{ border-collapse: collapse; margin: 6px 0 12px; }} th {{ text-align: left; padding: 3px 14px 3px 0;
color: #445; font-weight: 600; }} td {{ padding: 3px 8px; }}
table.grid td, table.grid th {{ border-bottom: 1px solid #dde; }}
.bar {{ background: #5b8def; color: white; font-size: 11px; padding: 1px 4px; min-width: 28px; }}
.pass, .ok {{ color: #148a3c; }} .warning, .warn {{ color: #a86b00; }} .fail, .bad {{ color: #c0262d; }}
h2 span {{ font-size: 14px; margin-left: 8px; }} img {{ max-width: 100%; border-radius: 6px; }}
</style></head><body><h1>{html.escape(title)}</h1><p class="when">{when}</p>
{''.join(parts)}</body></html>"""
