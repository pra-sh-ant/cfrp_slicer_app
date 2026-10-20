"""Zigzag patterns: oriented serpentine infill (spec §5.6)."""
from __future__ import annotations

import heapq
import math

import numpy as np
import shapely
from shapely import STRtree
from shapely.affinity import rotate
from shapely.geometry import LineString, MultiLineString, Point, Polygon

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


def _level_ends(lines, direction, keep=0.5):
    """Line coordinates in traversal order with both ends of every turn cut
    back level to the shorter line's end (keeping at least `keep` mm of
    each line). Turns then sit inside the part even where an edge is
    slanted: extending the shorter line instead, as tight turns do, pushes a
    wide turn past the outline."""
    out = []
    for line in lines:
        c = [tuple(q) for q in line.coords]
        out.append(c if direction == 1 else c[::-1])
        direction *= -1
    for i in range(len(out) - 1):
        a, b = out[i], out[i + 1]
        right = a[-1][0] > a[0][0]                 # line i runs +x: the turn is on the right
        level = min(a[-1][0], b[0][0]) if right else max(a[-1][0], b[0][0])
        lo_a = a[0][0] + keep if right else a[0][0] - keep
        lo_b = b[-1][0] + keep if right else b[-1][0] - keep
        xa = max(level, lo_a) if right else min(level, lo_a)
        xb = max(level, lo_b) if right else min(level, lo_b)
        out[i] = a[:-1] + [(xa, a[-1][1])]
        out[i + 1] = [(xb, b[0][1])] + b[1:]
    return out


def _zigzag(lines, direction, level=False):
    """One serpentine over consecutive `lines`, first line run along +x
    (direction 1) or -x, joined by _uturn. `level`: cut each turn's line
    ends back level first (_level_ends; for widely spaced lines)."""
    path = []
    ordered = _level_ends(lines, direction) if level else None
    for i, line in enumerate(lines):
        if ordered is not None:
            coords = list(ordered[i])
        else:
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


def _routes(a, b, poly, rings, reach, step=0.75, roadmap=None):
    """Candidate connectors from a to b, each the points after a up to b:
    straight if that stays on the part, and along every margin ring within
    `reach` of both ends, both ways round. None of them spans a hole or
    leaves the outline. When none exists (a run end by the outline, the next
    run's start beyond a hole: no ring is near both), the shortest way on
    the `roadmap` (a callable), and only failing that the straight line."""
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
            # all of a route's points in one call (point by point, this was
            # most of Auto's time on a plate with holes)
            at = (t0 + sign * span * np.arange(n + 1) / n) % length
            xy = shapely.get_coordinates(shapely.line_interpolate_point(ring, at))
            out.append([tuple(q) for q in xy.tolist()] + [tuple(b)])
    if not out and roadmap is not None:
        detour = roadmap(a, b)
        if detour:
            out.append(detour)
    return out or [[tuple(b)]]


