"""One flat laminate layer: wall + infill, stitched into one stroke (spec §5.3)."""
from __future__ import annotations

import math

from shapely.geometry import LineString

from ..config import ARCHIMEDEAN, CORNER_STEP, DIAGONAL_45, FERMAT
from ._common import arc_along_ring, check_gap_radius, dedupe_path, largest_polygon
from .archimedean import archimedean_path
from .corner_step import corner_step_path
from .fermat import fermat_spiral_path
from .outlines import printable_boundary
from .serpentine import oriented_serpentine


def _nearest_vertex(coords, point):
    return min(coords, key=lambda c: math.hypot(c[0] - point[0], c[1] - point[1]))


def _entry_route(entry_from, path, corridor):
    """Points along `corridor`'s ring from entry_from towards path[0], used
    only when the straight jump entry_from -> path[0] crosses `path` and the
    detour crosses it less (a corridor narrower than the U-turns would cut
    through them instead)."""
    if corridor.is_empty or corridor.geom_type != 'Polygon' or len(path) < 3:
        return []
    line = LineString(path)
    straight = LineString([entry_from, path[0]])
    if not straight.crosses(line):
        return []
    route = arc_along_ring(corridor, entry_from, path[0])
    if _crossing_count(LineString(route + [path[0]]), line) < _crossing_count(straight, line):
        return route
    return []


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


def laminate_layer(base_poly, track_gap, min_radius, infill_type, tracks_per_layer,
                   include_boundary, angle, layer_idx=0, last_pt=None, last_dir=None):
    """2D path for one layer of a flat part.

    Even layers spiral outside-in, odd layers inside-out. With
    `include_boundary` the wall loop is joined to the infill (wall loop
    rotated to start nearest the join point; only the zigzag pattern may be
    reversed, never a spiral). `last_pt` (the previous layer's end) is
    prepended to keep the stroke continuous. `last_dir` (unit direction the
    previous layer ended with) lets the Fermat spiral pick the winding that
    continues it rather than reversing at the seam.
    """
    check_gap_radius(track_gap, min_radius)
    gap = track_gap
    min_rad = min_radius

    # Corner-Step Spiral: ring 0 IS the boundary — bypass wall/boundary logic.
    if infill_type == CORNER_STEP:
        path, _ = corner_step_path(base_poly, gap, min_rad, tracks_per_layer)
        if not path:
            return []
        if layer_idx % 2 != 0:
            path = list(reversed(path))
        if last_pt:
            path = [last_pt] + path
        return path

    wall_poly = printable_boundary(base_poly, gap, min_rad)
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

    if infill_type == ARCHIMEDEAN:
        infill_path, _ = archimedean_path(
            infill_bounds, gap, min_rad, track_count=tracks_per_layer, start_from_inside=is_outward,
            seam_hint=last_pt if is_outward else None,
        )
    elif infill_type == FERMAT:
        # Enter where the previous layer ended. (Entering diametrically
        # opposite, as before, left the jump from the previous end as a
        # straight chord across the whole layer at layer Z, which every ring
        # of the layer then crossed.)
        variants = [
            fermat_spiral_path(
                infill_bounds, gap, min_rad, track_count=tracks_per_layer,
                start_from_inside=is_outward, entry_hint=last_pt, mirror=mirror,
            )
            for mirror in ((False, True) if last_dir is not None else (False,))
        ]
        infill_path = max(variants, key=lambda p: _start_alignment(p, last_dir))
    else:
        # Zigzags. Fixed 45° hatching runs +45°/-45° on alternate layers
        # regardless of the layup angle (balanced cross-ply).
        if infill_type == DIAGONAL_45:
            angle = 45.0 if layer_idx % 2 == 0 else -45.0
        infill_path = oriented_serpentine(infill_bounds, angle, gap, min_rad, start_from_end=is_outward)
        # Where the stroke enters the zigzag: the previous layer's end, or the
        # end of the wall loop when the wall is printed first.
        if include_boundary and not is_outward:
            entry_from = _nearest_vertex(wall_coords, last_pt) if last_pt else wall_coords[0]
        else:
            entry_from = last_pt
        if entry_from is not None and infill_path:
            # Start at the zigzag corner nearest the entry point...
            other = oriented_serpentine(infill_bounds, angle, gap, min_rad, start_from_end=not is_outward)
            candidates = [infill_path, infill_path[::-1], other, other[::-1]]
            infill_path = min(candidates, key=lambda p: math.hypot(p[0][0] - entry_from[0], p[0][1] - entry_from[1]))
            # ...and if the straight jump to it would still cut across the
            # lines, run along a ring between the outline and the line ends
            # (the lines are eroded by min_rad, so the corridor is clear).
            corridor = wall_poly.buffer(-gap, join_style=1) if include_boundary else wall_poly
            infill_path = _entry_route(entry_from, infill_path, largest_polygon(corridor)) + infill_path

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
        continuous_path.extend(infill_path)

        peel_pt = continuous_path[-1]
        distances = [math.hypot(x - peel_pt[0], y - peel_pt[1]) for x, y in wall_coords]
        min_idx = distances.index(min(distances))
        wall_reordered = wall_coords[min_idx:] + wall_coords[:min_idx] + [wall_coords[min_idx]]
        continuous_path.extend(wall_reordered)

    else:
        if last_pt:
            distances = [math.hypot(x - last_pt[0], y - last_pt[1]) for x, y in wall_coords]
            min_idx = distances.index(min(distances))
            wall_reordered = wall_coords[min_idx:] + wall_coords[:min_idx] + [wall_coords[min_idx]]
            continuous_path.append(last_pt)
        else:
            wall_reordered = wall_coords

        continuous_path.extend(wall_reordered)

        # Zigzags already start nearest the wall's end (chosen above); both
        # spirals keep one monotonic winding direction end to end.
        continuous_path.extend(infill_path)

    return dedupe_path(continuous_path)
