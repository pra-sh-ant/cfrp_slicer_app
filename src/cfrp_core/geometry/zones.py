"""Layer recipe (Phase 44): edge rings, a core pattern and wraps round holes,
joined into one stroke per layer.

An inward layer: edge rings spiral in -> the core (a zigzag or a Fermat
spiral, which start and end on their zone's edge) -> a spiral into the
chosen hole's rings, ending on the innermost ring. The next layer climbs
there and spirals out of the same hole the same way round -> core -> edge
rings outwards, ending at the edge. So a wrapped hole gets full laps of
fibre in two layers; the climb between them follows its innermost ring.

A path that goes all the way round a hole shuts itself in, so full wraps
are only possible where a layer ends or starts; elsewhere the core passes
holes as it always has. In a layer that wraps no hole the core fills round
every hole as before. Lengths in mm.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import shapely
import shapely.affinity
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.polygon import orient

from ._common import blend_chain, count_crossings, dedupe_path, largest_polygon, resample_polar_ring
from .archimedean import archimedean_path
from .strict import smooth_join

WRAP_RADIUS_FACTOR = 1.05     # innermost wrap ring at least this x r_min (strict climbs need >= 1.03)


@dataclass(frozen=True)
class HoleBand:
    """The rings round one hole, innermost first, and the band they fill."""
    index: int                 # 1-based, in the order the outline lists its holes
    hole: Polygon
    rings: tuple               # ring polygons (paths are their exteriors), innermost first
    band: Polygon              # the outer ring grown by half a gap
    centre: tuple


def hole_band(index, hole, gap, r_min, count):
    """`count` rings round `hole`, one gap apart, the innermost half a gap
    off it, every ring at least WRAP_RADIUS_FACTOR x r_min round (a ring
    round a small hole is pushed out to it). None if count < 1."""
    if count < 1 or hole.is_empty:
        return None
    first = hole.buffer(gap / 2.0, quad_segs=32)
    need = WRAP_RADIUS_FACTOR * r_min
    inner = shapely.maximum_inscribed_circle(first).length
    if inner < need:
        first = first.buffer(need - inner, quad_segs=32)
    rings = tuple(orient(Polygon(first.buffer(k * gap, quad_segs=32).exterior), 1.0) if k else
                  orient(Polygon(first.exterior), 1.0) for k in range(count))
    band = rings[-1].buffer(gap / 2.0, quad_segs=32)
    c = hole.centroid
    return HoleBand(index, hole, rings, band, (c.x, c.y))


def _samples(ring, step=0.5):
    return max(72, int(math.ceil(ring.exterior.length / step)))


def wrap_in(band: HoleBand, entry, ccw=True):
    """Spiral into the hole's rings: a full lap of the outer ring starting
    nearest `entry`, blended one gap inward per lap, ending with a full lap
    of the innermost ring (counter-clockwise if `ccw`)."""
    rings = list(band.rings)[::-1]                    # outer first
    n = max(_samples(r) for r in rings)
    pts = []
    start = entry
    for r in rings:
        ring = r if ccw else orient(Polygon(r.exterior), -1.0)
        p = resample_polar_ring(ring, n, band.centre, start)
        pts.append(p)
        start = p[0]
    first_lap = pts[0] + [pts[0][0]]
    if len(pts) == 1:
        return dedupe_path(first_lap)
    close = pts[-1][1:] + [pts[-1][0]]
    return dedupe_path(first_lap + blend_chain(pts, n) + close)


def wrap_out(band: HoleBand, start, ccw=True):
    """Spiral out of the hole's rings: a full lap of the innermost ring
    from the point nearest `start`, blended one gap outward per lap, ending
    with a full lap of the outer ring (counter-clockwise if `ccw`)."""
    rings = list(band.rings)                          # innermost first
    n = max(_samples(r) for r in rings)
    pts, at = [], start
    for r in rings:
        ring = r if ccw else orient(Polygon(r.exterior), -1.0)
        p = resample_polar_ring(ring, n, band.centre, at)
        pts.append(p)
        at = p[0]
    first_lap = pts[0] + [pts[0][0]]
    if len(pts) == 1:
        return dedupe_path(first_lap)
    close = pts[-1][1:] + [pts[-1][0]]
    return dedupe_path(first_lap + blend_chain(pts, n) + close)


def _turns_ccw(path, centre):
    """True if the path's last stretch turns counter-clockwise about centre."""
    (x0, y0), (x1, y1) = path[-2], path[-1]
    return (x0 - centre[0]) * (y1 - centre[1]) - (y0 - centre[1]) * (x1 - centre[0]) > 0.0


