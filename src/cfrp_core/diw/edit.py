"""Editing operations on a BoardDesign (pure: each returns a new design).

A selection is (kind, key): ("trace"|"pad"|"hole"|"zone", index) or
("part", ref) for all pads of one placed footprint ("R1.1", "R1.2", ... share
the reference "R1"), so a part moves, rotates and deletes as one piece.
"""
from __future__ import annotations

import math

from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

from .design import BoardDesign, Trace, footprint, interdigitated, meander, pad_shape, spiral_coil, trace_shape

_FIELD = {"trace": "traces", "pad": "pads", "hole": "holes", "zone": "zones"}


def part_ref(pad_name: str) -> str | None:
    """'R1.2' -> 'R1'; None for a pad that is not part of a footprint."""
    if "." in pad_name:
        ref, _, num = pad_name.rpartition(".")
        if ref and num.isdigit():
            return ref
    return None


def part_pads(design: BoardDesign, ref: str) -> list[int]:
    return [i for i, p in enumerate(design.pads) if part_ref(p.name) == ref]


def geometry(design: BoardDesign, sel):
    kind, key = sel
    if kind == "part":
        return unary_union([pad_shape(design.pads[i]) for i in part_pads(design, key)])
    if kind == "trace":
        return trace_shape(design.traces[key])
    if kind == "pad":
        return pad_shape(design.pads[key])
    if kind == "hole":
        h = design.holes[key]
        return Point(h.x, h.y).buffer(h.d_mm / 2)
    return Polygon(design.zones[key].points).buffer(0)


def centre(design: BoardDesign, sel):
    g = geometry(design, sel)
    if g.is_empty:
        return (0.0, 0.0)
    b = g.bounds
    return ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)


def _rot(x, y, cx, cy, a):
    c, s = math.cos(a), math.sin(a)
    return (cx + (x - cx) * c - (y - cy) * s, cy + (x - cx) * s + (y - cy) * c)


def transform(design: BoardDesign, sel, dx=0.0, dy=0.0, angle_deg=0.0, about=None) -> BoardDesign:
    """Move by (dx, dy) and/or rotate by angle_deg about `about` (default:
    the selection's centre)."""
    kind, key = sel
    cx, cy = about if about is not None else centre(design, sel)
    a = math.radians(angle_deg)

    def pt(x, y):
        x, y = _rot(x, y, cx, cy, a) if angle_deg else (x, y)
        return (x + dx, y + dy)

    def mv_pad(p):
        x, y = pt(p.x, p.y)
        return p.model_copy(update=dict(x=x, y=y, angle_deg=(p.angle_deg + angle_deg) % 360.0))

    if kind == "part":
        idx = set(part_pads(design, key))
        pads = tuple(mv_pad(p) if i in idx else p for i, p in enumerate(design.pads))
        return design.model_copy(update=dict(pads=pads))
    items = list(getattr(design, _FIELD[kind]))
    it = items[key]
    if kind == "pad":
        items[key] = mv_pad(it)
    elif kind == "hole":
        x, y = pt(it.x, it.y)
        items[key] = it.model_copy(update=dict(x=x, y=y))
    else:
        items[key] = it.model_copy(update=dict(points=tuple(pt(x, y) for x, y in it.points)))
    return design.model_copy(update={_FIELD[kind]: tuple(items)})


def delete(design: BoardDesign, sel) -> BoardDesign:
    kind, key = sel
    if kind == "part":
        idx = set(part_pads(design, key))
        return design.model_copy(update=dict(pads=tuple(p for i, p in enumerate(design.pads) if i not in idx)))
    items = list(getattr(design, _FIELD[kind]))
    items.pop(key)
    return design.model_copy(update={_FIELD[kind]: tuple(items)})


def pick(design: BoardDesign, x, y, tol=0.6):
    """What is under (x, y): a part (any of its pads), a loose pad, a hole,
    the nearest trace, or a zone; None for empty board."""
    p = Point(x, y)
    for i, pad in enumerate(design.pads):
        if pad_shape(pad).buffer(tol * 0.5).contains(p):
            ref = part_ref(pad.name)
            return ("part", ref) if ref else ("pad", i)
    for i, h in enumerate(design.holes):
        if math.dist((h.x, h.y), (x, y)) <= h.d_mm / 2 + tol * 0.5:
            return ("hole", i)
    best = None
    for i, t in enumerate(design.traces):
        from shapely.geometry import LineString
        d = LineString(t.points).distance(p)
        if d <= t.width_mm / 2 + tol and (best is None or d < best[0]):
            best = (d, ("trace", i))
    if best:
        return best[1]
    for i, z in enumerate(design.zones):
        if Polygon(z.points).buffer(0).contains(p):
            return ("zone", i)
    return None


