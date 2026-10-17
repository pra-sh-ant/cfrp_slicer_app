"""Zigzag patterns: oriented serpentine infill (spec §5.6)."""
from __future__ import annotations

import math

import numpy as np
from shapely.affinity import rotate
from shapely.geometry import LineString, MultiLineString, Point

from ._common import check_gap_radius


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


def _boustrophedon_runs(scan, region):
    """Group every scan segment into runs of consecutive lines that one
    zigzag can serve (boustrophedon decomposition). A segment continues a
    run when it overlaps that run's segment on the line before AND the convex
    hull of the two lies inside `region`: the U-turn and its straight
    level-off between them then stay on the part. On a convex part that is
    always so (one run, the plain zigzag). Earlier code kept only the longest
    segment per line, so around a hole consecutive lines could sit on
    opposite sides and the U-turn crossed the hole."""
    runs, open_runs = [], []
    for segs in scan:
        new_open, taken = [], set()
        for seg in segs:
            lo, hi = _x_span(seg)
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


def _connector(a, b, poly, gap):
    """Points after `a` up to `b`: straight if that stays inside the part,
    otherwise along the nearest boundary ring (offset half a track inside),
    so the fibre never spans a hole or leaves the outline."""
    seg = LineString([a, b])
    if poly.buffer(1e-6).contains(seg):
        return [tuple(b)]
    inner = poly.buffer(-gap / 2.0, join_style=1)
    rings = []
    for g in getattr(inner, 'geoms', [inner]):
        if g.geom_type == 'Polygon' and not g.is_empty:
            rings.extend([g.exterior, *g.interiors])
    if not rings:
        return [tuple(b)]
    pa, pb = Point(a), Point(b)
    ring = min(rings, key=lambda r: r.distance(pa) + r.distance(pb))
    length = ring.length
    t0, t1 = ring.project(pa), ring.project(pb)
    fwd = (t1 - t0) % length
    span, sign = (fwd, 1.0) if fwd <= length - fwd else (length - fwd, -1.0)
    n = max(2, int(span / 0.75))
    pts = [ring.interpolate((t0 + sign * span * k / n) % length).coords[0] for k in range(n + 1)]
    return pts + [tuple(b)]


def _join_runs(runs, poly, gap, start_from_end):
    """Zigzag each run and chain them nearest-first into one stroke."""
    variants = []
    for r in runs:
        options = []
        for lines in (r, r[::-1]):
            for d in (1, -1):
                options.append(_zigzag(lines, d))
        variants.append(options)
    order = list(range(len(runs)))
    first = order[-1] if start_from_end else order[0]
    path = list(variants[first][2 if start_from_end else 0])
    remaining = [i for i in order if i != first]
    while remaining:
        end = path[-1]
        laid = LineString(path) if len(path) > 1 else None
        candidates = sorted(((math.dist(end, v[0]), i, v) for i in remaining for v in variants[i]),
                            key=lambda t: t[0])
        # Nearest run start whose connector neither leaves the part nor
        # crosses fibre -- laid already, or still to come in the other runs;
        # the nearest overall if none is clean (validation then reports it).
        chosen = None
        for _, i, v in candidates:
            link = LineString([tuple(end)] + _connector(end, v[0], poly, gap))
            others = [LineString(variants[k][0]) for k in remaining if k != i]
            if all(g is None or not link.crosses(g) for g in [laid] + others):
                chosen = (i, v, list(link.coords))
                break
        if chosen is None:
            _, i, v = candidates[0]
            chosen = (i, v, [tuple(end)] + _connector(end, v[0], poly, gap))
        i, v, link = chosen
        path.extend(link[1:-1])
        path.extend(v)
        remaining.remove(i)
    return path


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

    runs = _boustrophedon_runs(scan, rot_poly.buffer(1e-6))
    if len(runs) <= 1:
        # Convex (one segment per line, each overlapping the last): the
        # original single zigzag.
        lines = runs[0] if runs else []
        path = _zigzag(list(reversed(lines)) if start_from_end else lines,
                       -1 if start_from_end else 1)
    else:
        path = _join_runs(runs, rot_poly, gap, start_from_end)

    if not path:
        return []
    final_line = LineString(path)
    final_line = rotate(final_line, angle, origin=pivot)
    return list(final_line.coords)
