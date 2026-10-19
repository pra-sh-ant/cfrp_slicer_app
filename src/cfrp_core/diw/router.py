"""Unrouted connections ("ratsnest") and a grid auto-router for a BoardDesign.

Pads and traces with the same net name should be electrically joined. A
net whose elements form several separate islands has unrouted connections:
the shortest links that would join the islands (a minimum spanning tree over
nearest points). `auto_route` draws a wire for each link with A* on a grid
(8 directions, a penalty per bend), keeping a clearance from other nets,
holes and the board edge; a routed wire is an obstacle for the next nets.
A link with no way through on level 1 is routed on level 2 (a crossover):
there only other nets' pads and level-2 wires block it, and an insulating
bridge is printed wherever it crosses another net's level-1 wire.
"""
from __future__ import annotations

import heapq
import math

import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import nearest_points, unary_union

from .config import DiwConfig
from .design import BoardDesign, Trace, conductors, hole_shape, joined


def _islands(elems):
    """Connected groups (lists of element indices) of joined elements."""
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


def _root(parent, i):
    while parent[i] != i:
        i = parent[i]
    return i


def ratsnest(design: BoardDesign) -> list[dict]:
    """Unrouted links: [{net, a: (x, y), b: (x, y), length}] joining the
    islands of every net (shortest links first, Kruskal over islands)."""
    elems = [e for e in conductors(design) if e["net"]]
    out = []
    for net in sorted({e["net"] for e in elems}):
        mine = [e for e in elems if e["net"] == net]
        groups = _islands(mine)
        if len(groups) < 2:
            continue
        shapes = [unary_union([mine[i]["geom"] for i in g]) for g in groups]
        edges = []
        for i in range(len(shapes)):
            for j in range(i + 1, len(shapes)):
                p, q = nearest_points(shapes[i], shapes[j])
                edges.append((p.distance(q), i, j, (p.x, p.y), (q.x, q.y)))
        edges.sort()
        parent = list(range(len(shapes)))
        for dist, i, j, a, b in edges:
            ri, rj = _root(parent, i), _root(parent, j)
            if ri != rj:
                parent[ri] = rj
                out.append(dict(net=net, a=a, b=b, length=dist))
    return sorted(out, key=lambda r: r["length"])


def _anchor(design, net, pt):
    """Where a wire should end for a link point: the centre of the pad of
    `net` the point is on (wires look tidy pad-centre to pad-centre), else
    the point itself (it lies on a trace)."""
    for p in design.pads:
        if p.net == net and Point(pt).distance(Point(p.x, p.y)) <= max(p.w_mm, p.h_mm):
            return (p.x, p.y)
    return pt