def next_ref(design: BoardDesign, prefix: str) -> str:
    """First free reference 'prefix<n>'."""
    used = {part_ref(p.name) for p in design.pads}
    n = 1
    while f"{prefix}{n}" in used:
        n += 1
    return f"{prefix}{n}"


# --- the parts palette (easy mode) ------------------------------------------------

# key -> (label, ref prefix, footprint or generator, plain description)
PALETTE = {
    "led": ("LED", "D", "LED / resistor 1206", "A light. Two pads; connect + and -."),
    "resistor": ("Resistor", "R", "LED / resistor 1206", "Limits current, e.g. in front of an LED."),
    "small_part": ("Small 2-pad part (0805)", "C", "LED / resistor 0805", "A small resistor, capacitor or LED."),
    "transistor": ("Transistor (SOT-23)", "Q", "SOT-23", "A small 3-pin chip: transistor or sensor."),
    "header2": ("2-pin connector", "J", "Header 2-pin 2.54", "Where power or wires plug in."),
    "header3": ("3-pin connector", "J", "Header 3-pin 2.54", "Power + signal connector."),
    "jst": ("JST-PH battery plug", "J", "JST-PH 2-pin", "Small battery / sensor plug."),
    "testpad": ("Test pad", "TP", "Test pad", "A round pad to measure or solder a wire."),
    "gauge": ("Strain gauge", "SG", "meander", "A zigzag wire whose resistance changes when the board bends."),
    "heater": ("Heater", "H", "heater", "A long zigzag wire that warms up when current flows."),
    "electrodes": ("Touch / humidity electrodes", "E", "electrodes", "Two interlocking combs that sense touch or moisture."),
    "coil": ("Coil / antenna", "L", "coil", "A flat spiral: NFC antenna, inductor or heater."),
}


def place(design: BoardDesign, key: str, x: float, y: float, angle_deg: float = 0.0) -> tuple[BoardDesign, tuple]:
    """Add a palette item centred at (x, y); returns (design, selection)."""
    label, prefix, what, _ = PALETTE[key]
    ref = next_ref(design, prefix)
    if what == "meander":
        # 4 legs, 2 mm apart: starts at (x-7.5, y-3), ends at (x-7.5, y+3),
        # each end led to a test pad on the left.
        tr = meander(x - 7.5, y - 3.0, 15.0, 2.0, 4, width=0.6, net=ref, name=ref)
        leads = (Trace(points=((x - 10.0, y - 3.0), (x - 7.5, y - 3.0)), width_mm=0.8, net=ref, name=f"{ref} lead a"),
                 Trace(points=((x - 10.0, y + 3.0), (x - 7.5, y + 3.0)), width_mm=0.8, net=ref, name=f"{ref} lead b"))
        pads = (footprint("Test pad", x - 10.0, y - 3.0, 0, f"{ref}a", (ref,))
                + footprint("Test pad", x - 10.0, y + 3.0, 0, f"{ref}b", (ref,)))
        d = design.model_copy(update=dict(traces=design.traces + (tr,) + leads, pads=design.pads + tuple(pads)))
        return d, ("trace", len(d.traces) - 3)
    if what == "heater":
        tr = meander(x - 10.0, y - 5.0, 20.0, 2.5, 5, width=1.0, net=ref, name=ref)
        d = design.model_copy(update=dict(traces=design.traces + (tr,)))
        return d, ("trace", len(d.traces) - 1)
    if what == "electrodes":
        trs = interdigitated(x - 5.0, y - 4.0, 6.0, 8, 1.4, 0.5, (f"{ref}A", f"{ref}B"))
        d = design.model_copy(update=dict(traces=design.traces + tuple(trs)))
        return d, ("trace", len(d.traces) - len(trs))
    if what == "coil":
        tr = spiral_coil(x, y, 2.0, 4, 1.6, 0.6, ref, ref)
        d = design.model_copy(update=dict(traces=design.traces + (tr,)))
        return d, ("trace", len(d.traces) - 1)
    pads = footprint(what, x, y, angle_deg, ref)
    d = design.model_copy(update=dict(pads=design.pads + tuple(pads)))
    return d, ("part", ref)


