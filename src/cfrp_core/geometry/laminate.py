"""One flat laminate layer: wall + infill, stitched into one stroke (spec §5.3)."""
from __future__ import annotations

import math

from shapely.geometry import LineString, Point

from ..config import ARCHIMEDEAN, CORNER_STEP, DIAGONAL_45, FERMAT
from ._common import (
    arc_along_ring,
    check_gap_radius,
    count_crossings,
    dedupe_path,
    hole_keepouts,
    inset_components,
    largest_polygon,
    notched_rings,
)
from .archimedean import archimedean_path
from .corner_step import corner_step_path, is_rectangle
from .fermat import fermat_spiral_path
from .outlines import printable_boundary
from .serpentine import centre_fill, oriented_serpentine


def _lane_change_length(gap, min_rad):
    """Length over which a one-gap sideways step bends at >= min_rad."""
    return 1.5 * math.sqrt(6.0 * gap * min_rad) if min_rad > 0 else gap


def _trim_end(points, length):
    """`points` with the last `length` mm of travel removed (cut point
    interpolated); unchanged if that would remove more than half of it."""
    line = LineString(points)
    if length <= 0 or line.length <= 2.0 * length:
        return points
    keep = line.length - length
    out, travelled = [points[0]], 0.0
    for a, b in zip(points[:-1], points[1:], strict=False):
        seg = math.dist(a, b)
        if travelled + seg >= keep:
            f = (keep - travelled) / seg if seg > 1e-12 else 0.0
            out.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
            return out
        out.append(b)
        travelled += seg
    return points


def wall_seam(wall_poly, near=None):
    """Where the wall loop starts/ends: the point of the wall nearest `near`
    (the stroke's current end), or, with none, the middle of its longest
    straight side, so the hand-over to the infill is not on a corner arc."""
    ring = wall_poly.exterior
    if near is not None:
        p = ring.interpolate(ring.project(Point(near)))
        return (p.x, p.y)
    coords = list(ring.coords)
    a, b = max(zip(coords[:-1], coords[1:], strict=False), key=lambda s: math.dist(*s))
    return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)


def ring_from(poly, start):
    """poly's exterior as a closed point list starting and ending at `start`
    (a point on it), keeping the exterior's direction."""
    ring = poly.exterior
    d0 = ring.project(Point(start))
    coords = list(ring.coords)
    cum = [0.0]
    for a, b in zip(coords[:-1], coords[1:], strict=False):
        cum.append(cum[-1] + math.dist(a, b))
    after = [c for c, d in zip(coords[:-1], cum[:-1], strict=False) if d > d0 + 1e-9]
    before = [c for c, d in zip(coords[:-1], cum[:-1], strict=False) if d < d0 - 1e-9]
    return [tuple(start)] + after + before + [tuple(start)]


def _entry_route(entry_from, path, corridor, allowed=None, gap=1.0, min_rad=0.0, wide=False):
    """Points along `corridor`'s ring from entry_from towards path[0], used
    only when the straight jump entry_from -> path[0] crosses `path` and the
    detour crosses it less (a corridor narrower than the U-turns would cut
    through them instead). With holes, a route must stay inside `allowed`
    (where fibre may lie; default the corridor).

    `wide` (lines 2·r_min apart): their U-turns reach out to the corridor,
    so a route along `allowed`'s own ring (the wall loop just laid,
    retraced) is tried too, and kept if it crosses fewer lines."""
    route = _corridor_route(entry_from, path, corridor, allowed, gap, min_rad)
    if not wide or allowed is None or allowed.is_empty or allowed.geom_type != 'Polygon' or len(path) < 3:
        return route
    line = LineString(path)
    along = arc_along_ring(allowed, entry_from, path[0])
    if _crossing_count(LineString([tuple(entry_from)] + along + [path[0]]), line) < \
            _crossing_count(LineString([tuple(entry_from)] + [tuple(q) for q in route] + [path[0]]), line):
        return along
    return route


