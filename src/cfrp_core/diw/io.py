"""Import into a BoardDesign: board outlines from DXF, nets from a netlist.

DXF (ASCII, any CAD program): closed LWPOLYLINE / POLYLINE, LINE chains,
CIRCLE and ARC entities. The largest closed shape is the board outline;
circles (and other closed shapes) inside it become holes. Units follow
$INSUNITS (inches, cm, m are converted to mm; unknown = mm).

Netlist: one connection per line, either CSV `ref,pin,net` (e.g. R1,2,VCC;
a header line is skipped) or `NET: R1.2 D1.1 ...` (also R1-2). Pads named
`<ref>.<pin>` (as placed footprints are) get that net.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import polygonize, unary_union

from .design import BoardDesign, Hole

_UNITS = {1: 25.4, 2: 304.8, 4: 1.0, 5: 10.0, 6: 1000.0}


def _pairs(text):
    lines = text.splitlines()
    for k in range(0, len(lines) - 1, 2):
        yield lines[k].strip(), lines[k + 1].strip()


def read_dxf_shapes(path) -> tuple[list[Polygon], list[tuple[float, float, float]]]:
    """(closed polygons, circles (x, y, r)) in mm."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    scale = 1.0
    m = re.search(r"\$INSUNITS\s*\n\s*70\s*\n\s*(\d+)", text)
    if m:
        scale = _UNITS.get(int(m.group(1)), 1.0)
    ents, cur = [], None
    in_entities = False
    for code, val in _pairs(text):
        if code == "2" and val in ("ENTITIES", "BLOCKS"):
            in_entities = val == "ENTITIES"
        if not in_entities:
            continue
        if code == "0":
            if cur is not None:
                ents.append(cur)
            cur = {"type": val, "pts": [], "f": {}}
            continue
        if cur is None:
            continue
        if code in ("10", "20"):
            if code == "10":
                cur["pts"].append([float(val), None])
            elif cur["pts"]:
                cur["pts"][-1][1] = float(val)
        else:
            cur["f"].setdefault(code, val)
    if cur is not None:
        ents.append(cur)

    polys, circles, segs = [], [], []
    vertex_run = None
    for e in ents:
        t, pts, f = e["type"], [(x * scale, y * scale) for x, y in e["pts"] if y is not None], e["f"]
        if t == "LWPOLYLINE" and len(pts) >= 2:
            closed = int(f.get("70", "0")) & 1
            if closed and len(pts) >= 3:
                polys.append(Polygon(pts))
            else:
                segs.append(LineString(pts))
        elif t == "POLYLINE":
            vertex_run = {"closed": int(f.get("70", "0")) & 1, "pts": []}
        elif t == "VERTEX" and vertex_run is not None and pts:
            vertex_run["pts"].append(pts[0])
        elif t == "SEQEND" and vertex_run is not None:
            vp = vertex_run["pts"]
            if vertex_run["closed"] and len(vp) >= 3:
                polys.append(Polygon(vp))
            elif len(vp) >= 2:
                segs.append(LineString(vp))
            vertex_run = None
        elif t == "LINE":
            x1, y1 = float(f.get("11", 0)) * scale, float(f.get("21", 0)) * scale
            if pts:
                segs.append(LineString([pts[0], (x1, y1)]))
        elif t == "CIRCLE" and pts:
            circles.append((pts[0][0], pts[0][1], float(f.get("40", 0)) * scale))
        elif t == "ARC" and pts:
            r = float(f.get("40", 0)) * scale
            a0, a1 = math.radians(float(f.get("50", 0))), math.radians(float(f.get("51", 0)))
            if a1 <= a0:
                a1 += 2 * math.pi
            n = max(4, int((a1 - a0) / math.radians(5)))
            cx, cy = pts[0]
            segs.append(LineString([(cx + r * math.cos(a0 + (a1 - a0) * i / n),
                                     cy + r * math.sin(a0 + (a1 - a0) * i / n)) for i in range(n + 1)]))
    if segs:
        merged = unary_union([s for s in segs if s.length > 0])
        polys += [p for p in polygonize(merged) if p.area > 0]
    return [p.buffer(0) for p in polys if p.is_valid or p.buffer(0).area > 0], circles


def import_dxf(path, design: BoardDesign | None = None) -> BoardDesign:
    """The design with its outline (largest closed shape) and holes (circles
    and closed shapes inside it) taken from a DXF; parts and wires kept.
    Raises ValueError if the file has no closed outline."""
    design = design or BoardDesign()
    polys, circles = read_dxf_shapes(path)
    shapes = polys + [Point(x, y).buffer(r, quad_segs=16) for x, y, r in circles if r > 0]
    if not shapes:
        raise ValueError("The DXF has no closed shape (polyline, circle or chain of lines) to use as the board.")
    board = max(shapes, key=lambda p: p.area)
    holes = []
    for x, y, r in circles:
        c = Point(x, y).buffer(r)
        if c.area < board.area * 0.99 and board.contains(c):
            holes.append(Hole(x=x, y=y, d_mm=2 * r))
    outline = list(board.exterior.coords)[:-1]
    if not Polygon(outline).exterior.is_ccw:
        outline = outline[::-1]
    return design.model_copy(update=dict(outline=tuple(outline), holes=tuple(holes)))


def parse_netlist(text: str) -> dict[str, str]:
    """{'R1.2': 'VCC', ...} from CSV `ref,pin,net` or `NET: R1.2 D1-1` lines."""
    out = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if ":" in line and "," not in line:
            net, _, rest = line.partition(":")
            for tok in re.split(r"[\s,]+", rest.strip()):
                m = re.fullmatch(r"([A-Za-z_]+\d*[A-Za-z]*)[.\-](\d+)", tok)
                if m:
                    out[f"{m.group(1)}.{m.group(2)}"] = net.strip()
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3 and parts[1].isdigit():
            out[f"{parts[0]}.{parts[1]}"] = parts[2]
    return out


def apply_netlist(design: BoardDesign, text: str) -> tuple[BoardDesign, dict]:
    """Assign nets to pads by name; returns (design, {"assigned": n,
    "missing": [pad names in the netlist but not on the board]})."""
    nets = parse_netlist(text)
    names = {p.name for p in design.pads}
    pads = tuple(p.model_copy(update=dict(net=nets[p.name])) if p.name in nets else p for p in design.pads)
    assigned = sum(1 for p in design.pads if p.name in nets)
    return design.model_copy(update=dict(pads=pads)), {
        "assigned": assigned, "missing": sorted(n for n in nets if n not in names)}


def export_dxf(design: BoardDesign, path) -> None:
    """Board outline, holes and wires (as centre lines) to an ASCII DXF (mm)."""
    out = ["0", "SECTION", "2", "HEADER", "9", "$INSUNITS", "70", "4", "0", "ENDSEC",
           "0", "SECTION", "2", "ENTITIES"]

    def poly(pts, closed, layer):
        out.extend(["0", "LWPOLYLINE", "8", layer, "90", str(len(pts)), "70", "1" if closed else "0"])
        for x, y in pts:
            out.extend(["10", f"{x:.4f}", "20", f"{y:.4f}"])
    poly(design.outline, True, "OUTLINE")
    for h in design.holes:
        out.extend(["0", "CIRCLE", "8", "HOLES", "10", f"{h.x:.4f}", "20", f"{h.y:.4f}", "40", f"{h.d_mm / 2:.4f}"])
    for t in design.traces:
        poly(t.points, False, "WIRES")
    out.extend(["0", "ENDSEC", "0", "EOF"])
    Path(path).write_text("\n".join(out) + "\n", encoding="utf-8")
