"""One flat laminate layer: wall + infill, stitched into one stroke (spec §5.3)."""
from __future__ import annotations

import math

from ..config import ARCHIMEDEAN, FERMAT
from ._common import check_gap_radius, dedupe_path, largest_polygon
from .archimedean import archimedean_path
from .fermat import fermat_spiral_path
from .outlines import printable_boundary
from .serpentine import oriented_serpentine


def laminate_layer(base_poly, track_gap, min_radius, infill_type, tracks_per_layer,
                   include_boundary, angle, layer_idx=0, last_pt=None):
    """2D path for one layer of a flat part.

    Even layers spiral outside-in, odd layers inside-out. With
    `include_boundary` the wall loop is joined to the infill (wall loop
    rotated to start nearest the join point; only the zigzag pattern may be
    reversed, never a spiral). `last_pt` (the previous layer's end) is
    prepended to keep the stroke continuous.
    """
    check_gap_radius(track_gap, min_radius)
    gap = track_gap
    min_rad = min_radius

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
            infill_bounds, gap, min_rad, track_count=tracks_per_layer, start_from_inside=is_outward
        )
    elif infill_type == FERMAT:
        # Phase-shift consecutive seams ~180° apart so the Z-ramp between
        # layers has a horizontal run ≈ the infill diameter rather than ~gap.
        fermat_hint = None
        if last_pt is not None:
            cx = infill_bounds.centroid.x
            cy = infill_bounds.centroid.y
            fermat_hint = (2.0 * cx - last_pt[0], 2.0 * cy - last_pt[1])
        infill_path = fermat_spiral_path(
            infill_bounds, gap, min_rad, track_count=tracks_per_layer,
            start_from_inside=is_outward, entry_hint=fermat_hint,
        )
    else:
        infill_path = oriented_serpentine(infill_bounds, angle, gap, min_rad, start_from_end=is_outward)

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

        peel_pt = continuous_path[-1]
        # Both spirals keep one monotonic winding direction end to end, so
        # only the plain zigzag pattern gets end-swapped here.
        if infill_type not in (ARCHIMEDEAN, FERMAT):
            d_start = math.hypot(infill_path[0][0] - peel_pt[0], infill_path[0][1] - peel_pt[1])
            d_end = math.hypot(infill_path[-1][0] - peel_pt[0], infill_path[-1][1] - peel_pt[1])
            if d_end < d_start:
                infill_path.reverse()

        continuous_path.extend(infill_path)

    return dedupe_path(continuous_path)
