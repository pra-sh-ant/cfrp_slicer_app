"""Part outlines (spec §5.1) and the printable wall boundary (spec §5.2)."""
from __future__ import annotations

import math

from shapely.affinity import translate
from shapely.geometry import Point, Polygon

from ..config import GeometryConfig
from ._common import fillet


def serpentine_print_extent(width, height, min_radius, track_gap, invert_bends=False):
    """The exact zigzag geometry -- X range, line spacing, and line count --
    that `serpentine_infill` walks. Shared with `base_polygon` so the
    boundary shown in the viewer can never drift from the printed path.

        line_gap  = max(gap, 2·r_min)
        width     = w − 2·r_min   (or w when inverted)
        num_lines = ⌊h / line_gap⌋
    """
    line_gap = max(track_gap, 2.0 * min_radius)
    effective_w = width - (min_radius * 2) if not invert_bends else width
    start_x = min_radius if not invert_bends else 0.0
    end_x = start_x + effective_w
    num_lines = int(height / line_gap)
    return start_x, end_x, line_gap, num_lines


def base_polygon(geometry: GeometryConfig, track_gap, min_radius):
    """The part outline for a flat shape, translated to (start_x, start_y)."""
    shape, w, h = geometry.shape, geometry.width_mm, geometry.height_mm
    if shape == "Square/Rectangle":
        poly = Polygon([(0, 0), (w, 0), (w, h), (0, h)])
    elif shape == "Serpentine Infill":
        start_x, end_x, line_gap, num_lines = serpentine_print_extent(
            w, h, min_radius, track_gap, geometry.invert_bends
        )
        end_y = num_lines * line_gap
        # Only the end_x-side U-turn bulges past its anchor (by its own
        # radius, line_gap/2); the start_x-side turn curves back inward.
        pad = line_gap / 2.0 if num_lines >= 1 else 0.0
        poly = Polygon([
            (start_x, 0), (end_x + pad, 0),
            (end_x + pad, end_y), (start_x, end_y),
        ])
    elif shape == "Triangle":
        poly = Polygon([(0, 0), (w, 0), (w / 2.0, h)])
    elif shape == "Hexagon":
        r = w / 2.0
        cx, cy = r, r
        poly = Polygon([(cx + r * math.cos(math.radians(60 * i)), cy + r * math.sin(math.radians(60 * i))) for i in range(6)])
    elif shape == "Circle":
        poly = Point(w / 2.0, h / 2.0).buffer(w / 2.0)
    else:
        poly = Polygon()

    return translate(poly, xoff=geometry.start_x_mm, yoff=geometry.start_y_mm).buffer(0)


def printable_boundary(base_poly, track_gap, min_radius):
    """The wall track's centreline: half a gap inside the outline, with
    every corner rounded to at least `min_radius`."""
    gap = float(track_gap)
    min_rad = float(min_radius)
    boundary = base_poly.buffer(-gap / 2.0, join_style=1)
    if boundary.is_empty or min_rad <= 0:
        return boundary

    rounded = fillet(boundary, min_rad)
    return rounded if not rounded.is_empty else boundary
