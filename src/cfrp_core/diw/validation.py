"""Design-rule and process checks for a structural PCB (DRC), plus an
estimate of every trace's resistance.

Electrical nodes are found by connectivity: elements that touch are one node;
a node whose elements carry two different net names is a short. Different
nodes closer than the clearance risk shorting when the ink spreads.
"""
from __future__ import annotations

from shapely.geometry import Polygon
from shapely.ops import unary_union

from .config import DiwConfig
from .design import BoardDesign, conductors, hole_shape


def _nodes(elems):
    parent = list(range(len(elems)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(elems)):
        for j in range(i + 1, len(elems)):
            if elems[i]["geom"].intersects(elems[j]["geom"]):
                parent[find(i)] = find(j)
    groups = {}
    for i in range(len(elems)):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def _where(geom):
    p = geom.representative_point() if not geom.is_empty else None
    return f"({p.x:.1f}, {p.y:.1f})" if p is not None else ""


def trace_resistance(trace, cfg: DiwConfig) -> float:
    """R = rho * L / (w * t), t = cured film thickness of all conductor layers."""
    t = cfg.conductor_layers * cfg.layer_height_mm * cfg.cured_thickness_ratio
    return cfg.resistivity_ohm_mm * trace.length() / (trace.width_mm * t)


def drc(design: BoardDesign, cfg: DiwConfig) -> dict:
    """{"status": Pass|Warning|Fail, "messages": [...], "nets": [...], "traces": [...]}"""
    errors, warnings = [], []
    elems = conductors(design)
    board = Polygon(design.outline).buffer(0)
    if not board.is_valid or board.is_empty:
        errors.append("The board outline is not a valid closed shape.")
    inner = board.buffer(-cfg.edge_clearance_mm, join_style=2)
    lw = cfg.line_width_mm

    # printability
    for t in design.traces:
        if t.width_mm < 0.9 * lw:
            errors.append(f"{t.name or 'A trace'} is {t.width_mm:g} mm wide, narrower than the "
                          f"{lw:g} mm printed bead: widen it or use a finer nozzle.")
    for p in design.pads:
        if min(p.w_mm, p.h_mm) < lw:
            warnings.append(f"Pad {p.name or ''} ({p.w_mm:g} x {p.h_mm:g} mm) is smaller than one "
                            f"{lw:g} mm bead; it prints as a single line.")

    # placement
    for e in elems:
        if not inner.covers(e["geom"]):
            errors.append(f"{e['name']} comes within {cfg.edge_clearance_mm:g} mm of the board edge or "
                          f"leaves the board near {_where(e['geom'].difference(inner))}.")
        for h in design.holes:
            keep = hole_shape(h).buffer(cfg.edge_clearance_mm)
            if e["geom"].intersects(keep):
                errors.append(f"{e['name']} runs over or within {cfg.edge_clearance_mm:g} mm of the hole "
                              f"at ({h.x:g}, {h.y:g}).")
    for z in design.zones:
        if z.kind == "keepout":
            ko = Polygon(z.points).buffer(0)
            for e in elems:
                if e["geom"].intersects(ko):
                    errors.append(f"{e['name']} enters a keepout zone near {_where(e['geom'].intersection(ko))}.")

    # electrical: shorts and clearance between nodes
    groups = _nodes(elems)
    nets = []
    for g in groups:
        names = sorted({elems[i]["net"] for i in g if elems[i]["net"]})
        if len(names) > 1:
            errors.append(f"Short: nets {', '.join(names)} touch ({', '.join(elems[i]['name'] for i in g)}).")
        nets.append(dict(net=names[0] if names else f"node {len(nets) + 1}", elements=[elems[i]["name"] for i in g],
                         geom=unary_union([elems[i]["geom"] for i in g])))
    for i in range(len(nets)):
        for j in range(i + 1, len(nets)):
            d = nets[i]["geom"].distance(nets[j]["geom"])
            if d < cfg.clearance_mm:
                warnings.append(f"Clearance: {nets[i]['net']} and {nets[j]['net']} are only {d:.2f} mm apart "
                                f"(minimum {cfg.clearance_mm:g} mm); the ink may bridge them.")

    # process
    if elems and cfg.substrate_conductive and not (cfg.dielectric and cfg.dielectric_layers):
        errors.append("Carbon fibre is conductive: with no dielectric layer every trace shorts through the "
                      "laminate. Turn the dielectric on (or print on a glass/aramid laminate).")
    if not elems:
        warnings.append("The design has no traces, pads or conductor zones: nothing to print in ink.")

    traces = []
    for t in design.traces:
        traces.append(dict(name=t.name or t.net or "trace", net=t.net, length_mm=t.length(), width_mm=t.width_mm,
                           resistance_ohm=trace_resistance(t, cfg)))
    status = "Fail" if errors else ("Warning" if warnings else "Pass")
    msgs = errors + warnings
    if not msgs:
        msgs = ["DRC passed: widths, clearances, edges and holes are fine."]
    for n in nets:
        n.pop("geom")
    return {"status": status, "messages": msgs, "nets": nets, "traces": traces}