class _Roadmap:
    """Points about every `step` along the margin rings, linked along each
    ring and by straight hops of up to `reach` that stay on the part.
    Shortest-path search on it finds a connector that keeps to the part when
    no single ring joins its ends. (The straight fallback ran 1.9 mm of
    fibre across a 2.5 mm hole by a plate's edge -- found by the property
    tests.)"""

    def __init__(self, region, rings, reach, step):
        self.region, self.reach = region, reach
        pts, edges = [], []
        for ring in rings:
            n = max(3, int(math.ceil(ring.length / step)))
            base = len(pts)
            pts.extend(ring.interpolate(ring.length * k / n).coords[0] for k in range(n))
            edges.extend((base + k, base + (k + 1) % n) for k in range(n))
        self.pts = np.asarray(pts, dtype=np.float64).reshape(-1, 2)
        self.adj = [[] for _ in pts]
        for i, j in edges:
            self._link(i, j)
        if len(pts) > 1:
            geoms = shapely.points(self.pts)
            self.tree = STRtree(geoms)
            i, j = self.tree.query(geoms, predicate='dwithin', distance=reach)
            keep = i < j
            i, j = i[keep], j[keep]
            if len(i):
                ok = shapely.covers(region, shapely.linestrings(np.stack([self.pts[i], self.pts[j]], axis=1)))
                for a, b in zip(i[ok].tolist(), j[ok].tolist(), strict=True):
                    self._link(a, b)
        else:
            self.tree = None

    def _link(self, i, j):
        d = float(np.hypot(*(self.pts[i] - self.pts[j])))
        self.adj[i].append((j, d))
        self.adj[j].append((i, d))

    def _near(self, p):
        """(node, distance) of roadmap nodes a straight hop on the part from p."""
        if self.tree is None:
            return []
        idx = self.tree.query(Point(p), predicate='dwithin', distance=self.reach)
        if not len(idx):
            return []
        segs = shapely.linestrings(np.stack([np.broadcast_to(p, (len(idx), 2)), self.pts[idx]], axis=1))
        ok = shapely.covers(self.region, segs)
        return [(int(k), float(np.hypot(*(self.pts[k] - p)))) for k in idx[ok]]

    def __call__(self, a, b):
        a, b = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
        goal = {k: d for k, d in self._near(b)}
        if not goal:
            return None
        dist, prev, heap = {}, {}, []
        for k, d in self._near(a):
            if d < dist.get(k, math.inf):
                dist[k], prev[k] = d, None
                heapq.heappush(heap, (d, k))
        best, end = math.inf, None
        while heap:
            d, k = heapq.heappop(heap)
            if d > dist.get(k, math.inf) or d >= best:
                continue
            if k in goal and d + goal[k] < best:
                best, end = d + goal[k], k
            for j, w in self.adj[k]:
                nd = d + w
                if nd < dist.get(j, math.inf):
                    dist[j], prev[j] = nd, k
                    heapq.heappush(heap, (nd, j))
        if end is None:
            return None
        chain = []
        while end is not None:
            chain.append(tuple(self.pts[end].tolist()))
            end = prev[end]
        return chain[::-1] + [tuple(b.tolist())]


_CROSSING_COST = 1e6     # one crossing outweighs any connector length
_OFF_PART_COST = 1e9     # fibre off the part / over a hole outweighs any crossings
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


def _join_runs(runs, poly, gap, start_from_end, min_rad=0.0, level=False):
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
                options.append(_zigzag(lines, d, level))
        variants.append(options)
    geoms = [[LineString(v) if len(v) > 1 else None for v in vs] for vs in variants]
    # A run whose way round is not fixed yet: both U-turn sides.
    footprint = [MultiLineString([g for g in gs[:2] if g is not None]) for gs in geoms]
    tree = STRtree(footprint)
    region = poly.buffer(1e-6)
    rings = _margin_rings(poly, gap)
    reach = max(min_rad, gap) + 1.5 * gap
    roadmap = []                                 # built on first need

    def detour(a, b):
        if not roadmap:
            roadmap.append(_Roadmap(region, rings, reach, max(gap, 0.75)))
        return roadmap[0](a, b)

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
                    for route in _routes(end, start, region, rings, reach, roadmap=detour):
                        link = LineString([end] + route)
                        hits = sum(1 for k in tree.query(link, predicate='crosses') if k not in (i, j))
                        hits += sum(1 for g in (geoms[i][a], geoms[j][b])
                                    if g is not None and link.crosses(g))
                        c = hits * _CROSSING_COST + link.length
                        if not region.covers(link):
                            c += _OFF_PART_COST
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


def _wide_safe(poly, pitch, gap):
    """Where widely spaced line ends may lie: pitch/2 + gap/2 inside the
    outer edges, pitch + gap/2 clear of holes."""
    parts = []
    for g in getattr(poly, 'geoms', [poly]):
        if g.geom_type != 'Polygon' or g.is_empty:
            continue
        outer = Polygon(g.exterior).buffer(-(pitch / 2.0 + gap / 2.0), join_style=1)
        holes = [Polygon(r).buffer(pitch + gap / 2.0, join_style=1) for r in g.interiors]
        parts.append(outer.difference(shapely.union_all(holes)) if holes else outer)
    return shapely.union_all(parts) if parts else Polygon()