def _join(a, b, zone, rings, laid, gap, min_rad, ahead=None):
    """Points after `a` up to `b`: straight, or along one of `rings` (the
    edges of the zones, where no pattern reaches), whichever stays in
    `zone` and crosses `laid` (the layer so far) and `ahead` (the piece laid
    next, from `b`) least, then is shortest. The bends at its ends are
    rounded later (strict repair, or the radius smoothing)."""
    from .serpentine import _Roadmap, _routes
    area = zone.buffer(1e-6)
    options = [r for r in _routes(tuple(a), tuple(b), area, rings, reach=1e9)
               if area.covers(LineString([a] + r))]
    if not options:
        detour = _Roadmap(area, rings, max(min_rad, gap) + 1.5 * gap, max(gap, 0.75))(a, b)
        options = [detour] if detour else [[tuple(b)]]
    lines = [LineString(p) for p in (laid, ahead) if p is not None and len(p) > 1]

    def cost(r):
        seg = LineString([a] + r)
        hits = 0
        for line in lines:
            x = seg.intersection(line)
            hits += 0 if x.is_empty else len(getattr(x, 'geoms', [x])) - 1    # less the shared end
        return hits, seg.length
    return list(min(options, key=cost))


def _rings_of(*polys):
    out = []
    for p in polys:
        for g in getattr(p, 'geoms', [p]):
            if g is not None and g.geom_type == 'Polygon' and not g.is_empty:
                out.extend([g.exterior, *g.interiors])
    return out


def edge_path(shell_poly, gap, min_rad, count, outward, near=None, ccw=True):
    """`count` rings along the outline (bending round the holes of
    `shell_poly`), spiralling in from the outer ring (or out to it,
    `outward`), starting / ending nearest `near`, turning counter-clockwise
    (`ccw`) or clockwise (the zone mirrored, laid, and mirrored back)."""
    if count < 1:
        return []
    if not ccw:
        cx = shell_poly.centroid.x
        flip = shapely.affinity.scale(shell_poly, xfact=-1.0, yfact=1.0, origin=(cx, 0.0))
        hint = (2.0 * cx - near[0], near[1]) if near is not None else None
        return [(2.0 * cx - x, y) for x, y in edge_path(flip, gap, min_rad, count, outward, hint, True)]
    try:
        path, _ = archimedean_path(shell_poly, gap, min_rad, track_count=count, start_from_inside=outward,
                                   seam_hint=near if outward else None, outer_start=None if outward else near)
    except ValueError:
        path, _ = archimedean_path(shell_poly, gap, min_rad, track_count=None, start_from_inside=outward,
                                   seam_hint=near if outward else None, outer_start=None if outward else near)
    if near is not None and len(path) > 3 and math.dist(path[0], path[-1]) < 0.5 * gap:
        # one closed ring (archimedean_path does not phase a single ring):
        # start it at the point nearest `near`
        ring = path[:-1]
        k = min(range(len(ring)), key=lambda i: math.dist(ring[i], near))
        path = ring[k:] + ring[:k] + [ring[k]]
    return path


def _heading(path):
    """Unit direction of the path's last ~1 mm, or None."""
    if len(path) < 2:
        return None
    ex, ey = path[-1]
    for x, y in reversed(path[:-1]):
        n = math.hypot(ex - x, ey - y)
        if n >= 1.0:
            return ((ex - x) / n, (ey - y) / n)
    x, y = path[-2]
    n = math.hypot(ex - x, ey - y)
    return ((ex - x) / n, (ey - y) / n) if n > 1e-9 else None


