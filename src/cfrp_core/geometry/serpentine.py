"""Zigzag patterns: oriented serpentine infill (spec §5.6)."""
from __future__ import annotations

import math

import numpy as np
from shapely import STRtree
from shapely.affinity import rotate
from shapely.geometry import LineString, MultiLineString, Point

from ._common import check_gap_radius, count_crossings


def _uturn(p0, p3, d, step=0.3):
    """Tangent-continuous U-turn from the end p0 of a line running along
    unit `d` to the start p3 of the next line (running along -d).

    The nearer end is first extended straight along d until both ends are
    level, then a semicircle of radius lat/2 joins them (lat = line
    spacing): the widest constant-curvature turn that stays strictly between
    the two lines, so it never crosses either line or the next U-turn.
    Lines closer than 2·min_rad cannot turn at min_rad; validation reports
    that as curvature."""
    d = np.asarray(d, dtype=np.float64)
    along = float((p3 - p0) @ d)
    lateral = (p3 - p0) - along * d
    lat = float(np.linalg.norm(lateral))
    if lat < 1e-9:
        return []
    n = lateral / lat
    a0 = p0 + d * max(along, 0.0)      # p0 extended level with p3
    a3 = p3 + d * max(-along, 0.0)     # p3 extended level with p0
    pts = []
    if along > 1e-9:
        pts.append(a0.tolist())
    r = lat / 2.0
    c = (a0 + a3) / 2.0
    k = max(8, int(math.ceil(math.pi * r / step)))
    for phi in np.linspace(0.0, math.pi, k + 1)[1:]:
        pts.append((c - n * r * math.cos(phi) + d * r * math.sin(phi)).tolist())
    if along < -1e-9:
        pts.append(p3.tolist())
    return pts


def _x_span(seg):
    xs = (seg.coords[0][0], seg.coords[-1][0])
    return min(xs), max(xs)


def _boustrophedon_runs(scan, region, cells=False):
    """Group every scan segment into runs of consecutive lines that one
    zigzag can serve (boustrophedon decomposition). A segment continues a
    run when it overlaps that run's segment on the line before AND the convex
    hull of the two lies inside `region`: the U-turn and its straight
    level-off between them then stay on the part. On a convex part that is
    always so (one run, the plain zigzag). Earlier code kept only the longest
    segment per line, so around a hole consecutive lines could sit on
    opposite sides and the U-turn crossed the hole.

    `cells=True` is the classic cellular decomposition: a run also ends
    wherever the line splits or merges (its segment must be the only one
    overlapping the run's last segment, and vice versa). More, shorter runs,
    but no run wraps round a hole and boxes another one in, so runs meet
    only across one line gap and can be chained without crossings."""
    runs, open_runs = [], []
    prev = []
    for segs in scan:
        new_open, taken = [], set()
        for seg in segs:
            lo, hi = _x_span(seg)
            if cells:
                above = [p for p in prev if min(hi, _x_span(p)[1]) > max(lo, _x_span(p)[0])]
                if len(above) != 1 or sum(1 for s in segs if min(_x_span(s)[1], _x_span(above[0])[1])
                                          > max(_x_span(s)[0], _x_span(above[0])[0])) != 1:
                    home = []
                    runs.append(home)
                    taken.add(id(home))
                    home.append(seg)
                    new_open.append(home)
                    continue
            home = None
            for r in open_runs:
                if id(r) in taken:
                    continue
                plo, phi = _x_span(r[-1])
                if min(hi, phi) > max(lo, plo) and \
                        region.covers(MultiLineString([seg, r[-1]]).convex_hull):
                    home = r
                    break
            if home is None:
                home = []
                runs.append(home)
            taken.add(id(home))
            home.append(seg)
            new_open.append(home)
        open_runs = new_open
        prev = segs
    return [r for r in runs if r]


def _zigzag(lines, direction):
    """One serpentine over consecutive `lines`, first line run along +x
    (direction 1) or -x, joined by _uturn."""
    path = []
    for i, line in enumerate(lines):
        coords = list(line.coords)
        if direction == -1:
            coords.reverse()
        if i > 0 and path:
            # The previous line ran along -direction (direction flips per line).
            path.extend(_uturn(np.array(path[-1]), np.array(coords[0]), (-direction, 0.0)))
        path.extend(coords)
        direction *= -1
    return path


def _margin_rings(poly, gap):
    """The part's boundary rings offset half a track inside: the corridor
    between the outline (or a hole) and the eroded line ends, where a
    connector can run past the U-turns without crossing them."""
    inner = poly.buffer(-gap / 2.0, join_style=1)
    rings = []
    for g in getattr(inner, 'geoms', [inner]):
        if g.geom_type == 'Polygon' and not g.is_empty:
            rings.extend([g.exterior, *g.interiors])
    return rings