def oriented_serpentine(poly, angle, gap, min_rad, start_from_end=False, spacing=None, offset=0.0):
    """Scan-line zigzag at `angle` degrees inside `poly`.

    Rotate by -angle; erode by max(r_min, gap) so turns stay inside; scan lines at
    miny + gap/2 + k*gap; group every segment into boustrophedon runs
    (_boustrophedon_runs), zigzag each run and chain the runs; alternate
    direction; join with semicircular U-turns (_uturn);
    rotate back by +angle.

    `spacing` (> gap): lines that far apart, the first `offset` further in,
    so the U-turns (radius spacing/2) can keep the bend radius; layers
    shifted by one gap each fill in between. Line ends then sit spacing/2 +
    gap/2 inside, and each turn's ends are cut back level (_level_ends).
    """
    check_gap_radius(gap, min_rad)

    # Rotate about ONE fixed point both ways. (Rotating back about the
    # path's own centroid shifted every angled layer: 0.5 mm on a triangle,
    # far off the part around holes.)
    pivot = poly.centroid
    rot_poly = rotate(poly, -angle, origin=pivot)
    pitch = float(spacing) if spacing else gap
    wide = pitch > gap * 1.001
    # Line ends sit this far inside: min_rad, or at least one gap, which is
    # what a lat/2 U-turn plus half a tow needs to stay inside the outline
    # (wide lines: their spacing/2 turn plus half a tow -- and a whole
    # spacing round holes, where the two ends of a turn lie on a curve that
    # bends towards the hole and the turn between them bulged into it).
    if wide:
        safe_poly = _wide_safe(rot_poly, pitch, gap)
    else:
        safe_poly = rot_poly.buffer(-max(min_rad, gap), join_style=1)
    if safe_poly.is_empty or safe_poly.area < gap:
        # Walls too thin for that: keep the turns inside with the least
        # erosion that fits (one gap = a gap/2 U-turn plus half a tow), and
        # only use the bare outline when even that leaves nothing.
        safe_poly = rot_poly.buffer(-gap, join_style=1)
        if safe_poly.is_empty or safe_poly.area < gap:
            safe_poly = rot_poly

    minx, miny, maxx, maxy = safe_poly.bounds
    y = miny + gap / 2.0 + (float(offset) % pitch if wide else 0.0)
    scan = []                                  # per scan line: its segments, left to right
    while y <= maxy:
        line = LineString([(minx - gap * 2, y), (maxx + gap * 2, y)])
        inter = line.intersection(safe_poly)
        parts = [g for g in getattr(inter, 'geoms', [inter])
                 if g.geom_type == 'LineString' and not g.is_empty]
        scan.append(sorted(parts, key=lambda g: g.coords[0][0] + g.coords[-1][0]))
        y += pitch

    region = rot_poly.buffer(1e-6)
    runs = _boustrophedon_runs(scan, region)
    if len(runs) <= 1:
        # Convex (one segment per line, each overlapping the last): the
        # original single zigzag.
        lines = runs[0] if runs else []
        path = _zigzag(list(reversed(lines)) if start_from_end else lines,
                       -1 if start_from_end else 1, wide)
    else:
        # Long runs (fewest U-turn breaks) can wrap round a hole and box
        # other runs in; strict cells cannot. Keep whichever chains with
        # fewer crossings (then the shorter path).
        options = []
        for cells in (False, True):
            r = runs if not cells else _boustrophedon_runs(scan, region, cells=True)
            p = _join_runs(r, rot_poly, gap, start_from_end, min_rad, wide)
            options.append((count_crossings(p), LineString(p).length, p))
            if options[0][0] == 0:
                break
        path = min(options, key=lambda o: o[:2])[2]

    if not path:
        return []
    final_line = LineString(path)
    final_line = rotate(final_line, angle, origin=pivot)
    return list(final_line.coords)