def rectangle_outline(w, h, corner_radius=0.0):
    poly = Polygon([(0, 0), (w, 0), (w, h), (0, h)])
    if corner_radius > 0:
        r = min(corner_radius, w / 3, h / 3)
        poly = poly.buffer(-r, join_style=2).buffer(r, quad_segs=8)
    pts = list(poly.exterior.coords)[:-1]
    return tuple(pts if Polygon(pts).exterior.is_ccw else pts[::-1])


def corner_holes(design: BoardDesign, inset=5.0, d_mm=3.2) -> BoardDesign:
    from .design import Hole
    x0, y0, x1, y1 = Polygon(design.outline).bounds
    holes = tuple(Hole(x=x, y=y, d_mm=d_mm) for x, y in ((x0 + inset, y0 + inset), (x1 - inset, y0 + inset),
                                                          (x1 - inset, y1 - inset), (x0 + inset, y1 - inset)))
    keep = tuple(h for h in design.holes if all(math.dist((h.x, h.y), (n.x, n.y)) > 1 for n in holes))
    return design.model_copy(update=dict(holes=keep + holes))


def auto_net(design: BoardDesign, points) -> tuple[BoardDesign, str]:
    """Net for a wire drawn through `points` (easy mode): the net of a pad it
    starts or ends on; if those pads have none, a new one is given to them,
    so wires and pads stay named and the short check stays meaningful."""
    ends = [Point(points[0]), Point(points[-1])]
    hit = [i for i, p in enumerate(design.pads) if any(pad_shape(p).buffer(0.3).contains(e) for e in ends)]
    nets = [design.pads[i].net for i in hit if design.pads[i].net]
    if nets:
        net = nets[0]
    else:
        used = {p.net for p in design.pads} | {t.net for t in design.traces}
        k = 1
        while f"N{k}" in used:
            k += 1
        net = f"N{k}"
    pads = tuple(p.model_copy(update=dict(net=net)) if i in hit and not p.net else p
                 for i, p in enumerate(design.pads))
    return design.model_copy(update=dict(pads=pads)), net


# --- several items at once -------------------------------------------------------

def transform_many(design: BoardDesign, sels, dx=0.0, dy=0.0, angle_deg=0.0) -> BoardDesign:
    """Move / rotate several selections together (rotation about the centre
    of all of them)."""
    sels = list(dict.fromkeys(sels))
    if not sels:
        return design
    about = None
    if angle_deg:
        b = unary_union([geometry(design, s) for s in sels]).bounds
        about = ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)
    for s in sels:
        design = transform(design, s, dx, dy, angle_deg, about)
    return design


def delete_many(design: BoardDesign, sels) -> BoardDesign:
    """Delete several selections (highest index first so the rest stay valid)."""
    by_kind = {}
    for s in dict.fromkeys(sels):
        by_kind.setdefault(s[0], []).append(s[1])
    for kind, keys in by_kind.items():
        if kind == "part":
            for ref in keys:
                design = delete(design, ("part", ref))
            continue
        for k in sorted(keys, reverse=True):
            design = delete(design, (kind, k))
    return design


def select_in_rect(design: BoardDesign, x0, y0, x1, y1) -> list:
    """Every item lying completely inside the rectangle."""
    from shapely.geometry import box
    r = box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
    out, refs = [], set()
    for i, p in enumerate(design.pads):
        ref = part_ref(p.name)
        if ref:
            if ref not in refs and r.contains(geometry(design, ("part", ref))):
                out.append(("part", ref))
            refs.add(ref)
        elif r.contains(pad_shape(p)):
            out.append(("pad", i))
    for kind, items in (("trace", design.traces), ("hole", design.holes), ("zone", design.zones)):
        for i in range(len(items)):
            if r.contains(geometry(design, (kind, i))):
                out.append((kind, i))
    return out


def copy_items(design: BoardDesign, sels) -> dict:
    """A clipboard: the selected traces, pads, holes and zones."""
    clip = {"traces": [], "pads": [], "holes": [], "zones": []}
    for kind, key in dict.fromkeys(sels):
        if kind == "part":
            clip["pads"] += [design.pads[i] for i in part_pads(design, key)]
        else:
            clip[_FIELD[kind]].append(getattr(design, _FIELD[kind])[key])
    return clip