def _routes(a, b, poly, rings, reach, step=0.75):
    """Candidate connectors from a to b, each the points after a up to b:
    straight if that stays on the part, and along every margin ring within
    `reach` of both ends, both ways round. None of them spans a hole or
    leaves the outline."""
    out = []
    if poly.covers(LineString([a, b])):
        out.append([tuple(b)])
    pa, pb = Point(a), Point(b)
    for ring in rings:
        if ring.distance(pa) > reach or ring.distance(pb) > reach:
            continue
        length = ring.length
        t0, t1 = ring.project(pa), ring.project(pb)
        for sign in (1.0, -1.0):
            span = ((t1 - t0) * sign) % length
            n = max(2, int(span / step))
            pts = [ring.interpolate((t0 + sign * span * k / n) % length).coords[0] for k in range(n + 1)]
            out.append(pts + [tuple(b)])
    return out or [[tuple(b)]]


_CROSSING_COST = 1e6     # one crossing outweighs any connector length
_EXACT_ORDER_RUNS = 8    # run counts up to this get the exact order search


def _order_runs(n_runs, cost, first, first_variants):
    """Visiting order [(run, variant), ...] starting with `first` (in one of
    `first_variants`) that minimises the summed connector cost
    cost[i, a, j, b]. Exact subset DP up to _EXACT_ORDER_RUNS runs, greedy
    nearest-cheapest beyond."""
    if n_runs > _EXACT_ORDER_RUNS:
        a0 = min(first_variants, key=lambda a: min(
            (cost[first, a, j, b][0] for j in range(n_runs) if j != first for b in range(4)),
            default=0.0))
        order, left = [(first, a0)], set(range(n_runs)) - {first}
        while left:
            i, a = order[-1]
            j, b = min(((j, b) for j in left for b in range(4)), key=lambda jb: cost[i, a, jb[0], jb[1]][0])
            order.append((j, b))
            left.remove(j)
        return order
    dp = {(1 << first, first, a): (0.0, None) for a in first_variants}
    for mask in range(1 << n_runs):     # transitions only add bits
        if not mask >> first & 1:
            continue
        for i in range(n_runs):
            if not mask >> i & 1:
                continue
            for a in range(4):
                here = dp.get((mask, i, a))
                if here is None:
                    continue
                for j in range(n_runs):
                    if mask >> j & 1:
                        continue
                    for b in range(4):
                        c = here[0] + cost[i, a, j, b][0]
                        key = (mask | 1 << j, j, b)
                        if key not in dp or c < dp[key][0]:
                            dp[key] = (c, (mask, i, a))
    full = (1 << n_runs) - 1
    state = min((k for k in dp if k[0] == full), key=lambda k: dp[k][0])
    order = []
    while state is not None:
        order.append(state[1:])
        state = dp[state][1]
    return order[::-1]


def _join_runs(runs, poly, gap, start_from_end, min_rad=0.0):
    """Zigzag each run and chain them into one stroke.

    Every connector from the end of one run (4 ways round) to the start of
    another is routed straight or along a margin ring and scored by how many
    runs it crosses (any crossing is fibre on fibre, whichever is laid
    first), then by length. That cost does not depend on the order, so the
    order and way round of every run is then chosen to minimise the total
    (_order_runs). (Chaining greedily nearest-first, and not checking the
    run a connector led into, left connectors cutting through runs: 14
    crossings on a plate with two bolt holes.)"""
    variants = []
    for r in runs:
        options = []
        for lines in (r, r[::-1]):
            for d in (1, -1):
                options.append(_zigzag(lines, d))
        variants.append(options)
    geoms = [[LineString(v) if len(v) > 1 else None for v in vs] for vs in variants]
    # A run whose way round is not fixed yet: both U-turn sides.
    footprint = [MultiLineString([g for g in gs[:2] if g is not None]) for gs in geoms]
    tree = STRtree(footprint)
    region = poly.buffer(1e-6)
    rings = _margin_rings(poly, gap)
    reach = max(min_rad, gap) + 1.5 * gap
    n = len(runs)
    cost = {}
    for i in range(n):
        for a in range(4):
            end = tuple(variants[i][a][-1])
            for j in range(n):
                if j == i:
                    continue
                for b in range(4):
                    start = variants[j][b][0]
                    best = None
                    for route in _routes(end, start, region, rings, reach):
                        link = LineString([end] + route)
                        hits = sum(1 for k in tree.query(link, predicate='crosses') if k not in (i, j))
                        hits += sum(1 for g in (geoms[i][a], geoms[j][b])
                                    if g is not None and link.crosses(g))
                        c = hits * _CROSSING_COST + link.length
                        if best is None or c < best[0]:
                            best = (c, route)
                    cost[i, a, j, b] = best
    first = n - 1 if start_from_end else 0
    order = _order_runs(n, cost, first, (2, 3) if start_from_end else (0, 1))
    i, a = order[0]
    path = list(variants[i][a])
    for j, b in order[1:]:
        route = cost[i, a, j, b][1]
        path.extend(route[:-1])
        path.extend(variants[j][b])
        i, a = j, b
    return path