def _start_dot(path, d):
    """cos between `d` and the direction `path` starts in (first ~1 mm)."""
    if d is None or len(path) < 2:
        return 0.0
    x0, y0 = path[0]
    for x, y in path[1:]:
        n = math.hypot(x - x0, y - y0)
        if n >= 1.0:
            return ((x - x0) * d[0] + (y - y0) * d[1]) / n
    return 0.0


def _edge_aligned(shell_poly, gap, min_rad, count, outward, near, arrive):
    """edge_path turning whichever way continues `arrive` (a direction)."""
    options = [edge_path(shell_poly, gap, min_rad, count, outward, near, ccw) for ccw in (True, False)]
    options = [p for p in options if p]
    if not options:
        return []
    return max(options, key=lambda p: _start_dot(p, arrive))


@dataclass
class RecipeLayer:
    path: list
    wrapped: int | None        # hole index this layer ends (inward) or starts (outward) wrapped round
    ccw: bool = True           # the way the wrap turns round its hole
    notes: tuple = ()
    parts: tuple = ()          # (name, first point, last point) of each piece, in `path` before dedupe


def recipe_layer(part, holes, gap, min_rad, edge_rings, core, layer_idx, last_pt=None, last_dir=None,
                 wrap_end=None, wrap_start=None, start_ccw=True, start_near=None):
    """One layer of the recipe on `part` (a Polygon with holes).

    `holes`: {index: hole Polygon} of the part's holes (drawing order).
    `core(poly, last_pt, last_dir, layer_idx, exit_to)`: the core pattern's
    path in `poly` (it prepends last_pt when given); `exit_to`, a line or
    None: where the stroke goes after the core (a zigzag picks a start
    corner whose end can get there without crossing its lines).
    Even `layer_idx`: inward -- edge rings in, core, then (`wrap_end`, a
    HoleBand) spiral into that hole. Odd: outward -- (`wrap_start`, a
    HoleBand; `start_ccw`: the way the last layer turned round it) spiral
    out of that hole, core, edge rings out. `start_near`: where a first
    layer (no `last_pt`) starts its edge rings. Returns a RecipeLayer."""
    outward = layer_idx % 2 == 1
    band = wrap_start if outward else wrap_end
    shell = Polygon(part.exterior)
    # The edge rings bend round the wrapped hole's band, and round the other holes.
    others = [h for k, h in holes.items() if band is None or k != band.index]
    keep = [band.band] if band is not None else []
    edge_poly = shell.difference(shapely.union_all(others + keep)) if (others or keep) else shell
    edge_poly = largest_polygon(edge_poly)
    core_poly = largest_polygon(shell.buffer(-edge_rings * gap, join_style=1)) if edge_rings else shell
    if others or keep:
        cut = others + ([_channel(band, core_poly, gap)] if band is not None else [])
        core_poly = largest_polygon(core_poly.difference(shapely.union_all(cut)))
    notes = []
    if core_poly.is_empty or core_poly.geom_type != 'Polygon':
        core_poly = None
        notes.append("no room for the core inside the edge and hole rings")

    path = [last_pt] if last_pt is not None else []
    # joins run along the zones' edges: the core zone's edge (the zigzag's
    # line ends stay a bend radius inside it), the part's margin
    zone = part.buffer(-0.4 * gap, join_style=1)
    rings = _rings_of(core_poly.buffer(0.5 * gap, join_style=1) if core_poly is not None else None,
                      part.buffer(-0.5 * gap, join_style=1))

    parts = []
    # where the stroke goes after the core: the wrapped hole's outer ring
    # (inward layers), the innermost edge ring (outward)
    inner_edge = largest_polygon(shell.buffer(-(edge_rings - 0.5) * gap, join_style=1)) if edge_rings else None
    edge_line = inner_edge.exterior if inner_edge is not None and inner_edge.geom_type == 'Polygon' else None

    def extend(points, name, area=zone):
        if not points:
            return
        if len(path) >= 2 and len(points) >= 2 and min_rad > 0:
            # a join that keeps the bend radius by construction (the end of
            # the last piece and the start of this one are cut back to make
            # room; a closed edge lap's end lies next to its own start)
            joined = smooth_join(path, points, min_rad, gap, area)
            if joined is not None:
                kept, curve, rest = joined
                path[:] = kept
                if parts:
                    parts[-1] = (parts[-1][0], parts[-1][1], min(parts[-1][2], len(path) - 1))
                parts.append(("join", len(path) - 1, len(path) + len(curve) - 1))
                path.extend(curve[:-1])
                parts.append((name, len(path), len(path) + len(rest) - 1))
                path.extend(rest)
                return
        if path:
            link = _join(path[-1], points[0], area, rings, path, gap, min_rad, ahead=points)[:-1]
            parts.append(("join", len(path) - 1, len(path) + len(link)))
            path.extend(link)
        parts.append((name, len(path), len(path) + len(points) - 1))
        path.extend(points)

    if not outward:
        # the joins before the wrap keep out of its band (the wrap, laid
        # last, would cross them)
        clear = zone.difference(band.band) if band is not None else zone
        edge = _edge_aligned(edge_poly, gap, min_rad, edge_rings, False,
                             last_pt if last_pt is not None else start_near, last_dir)
        extend(edge, "edge", clear)
        if core_poly is not None:
            body = core(core_poly, path[-1] if path else None, _heading(path) or last_dir, layer_idx,
                        band.rings[-1].exterior if band is not None else None)
            extend(body[1:] if path and body else body, "core", clear)
        ccw = True
        if band is not None:
            ccw = _turns_ccw(path, band.centre) if len(path) > 1 else True
            extend(wrap_in(band, path[-1] if path else band.centre, ccw=ccw), "wrap")
        return RecipeLayer(dedupe_path(path), band.index if band is not None else None, ccw, tuple(notes),
                           tuple(parts))

    if band is not None:
        # Start on the innermost ring where the last layer ended, turning the
        # same way, and spiral out.
        extend(wrap_out(band, last_pt if last_pt is not None else band.centre, ccw=start_ccw), "wrap")
    if core_poly is not None:
        body = core(core_poly, path[-1] if path else None, _heading(path) or last_dir, layer_idx, edge_line)
        extend(body[1:] if path and body else body, "core")
    edge = _edge_aligned(edge_poly, gap, min_rad, edge_rings, True, path[-1] if path else None, _heading(path))
    extend(edge, "edge")
    return RecipeLayer(dedupe_path(path), band.index if band is not None else None, start_ccw, tuple(notes),
                       tuple(parts))