def paste_items(design: BoardDesign, clip: dict, dx=5.0, dy=-5.0) -> tuple[BoardDesign, list]:
    """Add a clipboard moved by (dx, dy); copied parts get new names (R1 ->
    the next free R<n>). Returns (design, selections of the pasted items)."""
    def mv(pts):
        return tuple((x + dx, y + dy) for x, y in pts)
    rename = {}
    pads = []
    for p in clip["pads"]:
        ref = part_ref(p.name)
        name = p.name
        if ref:
            if ref not in rename:
                prefix = ref.rstrip("0123456789") or "U"
                taken = design.model_copy(update=dict(pads=design.pads + tuple(pads)))
                used = {part_ref(q.name) for q in taken.pads} | set(rename.values())
                n = 1
                while f"{prefix}{n}" in used:
                    n += 1
                rename[ref] = f"{prefix}{n}"
            name = rename[ref] + "." + p.name.rpartition(".")[2]
        pads.append(p.model_copy(update=dict(x=p.x + dx, y=p.y + dy, name=name)))
    n_t, n_h, n_z, n_p = len(design.traces), len(design.holes), len(design.zones), len(design.pads)
    d = design.model_copy(update=dict(
        traces=design.traces + tuple(t.model_copy(update=dict(points=mv(t.points))) for t in clip["traces"]),
        pads=design.pads + tuple(pads),
        holes=design.holes + tuple(h.model_copy(update=dict(x=h.x + dx, y=h.y + dy)) for h in clip["holes"]),
        zones=design.zones + tuple(z.model_copy(update=dict(points=mv(z.points))) for z in clip["zones"])))
    sels = [("trace", n_t + i) for i in range(len(clip["traces"]))]
    sels += [("hole", n_h + i) for i in range(len(clip["holes"]))]
    sels += [("zone", n_z + i) for i in range(len(clip["zones"]))]
    sels += [("part", r) for r in dict.fromkeys(rename.values())]
    sels += [("pad", n_p + i) for i, p in enumerate(pads) if not part_ref(p.name)]
    return d, sels


# --- corners of the outline and bends of wires/zones ----------------------------------

def _points(design: BoardDesign, target):
    kind, key = target
    if kind == "outline":
        return list(design.outline), True
    if kind == "trace":
        return list(design.traces[key].points), False
    return list(design.zones[key].points), True


def _set_points(design: BoardDesign, target, pts):
    kind, key = target
    if kind == "outline":
        return design.model_copy(update=dict(outline=tuple(pts)))
    items = list(getattr(design, _FIELD[kind]))
    items[key] = items[key].model_copy(update=dict(points=tuple(pts)))
    return design.model_copy(update={_FIELD[kind]: tuple(items)})


def _all_targets(design):
    return ([("trace", i) for i in range(len(design.traces))] + [("zone", i) for i in range(len(design.zones))]
            + [("outline", None)])


def find_vertex(design: BoardDesign, x, y, tol=1.0, targets=None):
    """(target, index) of the corner/bend nearest (x, y) within tol."""
    best = None
    for t in targets if targets is not None else _all_targets(design):
        pts, _ = _points(design, t)
        for k, (px, py) in enumerate(pts):
            d = math.hypot(px - x, py - y)
            if d <= tol and (best is None or d < best[0]):
                best = (d, t, k)
    return None if best is None else (best[1], best[2])


def find_segment(design: BoardDesign, x, y, tol=1.0, targets=None):
    """(target, i) of the segment pts[i] -> pts[i+1] nearest (x, y) within tol."""
    from shapely.geometry import LineString
    best = None
    for t in targets if targets is not None else _all_targets(design):
        pts, closed = _points(design, t)
        segs = list(zip(pts, pts[1:] + ([pts[0]] if closed else []), strict=False))
        for i, (a, b) in enumerate(segs):
            d = LineString([a, b]).distance(Point(x, y))
            if d <= tol and (best is None or d < best[0]):
                best = (d, t, i)
    return None if best is None else (best[1], best[2])


def move_vertex(design: BoardDesign, target, k, x, y) -> BoardDesign:
    pts, _ = _points(design, target)
    pts[k] = (x, y)
    return _set_points(design, target, pts)


def insert_vertex(design: BoardDesign, target, i, x, y) -> BoardDesign:
    """Add a bend/corner after point i (on segment i)."""
    pts, _ = _points(design, target)
    pts.insert(i + 1, (x, y))
    return _set_points(design, target, pts)


def delete_vertex(design: BoardDesign, target, k) -> BoardDesign:
    """Remove a bend/corner (a wire keeps 2 points, a shape 3)."""
    pts, closed = _points(design, target)
    if len(pts) <= (3 if closed else 2):
        return design
    pts.pop(k)
    return _set_points(design, target, pts)