def zigzag_fits(poly, gap, min_rad, share=0.5):
    """True when at least `share` of poly is wide enough for zigzag lines
    whose U-turns stay max(min_rad, gap) inside the outline (the area a
    zigzag can reach: poly opened by that radius). Thinner walls only get
    stubs across the wall with U-turns far below min_rad (a 4 mm pen-holder
    wall: 226k curvature points and 300 crossings); concentric rings follow
    such walls instead."""
    if poly.is_empty or poly.area <= 0:
        return False
    e = max(min_rad, gap)
    core = poly.buffer(-e, join_style=1)
    if core.is_empty or core.area < gap:
        return False
    return core.buffer(e, join_style=1).intersection(poly).area >= share * poly.area


def oriented_serpentine(poly, angle, gap, min_rad, start_from_end=False):
    """Scan-line zigzag at `angle` degrees inside `poly`.

    Rotate by -angle; erode by max(r_min, gap) so turns stay inside; scan lines at
    miny + gap/2 + k*gap; group every segment into boustrophedon runs
    (_boustrophedon_runs), zigzag each run and chain the runs; alternate
    direction; join with semicircular U-turns (_uturn);
    rotate back by +angle.
    """
    check_gap_radius(gap, min_rad)

    # Rotate about ONE fixed point both ways. (Rotating back about the
    # path's own centroid shifted every angled layer: 0.5 mm on a triangle,
    # far off the part around holes.)
    pivot = poly.centroid
    rot_poly = rotate(poly, -angle, origin=pivot)
    # Line ends sit this far inside: min_rad, or at least one gap, which is
    # what a lat/2 U-turn plus half a tow needs to stay inside the outline.
    safe_poly = rot_poly.buffer(-max(min_rad, gap), join_style=1)
    if safe_poly.is_empty or safe_poly.area < gap:
        # Walls too thin for that: keep the turns inside with the least
        # erosion that fits (one gap = a gap/2 U-turn plus half a tow), and
        # only use the bare outline when even that leaves nothing.
        safe_poly = rot_poly.buffer(-gap, join_style=1)
        if safe_poly.is_empty or safe_poly.area < gap:
            safe_poly = rot_poly

    minx, miny, maxx, maxy = safe_poly.bounds
    y = miny + gap / 2.0
    scan = []                                  # per scan line: its segments, left to right
    while y <= maxy:
        line = LineString([(minx - gap * 2, y), (maxx + gap * 2, y)])
        inter = line.intersection(safe_poly)
        parts = [g for g in getattr(inter, 'geoms', [inter])
                 if g.geom_type == 'LineString' and not g.is_empty]
        scan.append(sorted(parts, key=lambda g: g.coords[0][0] + g.coords[-1][0]))
        y += gap

    region = rot_poly.buffer(1e-6)
    runs = _boustrophedon_runs(scan, region)
    if len(runs) <= 1:
        # Convex (one segment per line, each overlapping the last): the
        # original single zigzag.
        lines = runs[0] if runs else []
        path = _zigzag(list(reversed(lines)) if start_from_end else lines,
                       -1 if start_from_end else 1)
    else:
        # Long runs (fewest U-turn breaks) can wrap round a hole and box
        # other runs in; strict cells cannot. Keep whichever chains with
        # fewer crossings (then the shorter path).
        options = []
        for cells in (False, True):
            r = runs if not cells else _boustrophedon_runs(scan, region, cells=True)
            p = _join_runs(r, rot_poly, gap, start_from_end, min_rad)
            options.append((count_crossings(p), LineString(p).length, p))
            if options[0][0] == 0:
                break
        path = min(options, key=lambda o: o[:2])[2]

    if not path:
        return []
    final_line = LineString(path)
    final_line = rotate(final_line, angle, origin=pivot)
    return list(final_line.coords)