def _corridor_route(entry_from, path, corridor, allowed, gap, min_rad):
    """_entry_route along the corridor (or the margin rings round holes)."""
    if corridor.is_empty or corridor.geom_type != 'Polygon' or len(path) < 3:
        return []
    line = LineString(path)
    straight = LineString([entry_from, path[0]])
    if len(corridor.interiors):
        # Holes: the straight jump may span one; route like the zigzag's own
        # run connectors (straight or along any margin ring, both ways),
        # keeping only routes that stay on the part -- a hop from the entry
        # point onto a ring across the plate ran over a hole between --
        # else the shortest way on the rings' roadmap.
        region = corridor.buffer(1e-6)
        zone = (allowed if allowed is not None else corridor).buffer(1e-6)
        if not straight.crosses(line) and zone.covers(straight):
            return []
        from .serpentine import _margin_rings, _Roadmap, _routes
        rings = _margin_rings(corridor, 0.0)
        options = [r for r in _routes(tuple(entry_from), path[0], region, rings, reach=1e9)
                   if zone.covers(LineString([entry_from] + r))]
        if not options:
            detour = _Roadmap(zone, rings, max(min_rad, gap) + 1.5 * gap, max(gap, 0.75))(entry_from, path[0])
            options = [detour] if detour else []
        if not options:
            return []
        best = min(options, key=lambda r: (_crossing_count(LineString([entry_from] + r), line),
                                           LineString([entry_from] + r).length))
        return best[:-1]
    if not straight.crosses(line):
        return []
    route = arc_along_ring(corridor, entry_from, path[0])
    if _crossing_count(LineString(route + [path[0]]), line) < _crossing_count(straight, line):
        return route
    return []


def _with_centre(path, region, gap, outward, last_pt, again=None, line=None):
    """`path` with the hollow in its middle filled: after an inward spiral
    ends there; or, before an outward one, starting next to where the last
    layer ended (so the climb does not cut across the new lines), the spiral
    re-started (`again(hint)`) where the fill ends. Not on a first layer
    that starts in the middle (its anchor does), and not where the way in
    would cross the new lines (that layer keeps its middle open).
    `line`: Strict Bend Radius, one line of the fill (centre_fill)."""
    if not outward:
        return dedupe_path(list(path) + centre_fill(path, region, gap, at_end=True, line=line))
    if last_pt is None:
        return path
    fill = centre_fill(path, region, gap, anchor=last_pt, line=line)
    if not fill:
        return path
    spiral = (again(fill[-1]) if again is not None else None) or path
    joined = dedupe_path(fill + list(spiral))
    if count_crossings([tuple(last_pt)] + joined) > count_crossings([tuple(last_pt)] + list(path)):
        return path
    return joined


def _crossing_count(seg, line):
    """Pieces where seg meets line, not counting their shared end path[0]."""
    hit = seg.intersection(line)
    return 0 if hit.is_empty else len(getattr(hit, 'geoms', [hit])) - 1


def _start_alignment(path, direction):
    """cos of the angle between `direction` and the path's first ~1 mm."""
    if direction is None or len(path) < 2:
        return 0.0
    x0, y0 = path[0]
    for x, y in path[1:]:
        dx, dy = x - x0, y - y0
        n = math.hypot(dx, dy)
        if n >= 1.0:
            return (dx * direction[0] + dy * direction[1]) / n
    dx, dy = path[-1][0] - x0, path[-1][1] - y0
    n = math.hypot(dx, dy)
    return (dx * direction[0] + dy * direction[1]) / n if n > 1e-9 else 0.0