def route_link(design: BoardDesign, cfg: DiwConfig, net: str, a, b, width=None, step=0.5,
               bend_cost=1.5, max_cells=400_000, level=1):
    """Points of a wire from a to b (A* on a `step` grid) avoiding other
    nets, holes and the edge, or None if there is no way through. On level
    2 only other nets' pads and level-2 wires are in the way (it crosses
    their level-1 wires over bridges)."""
    width = width or max(cfg.line_width_mm * 1.6, 0.8)
    keep = width / 2.0 + cfg.clearance_mm
    board = Polygon(design.outline).buffer(0).buffer(-(cfg.edge_clearance_mm + width / 2.0), join_style=2)
    blocks = [hole_shape(h).buffer(cfg.edge_clearance_mm + width / 2.0) for h in design.holes]
    blocks += [e["geom"].buffer(keep) for e in conductors(design)
               if e["net"] != net and (level == 1 or e["kind"] == "pad" or e.get("level", 1) == 2)]
    for z in design.zones:
        if z.kind == "keepout":
            blocks.append(Polygon(z.points).buffer(width / 2.0))
    free = board.difference(unary_union(blocks)) if blocks else board
    if free.is_empty:
        return None
    x0, y0, x1, y1 = Polygon(design.outline).bounds
    nx, ny = int((x1 - x0) / step) + 1, int((y1 - y0) / step) + 1
    if nx * ny > max_cells:
        step = math.sqrt((x1 - x0) * (y1 - y0) / max_cells)
        nx, ny = int((x1 - x0) / step) + 1, int((y1 - y0) / step) + 1
    gx, gy = np.meshgrid(x0 + np.arange(nx) * step, y0 + np.arange(ny) * step, indexing="ij")
    ok = shapely.contains_xy(free, gx, gy)

    def cell(p):
        return (min(max(int(round((p[0] - x0) / step)), 0), nx - 1),
                min(max(int(round((p[1] - y0) / step)), 0), ny - 1))

    sa, sb = cell(a), cell(b)
    # The pads themselves sit inside the edge/clearance margin of nothing of
    # ours, but their cells may be blocked by the bead margin: open a small
    # disc round both ends.
    for c, p in ((sa, a), (sb, b)):
        r = int(math.ceil((width + cfg.clearance_mm) / step))
        for i in range(max(c[0] - r, 0), min(c[0] + r + 1, nx)):
            for j in range(max(c[1] - r, 0), min(c[1] + r + 1, ny)):
                q = (x0 + i * step, y0 + j * step)
                if math.dist(q, p) <= width + cfg.clearance_mm and not any(
                        bl.contains(Point(q)) for bl in blocks[len(design.holes):]
                        if bl.distance(Point(p)) > 1e-6):
                    ok[i, j] = True
    moves = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]
    cost = {(sa, None): 0.0}
    heap = [(0.0, 0.0, sa, None)]
    came = {}
    seen = set()
    hx, hy = sb
    while heap:
        _, g, c, d = heapq.heappop(heap)
        if (c, d) in seen:
            continue
        seen.add((c, d))
        if c == sb:
            pts = [c]
            key = (c, d)
            while key in came:
                key = came[key]
                pts.append(key[0])
            pts = pts[::-1]
            xy = [(x0 + i * step, y0 + j * step) for i, j in pts]
            xy[0], xy[-1] = tuple(a), tuple(b)
            return _simplify(xy)
        for m in moves:
            n = (c[0] + m[0], c[1] + m[1])
            if not (0 <= n[0] < nx and 0 <= n[1] < ny) or not ok[n]:
                continue
            if m[0] and m[1] and not (ok[c[0] + m[0], c[1]] and ok[c[0], c[1] + m[1]]):
                continue                     # no squeezing diagonally past a corner
            ng = g + math.hypot(*m) * step + (bend_cost * step if d is not None and d != m else 0.0)
            key = (n, m)
            if ng < cost.get(key, float("inf")):
                cost[key] = ng
                came[key] = (c, d)
                h = math.hypot(n[0] - hx, n[1] - hy) * step
                heapq.heappush(heap, (ng + h, ng, n, m))
    return None


def _simplify(pts):
    """Drop points where the direction does not change."""
    if len(pts) <= 2:
        return pts
    out = [pts[0]]
    for p, q in zip(pts[1:-1], pts[2:], strict=False):
        a = (p[0] - out[-1][0], p[1] - out[-1][1])
        b = (q[0] - p[0], q[1] - p[1])
        if abs(a[0] * b[1] - a[1] * b[0]) > 1e-9:
            out.append(p)
    out.append(pts[-1])
    return out


def auto_route(design: BoardDesign, cfg: DiwConfig | None = None, width=None, crossovers_allowed=True):
    """Route every unrouted link (shortest first), on level 2 (a crossover)
    where level 1 has no way through. Returns (design, report) where
    report = {"routed": n, "crossovers": n, "failed": [net, ...]}."""
    cfg = cfg or DiwConfig()
    routed, crossed, failed = 0, 0, []
    for _ in range(200):
        links = ratsnest(design)
        if not links:
            break
        progress = False
        for link in links:
            net = link["net"]
            a, b = _anchor(design, net, link["a"]), _anchor(design, net, link["b"])
            level = 1
            pts = route_link(design, cfg, net, a, b, width)
            if (pts is None or len(pts) < 2) and crossovers_allowed:
                level = 2
                pts = route_link(design, cfg, net, a, b, width, level=2)
            if pts is None or len(pts) < 2 or LineString(pts).length < 1e-6:
                if net not in failed:
                    failed.append(net)
                continue
            w = width or max(cfg.line_width_mm * 1.6, 0.8)
            name = f"{net} (routed)" if level == 1 else f"{net} (routed, crossover)"
            design = design.model_copy(update=dict(traces=design.traces + (
                Trace(points=tuple(pts), width_mm=w, net=net, name=name, level=level),)))
            routed += 1
            crossed += level == 2
            progress = True
            break                            # recompute: the new wire changes the islands
        if not progress:
            break
    return design, {"routed": routed, "crossovers": crossed, "failed": failed}