def centre_fill(path, region, gap, at_end=True, anchor=None, line=None):
    """Back-and-forth lines over the hollow a spiral leaves in its middle
    (its innermost ring can be no narrower than twice the bend radius),
    starting next to the path's end (at_end), or next to `anchor` when
    given, else ending next to the path's start. Lines run along the
    hollow's long side, one gap apart and a gap from the laid track; where
    the spiral bites into the hollow (a line under 60 % of its neighbour)
    the lines stop, so every turn joins lines of about the same length and
    stays inside. Their half-circle turns (radius gap/2) are tighter than
    the bend radius, which validation reports. [] when there is no hollow
    wider than a track there.

    `line` (Strict Bend Radius): only one of those lines, with no turns:
    the middle one for line 0, then outwards on alternate sides, so that
    layers numbered 0, 1, 2 ... fill the hollow together."""
    if len(path) < 2 or region.is_empty:
        return []
    forward = at_end or anchor is not None
    target = anchor if anchor is not None else (path[-1] if at_end else path[0])
    point = Point(target)
    laid = LineString(path).buffer(gap * 0.98, join_style=1)
    free = region.buffer(-gap / 2.0, join_style=1).difference(laid)
    comps = [g for g in getattr(free, 'geoms', [free]) if g.geom_type == 'Polygon' and g.area > 0.5 * gap * gap]
    if not comps:
        return []
    hollow = min(comps, key=lambda g: g.distance(point))
    if hollow.distance(point) > 1.5 * gap:
        return []
    rect = hollow.minimum_rotated_rectangle
    c = list(rect.exterior.coords) if rect.geom_type == 'Polygon' else []
    if len(c) < 4:
        return []
    e1, e2 = (c[0], c[1]), (c[1], c[2])
    a, b = e1 if math.dist(*e1) >= math.dist(*e2) else e2
    ang = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
    pivot = hollow.centroid
    rot = rotate(hollow, -ang, origin=pivot)
    minx, miny, maxx, maxy = rot.bounds
    n = int(math.floor((maxy - miny) / gap)) + 1
    y0 = miny + ((maxy - miny) - (n - 1) * gap) / 2.0
    trim = gap / 2.0 if n > 1 else 0.0           # room for the half-circle turns
    lines = []
    for k in range(n):
        y = y0 + k * gap
        inter = LineString([(minx - 1.0, y), (maxx + 1.0, y)]).intersection(rot)
        segs = [g for g in getattr(inter, 'geoms', [inter]) if g.geom_type == 'LineString' and g.length > 1e-6]
        if not segs:
            lines.append(None)
            continue
        seg = max(segs, key=lambda g: g.length)
        x0, x1 = sorted((seg.coords[0][0], seg.coords[-1][0]))
        if x1 - x0 > 2.0 * trim + 1e-3:
            x0, x1 = x0 + trim, x1 - trim
        lines.append(LineString([(x0, y), (x1, y)]) if x1 - x0 >= gap else None)
    # the longest run of neighbouring lines of similar length
    groups, cur = [], []
    for ln in lines:
        if ln is not None and (not cur or ln.length >= 0.6 * cur[-1].length and cur[-1].length >= 0.6 * ln.length):
            cur.append(ln)
            continue
        if cur:
            groups.append(cur)
        cur = [ln] if ln is not None else []
    if cur:
        groups.append(cur)
    if not groups:
        return []
    lines = max(groups, key=lambda g: sum(ln.length for ln in g))
    if line is not None:
        mid = (len(lines) - 1) / 2.0
        order = sorted(range(len(lines)), key=lambda k: (abs(k - mid), k))
        lines = [lines[order[int(line) % len(lines)]]]
    options = []
    for order in (lines, lines[::-1]):
        for direction in (1, -1):
            zz = _zigzag(order, direction, level=True)
            pts = (list(rotate(LineString(zz), ang, origin=pivot).coords) if len(zz) > 1
                   else [rotate(Point(zz[0]), ang, origin=pivot).coords[0]])
            options.append(pts)
    if forward:
        return min(options, key=lambda p: math.dist(p[0], target))
    return min(options, key=lambda p: math.dist(p[-1], target))