def max_track_count(base_poly, track_gap, min_radius, infill_type, include_boundary):
    """How many tracks fit for the patterns whose track count is exact
    (Archimedean rings, analytic Corner-Step rectangle); None when the
    pattern has no hard limit (it fills what fits)."""
    gap = float(track_gap)
    if infill_type == ARCHIMEDEAN:
        bounds = base_poly
        if include_boundary:
            wall = printable_boundary(base_poly, gap, min_radius)
            bounds = largest_polygon(wall.buffer(-gap / 2.0, join_style=1))
        if bounds.is_empty:
            return 0
        bounds = largest_polygon(bounds)
        holes = hole_keepouts(bounds)
        count, inset = 0, gap / 2.0
        while count < 1000 and inset_components(bounds, inset, min_radius, holes, gap / 2.0):
            count += 1
            inset += gap
        if holes:
            # archimedean_path bends rings round the holes when they stop it
            count = max(count, len(notched_rings(bounds, gap, min_radius)))
        return count
    if infill_type == CORNER_STEP and is_rectangle(base_poly):
        x0, y0, x1, y1 = base_poly.bounds
        return max(0, int((min(x1 - x0, y1 - y0) - 2.0 * min_radius + gap) / (2.0 * gap) + 1e-9))
    return None


def laminate_layer(base_poly, track_gap, min_radius, infill_type, tracks_per_layer,
                   include_boundary, angle, layer_idx=0, last_pt=None, last_dir=None, lead_in=0.0,
                   seam_turn=0.0, line_spacing=None, line_offset=0.0, fill_centre=False, strict=False,
                   exit_to=None):
    """2D path for one layer of a flat part.

    Even layers spiral outside-in, odd layers inside-out. With
    `include_boundary` the wall loop is joined to the infill (wall loop
    rotated to start nearest the join point; only the zigzag pattern may be
    reversed, never a spiral). `last_pt` (the previous layer's end) is
    prepended to keep the stroke continuous. `last_dir` (unit direction the
    previous layer ended with) lets the Fermat spiral pick the winding that
    continues it rather than reversing at the seam. `seam_turn` (laps):
    Archimedean only, twist the spiral so the layer ends that much further
    round (seam stagger between layers). `line_spacing` / `line_offset`:
    zigzags only, lines that far apart, shifted that much (turns at the bend
    radius; see oriented_serpentine). `fill_centre`: Archimedean and
    Corner-Step, fill the hollow the rings leave in the middle
    (serpentine.centre_fill; not on an inside-out first layer, which starts
    there with its anchor). `strict` (Strict Bend Radius): Fill Centre lays
    one line per layer, moving across the hollow from layer to layer.
    `exit_to` (a line; the layer recipe): where the stroke goes after this
    path. A zigzag then also counts the lines its end would cross on the
    way there when it picks its start corner.
    """
    check_gap_radius(track_gap, min_radius)
    gap = track_gap
    min_rad = min_radius

    # Corner-Step Spiral: ring 0 IS the boundary — bypass wall/boundary logic.
    if infill_type == CORNER_STEP:
        # Odd layers run outward with the same winding, from where the
        # inward layer ended (no fibre reversal at the seam).
        path, _ = corner_step_path(base_poly, gap, min_rad, tracks_per_layer,
                                   outward=layer_idx % 2 != 0,
                                   corner_lead=layer_idx % 2 == 0 and last_pt is not None,
                                   seam_hint=last_pt, lead_in=lead_in)
        if not path:
            return []
        if fill_centre:
            def again(hint):
                return corner_step_path(base_poly, gap, min_rad, tracks_per_layer, outward=True,
                                        seam_hint=hint)[0]
            path = _with_centre(path, base_poly, gap, layer_idx % 2 != 0, last_pt, again,
                                line=layer_idx if strict else None)
        if last_pt:
            path = [last_pt] + path
        return path

    wall_poly = printable_boundary(base_poly, gap, min_rad)
    if wall_poly.geom_type == 'MultiPolygon':
        # Holes pinch the part into pieces: one stroke follows the largest.
        wall_poly = largest_polygon(wall_poly)
    if wall_poly.is_empty or wall_poly.geom_type != 'Polygon':
        return []
    wall_coords = list(wall_poly.exterior.coords)

    # Without boundary: spiral IS the whole fill — start at gap/2 from edge.
    # With boundary: the wall ring is already printed; space the first infill
    # ring exactly track_gap from it (gap/2 inside wall_poly → gap from wall).
    if include_boundary:
        infill_bounds = wall_poly.buffer(-gap / 2.0, join_style=1)
        if infill_bounds.is_empty:
            return wall_coords
        infill_bounds = largest_polygon(infill_bounds)
    else:
        infill_bounds = base_poly

    is_outward = (layer_idx % 2 != 0)
    # Wall first (even layers): it starts nearest the previous end and hands
    # over to the infill at that same seam.
    wall_first = include_boundary and not is_outward
    wall_start = wall_seam(wall_poly, last_pt) if wall_first else None

    if infill_type == ARCHIMEDEAN:
        infill_path, _ = archimedean_path(
            infill_bounds, gap, min_rad, track_count=tracks_per_layer, start_from_inside=is_outward,
            seam_hint=last_pt if is_outward else None, outer_start=wall_start,
            lead_in=lead_in if is_outward and not include_boundary else 0.0, seam_turn=seam_turn,
        )
        # Outside-in after another layer: the outer ring starts at its first
        # vertex, which on a sliced mesh can be anywhere; if that is not where
        # the last layer ended, start there instead (else the jump is a chord
        # across the rings).
        if (last_pt is not None and not is_outward and wall_start is None and infill_path
                and math.dist(infill_path[0], last_pt) > 2.0 * gap):
            infill_path, _ = archimedean_path(
                infill_bounds, gap, min_rad, track_count=tracks_per_layer, outer_start=last_pt,
                seam_turn=seam_turn)
        if fill_centre and infill_path:
            def again(hint):
                return archimedean_path(infill_bounds, gap, min_rad, track_count=tracks_per_layer,
                                        start_from_inside=True, seam_hint=hint, seam_turn=seam_turn)[0]
            infill_path = _with_centre(infill_path, infill_bounds, gap, is_outward, last_pt, again,
                                       line=layer_idx if strict else None)
    elif infill_type == FERMAT:
        # Enter where the previous layer ended. (Entering diametrically
        # opposite, as before, left the jump from the previous end as a
        # straight chord across the whole layer at layer Z, which every ring
        # of the layer then crossed.)
        # After a wall loop, enter at the wall's seam going the wall's way.
        entry, entry_dir = last_pt, last_dir
        if wall_first:
            wall_loop = ring_from(wall_poly, wall_start)
            entry = wall_start
            dx, dy = wall_loop[-1][0] - wall_loop[-2][0], wall_loop[-1][1] - wall_loop[-2][1]
            n = math.hypot(dx, dy)
            entry_dir = (dx / n, dy / n) if n > 1e-9 else None
        variants = [
            fermat_spiral_path(
                infill_bounds, gap, min_rad, track_count=tracks_per_layer,
                start_from_inside=is_outward, entry_hint=entry, mirror=mirror,
            )
            for mirror in ((False, True) if entry_dir is not None else (False,))
        ]
        infill_path = max(variants, key=lambda p: _start_alignment(p, entry_dir))
    else:
        # Zigzags. Fixed 45° hatching runs +45°/-45° on alternate layers
        # regardless of the layup angle (balanced cross-ply).
        if infill_type == DIAGONAL_45:
            angle = 45.0 if layer_idx % 2 == 0 else -45.0
        infill_path = oriented_serpentine(infill_bounds, angle, gap, min_rad, start_from_end=is_outward,
                                          spacing=line_spacing, offset=line_offset)
        # Where the stroke enters the zigzag: the previous layer's end, or the
        # end of the wall loop when the wall is printed first.
        if wall_first:
            entry_from = wall_start
        else:
            entry_from = last_pt
        if entry_from is not None and infill_path:
            # Start at the zigzag corner nearest the entry point...
            other = oriented_serpentine(infill_bounds, angle, gap, min_rad, start_from_end=not is_outward,
                                        spacing=line_spacing, offset=line_offset)
            candidates = [infill_path, infill_path[::-1], other, other[::-1]]
            # Distance, plus a penalty for folding the fibre back on the
            # direction it arrived with (a slightly farther corner that
            # continues the direction is better than a 180° reversal).
            arrive = last_dir if not wall_first else None

            def cost(p):
                d = math.hypot(p[0][0] - entry_from[0], p[0][1] - entry_from[1])
                return d + 5.0 * max(min_rad, gap) * max(0.0, -_start_alignment(p, arrive))
            # ...and if the straight jump to it would still cut across the
            # lines, run along a ring between the outline and the line ends
            # (the lines are eroded by min_rad, so the corridor is clear).
            corridor = largest_polygon(wall_poly.buffer(-gap, join_style=1) if include_boundary else wall_poly)

            def entry(p):
                route = _entry_route(entry_from, p, corridor, allowed=wall_poly, gap=gap, min_rad=min_rad,
                                     wide=line_spacing is not None)
                hits = (count_crossings([tuple(entry_from)] + [tuple(q) for q in route] + list(p))
                        - count_crossings(p)) if holed else 0
                if exit_to is not None:
                    # the way out: an end boxed in by the route in (or by the
                    # lines) crosses them to get there
                    q = exit_to.interpolate(exit_to.project(Point(p[-1])))
                    hits += _crossing_count(LineString([p[-1], (q.x, q.y)]),
                                            LineString([tuple(entry_from)] + [tuple(c) for c in route] + list(p)))
                return (hits, cost(p)), route
            # On a part with holes: fewest lines crossed on the way in, then
            # the nearest corner. (A layer that ended by a hole, mid-part,
            # entered the nearest corner across the new layer's lines.)
            # (also for widely spaced lines: a start corner on the last
            # layer's end leads the climb across the first line)
            holed = bool(len(getattr(wall_poly, 'interiors', ()))) or line_spacing is not None
            if holed or exit_to is not None:
                (_, route), infill_path = min(((entry(p), p) for p in candidates), key=lambda e: e[0][0])
            else:
                infill_path = min(candidates, key=cost)
                route = entry(infill_path)[1]
            infill_path = route + infill_path

    if not infill_path:
        return wall_coords if include_boundary else []

    if not include_boundary:
        if last_pt:
            # The layer index already determines the intended start side.
            # Never reverse this path: doing so breaks the outer/inner
            # alternation and changes the spiral winding direction.
            return [last_pt] + infill_path
        return infill_path

    continuous_path = []

    if is_outward:
        if last_pt:
            continuous_path.append(last_pt)
        # Lane change out to the wall: stop the infill L short of its end and
        # start the wall where the infill would have ended (see wall_first).
        # Only spirals end running along the wall; a zigzag ends on a line.
        peel_pt = infill_path[-1]
        wall_reordered = ring_from(wall_poly, wall_seam(wall_poly, peel_pt))
        if infill_type in (ARCHIMEDEAN, FERMAT):
            trimmed = _trim_end(infill_path, _lane_change_length(gap, min_rad))
            if not LineString([trimmed[-1], wall_reordered[0]]).crosses(LineString(trimmed)):
                infill_path = trimmed
        continuous_path.extend(infill_path)
        continuous_path.extend(wall_reordered)

    else:
        if last_pt:
            continuous_path.append(last_pt)
        wall_reordered = ring_from(wall_poly, wall_start)

        # Lane change into the infill: when it starts at the wall's seam,
        # stop the wall loop L short so the one-gap sideways step is spread
        # over L (bend ~ L²/(6·gap) >= min radius) instead of a sharp jog.
        if math.dist(infill_path[0], wall_reordered[0]) < 3.0 * gap:
            trimmed = _trim_end(wall_reordered, _lane_change_length(gap, min_rad))
            # Not when the seam sits on a corner: the shortened wall then ends
            # round the corner and the step would cut across the infill.
            if not LineString([trimmed[-1], infill_path[0]]).crosses(LineString(infill_path)):
                wall_reordered = trimmed
        continuous_path.extend(wall_reordered)

        # Zigzags already start nearest the wall's end (chosen above); both
        # spirals keep one monotonic winding direction end to end.
        continuous_path.extend(infill_path)

    return dedupe_path(continuous_path)