def _channel(band: HoleBand, core_poly, gap):
    """The wrapped hole's band and a channel as wide as it out to the core
    zone's outer edge. The core's rings then bend round it as round a
    notch, so the core can reach the hole without crossing its own rings
    (a band left inside the core zone is ringed by the core's tracks)."""
    zone = band.band.buffer(gap / 2.0, quad_segs=32)
    outer = Polygon(core_poly.exterior)
    c = Point(band.centre)
    edge = outer.exterior.interpolate(outer.exterior.project(c))
    dx, dy = edge.x - c.x, edge.y - c.y
    n = math.hypot(dx, dy)
    if n < 1e-9:
        return zone
    reach = shapely.minimum_bounding_radius(zone)
    tip = Point(edge.x + dx / n * gap, edge.y + dy / n * gap).buffer(reach, quad_segs=32)
    return shapely.convex_hull(shapely.union_all([zone, tip]))


def wrap_order(holes, bolt=()):
    """Hole indices in the order they are wrapped: bolt holes first (as
    listed), then -- only when no hole is marked a bolt hole -- every hole
    in drawing order."""
    bolt = [k for k in bolt if k in holes]
    return bolt if bolt else sorted(holes)


def joins_cross(path):
    """Crossings in a recipe layer (for the tests and the notes)."""
    return count_crossings(path)
