"""Design-rule and process checks for a structural PCB (DRC), plus an
estimate of every trace's resistance and current capacity.

Electrical nodes are found by connectivity: elements that touch are one node
(a level-2 wire only joins its own net where it touches level 1; across
another net it is insulated by a bridge); a node whose elements carry two
different net names is a short. Different nodes closer than the clearance
risk shorting when the ink spreads.
"""
from __future__ import annotations

from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

from .config import DiwConfig
from .design import BoardDesign, conductors, crossovers, hole_shape, joined


def _nodes(elems):
    parent = list(range(len(elems)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(elems)):
        for j in range(i + 1, len(elems)):
            if joined(elems[i], elems[j]):
                parent[find(i)] = find(j)
    groups = {}
    for i in range(len(elems)):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def _where(geom):
    p = geom.representative_point() if not geom.is_empty else None
    return f"({p.x:.1f}, {p.y:.1f})" if p is not None else ""


def _film_mm(cfg: DiwConfig) -> float:
    """Cured film thickness of all conductor layers."""
    return cfg.conductor_layers * cfg.layer_height_mm * cfg.cured_thickness_ratio


def trace_resistance(trace, cfg: DiwConfig) -> float:
    """R = rho * L / (w * t), t = cured film thickness of all conductor layers."""
    return cfg.resistivity_ohm_mm * trace.length() / (trace.width_mm * _film_mm(cfg))


def trace_capacity_a(trace, cfg: DiwConfig) -> float:
    """Current the cured cross-section carries at max_current_density."""
    return cfg.max_current_density_a_mm2 * trace.width_mm * _film_mm(cfg)


def _printed(e, trace, bead):
    """What actually gets inked: a wire narrower than the bead prints as
    wide as the bead."""
    if trace is not None and trace.width_mm < bead:
        return LineString(trace.points).buffer(bead / 2.0, quad_segs=8)
    return e["geom"]


def drc(design: BoardDesign, cfg: DiwConfig) -> dict:
    """{"status": Pass|Warning|Fail, "messages": [...], "notes": [...],
    "nets": [...], "traces": [...]}"""
    errors, warnings, notes = [], [], []
    elems = conductors(design)
    board = Polygon(design.outline).buffer(0)
    if not board.is_valid or board.is_empty:
        errors.append("The board outline is not a valid closed shape.")
    inner = board.buffer(-cfg.edge_clearance_mm, join_style=2)
    lw = cfg.line_width_mm
    bead = cfg.bead_mm
    spread = f" (a {lw:g} mm line spreads to {bead:g} mm)" if bead > lw else ""

    # printability
    for t in design.traces:
        if t.width_mm < 0.9 * bead:
            errors.append(f"{t.name or 'A trace'} is {t.width_mm:g} mm wide, narrower than the "
                          f"{bead:g} mm printed bead{spread}: widen it or use a finer nozzle.")
    for p in design.pads:
        if min(p.w_mm, p.h_mm) < bead:
            warnings.append(f"Pad {p.name or ''} ({p.w_mm:g} x {p.h_mm:g} mm) is smaller than one "
                            f"{bead:g} mm bead; it prints as a single line.")
    printed = [_printed(e, design.traces[e["index"]] if e["kind"] == "trace" else None, bead) for e in elems]

    # placement
    for e, g in zip(elems, printed, strict=True):
        if not inner.covers(g):
            errors.append(f"{e['name']} comes within {cfg.edge_clearance_mm:g} mm of the board edge or "
                          f"leaves the board near {_where(g.difference(inner))}.")
        for h in design.holes:
            keep = hole_shape(h).buffer(cfg.edge_clearance_mm)
            if g.intersects(keep):
                errors.append(f"{e['name']} runs over or within {cfg.edge_clearance_mm:g} mm of the hole "
                              f"at ({h.x:g}, {h.y:g}).")
    for z in design.zones:
        if z.kind == "keepout":
            ko = Polygon(z.points).buffer(0)
            for e, g in zip(elems, printed, strict=True):
                if g.intersects(ko):
                    errors.append(f"{e['name']} enters a keepout zone near {_where(g.intersection(ko))}.")

    # crossovers: a level-2 wire over another net's level-1 element
    cross = crossovers(design, cfg.bridge_margin_mm)
    pad_names = {p.name or f"pad {i + 1}" for i, p in enumerate(design.pads)}
    for c in cross:
        if c["under"] in pad_names:
            errors.append(f"{c['wire']} crosses pad {c['under']} of another net: the bridge would cover the "
                          f"pad, which must stay bare for its part. Route the wire round the pad.")
    bridges = unary_union([c["geom"] for c in cross]) if cross else Polygon()
    if cross:
        notes.append(f"{len(cross)} crossover(s): a level-2 wire crosses another net over a printed insulating "
                     f"bridge ({cfg.bridge_layers} layer(s), {cfg.bridge_margin_mm:g} mm past the crossing).")

    # electrical: shorts and clearance between nodes
    groups = _nodes(elems)
    nets = []
    for g in groups:
        names = sorted({elems[i]["net"] for i in g if elems[i]["net"]})
        if len(names) > 1:
            errors.append(f"Short: nets {', '.join(names)} touch ({', '.join(elems[i]['name'] for i in g)}).")
        nets.append(dict(net=names[0] if names else f"node {len(nets) + 1}", elements=[elems[i]["name"] for i in g],
                         geom=unary_union([printed[i] for i in g])))
    for i in range(len(nets)):
        for j in range(i + 1, len(nets)):
            a, b = nets[i]["geom"], nets[j]["geom"]
            if not bridges.is_empty and a.intersects(b):
                # crossing over a bridge: judge the wires beside it
                a, b = a.difference(bridges), b.difference(bridges)
                if a.is_empty or b.is_empty:
                    continue
            d = a.distance(b)
            if d < cfg.clearance_mm:
                warnings.append(f"Clearance: {nets[i]['net']} and {nets[j]['net']} are only {d:.2f} mm apart "
                                f"(minimum {cfg.clearance_mm:g} mm){spread}; the ink may bridge them.")

    # process
    if elems and cfg.substrate_conductive and not (cfg.dielectric and cfg.dielectric_layers):
        errors.append("Carbon fibre is conductive: with no dielectric layer every trace shorts through the "
                      "laminate. Turn the dielectric on (or print on a glass/aramid laminate).")
    elif elems and cfg.substrate_conductive and cfg.min_insulation_overlap_mm > 0:
        from .slicer import dielectric_region
        need = dielectric_region(design, cfg).buffer(-cfg.min_insulation_overlap_mm, join_style=1)
        bare = unary_union(printed).difference(need)
        if bare.area > 1e-3:
            warnings.append(f"Ink is less than {cfg.min_insulation_overlap_mm:g} mm inside the insulation near "
                            f"{_where(bare)}: ink that spreads past its edge touches the carbon fibre and shorts "
                            f"through it. Raise Dielectric Margin, or move the wire away from the edge / hole.")
    if not elems:
        warnings.append("The design has no traces, pads or conductor zones: nothing to print in ink.")

    # parts: a two-pad part with both pads on one net is shorted out
    parts = {}
    for p in design.pads:
        ref, _, pin = (p.name or "").partition(".")
        if ref and pin:
            parts.setdefault(ref, []).append(p)
    for ref, pads in sorted(parts.items()):
        if len(pads) == 2 and pads[0].net and pads[0].net == pads[1].net:
            warnings.append(f"{ref}: both pads are on net {pads[0].net}, so the part is shorted out. Check its "
                            f"wires, or rotate it.")

    traces = []
    for t in design.traces:
        r, cap = trace_resistance(t, cfg), trace_capacity_a(t, cfg)
        row = dict(name=t.name or t.net or "trace", net=t.net, length_mm=t.length(), width_mm=t.width_mm,
                   resistance_ohm=r, capacity_a=cap, level=t.level, current_a=t.current_a)
        if t.current_a > 0:
            row.update(drop_v=t.current_a * r, power_w=t.current_a ** 2 * r)
            if t.current_a > cap:
                need_w = t.current_a / (cfg.max_current_density_a_mm2 * _film_mm(cfg))
                warnings.append(f"{row['name']} must carry {t.current_a:g} A but its {t.width_mm:g} mm x "
                                f"{_film_mm(cfg):.3g} mm cured section takes about {cap:.2g} A "
                                f"({cfg.max_current_density_a_mm2:g} A/mm²): it would overheat. Widen it to "
                                f"{need_w:.2g} mm or print more conductor layers.")
        traces.append(row)
    status = "Fail" if errors else ("Warning" if warnings else "Pass")
    msgs = errors + warnings
    if not msgs:
        msgs = ["DRC passed: widths, clearances, edges and holes are fine."]
    for n in nets:
        n.pop("geom")
    return {"status": status, "messages": msgs, "notes": notes, "nets": nets, "traces": traces}
