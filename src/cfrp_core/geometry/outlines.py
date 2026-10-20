"""Part outlines (spec §5.1) and the printable wall boundary (spec §5.2)."""
from __future__ import annotations

import math

from shapely.affinity import translate
from shapely.geometry import Point, Polygon
from shapely.geometry.polygon import orient

from ..config import GeometryConfig
from ._common import fillet, largest_polygon


def base_polygon(geometry: GeometryConfig, track_gap, min_radius):
    """The part outline for a flat shape, translated to (start_x, start_y)."""
    shape, w, h = geometry.shape, geometry.width_mm, geometry.height_mm
    if shape == "Square/Rectangle":
        poly = Polygon([(0, 0), (w, 0), (w, h), (0, h)])
    elif shape == "Triangle":
        poly = Polygon([(0, 0), (w, 0), (w / 2.0, h)])
    elif shape == "Hexagon":
        r = w / 2.0
        cx, cy = r, r
        poly = Polygon([(cx + r * math.cos(math.radians(60 * i)), cy + r * math.sin(math.radians(60 * i))) for i in range(6)])
    elif shape == "Circle":
        poly = Point(w / 2.0, h / 2.0).buffer(w / 2.0)
    elif shape == "Custom Outline":
        # drill-after holes are printed over (listed to drill after curing)
        holes = [hole for k, hole in enumerate(geometry.custom_holes, start=1)
                 if len(hole) >= 3 and k not in geometry.drill_holes]
        poly = orient(largest_polygon(Polygon(geometry.custom_outline, holes).buffer(0)), 1.0)
    else:
        poly = Polygon()

    return translate(poly, xoff=geometry.start_x_mm, yoff=geometry.start_y_mm).buffer(0)


def drawn_holes(geometry: GeometryConfig) -> dict:
    """{hole number: Polygon} of a Custom Outline's holes as drawn (1 =
    first), placed like the outline; drill-after holes left out."""
    if geometry.shape != "Custom Outline":
        return {}
    out = {}
    for k, hole in enumerate(geometry.custom_holes, start=1):
        if len(hole) >= 3 and k not in geometry.drill_holes:
            poly = Polygon(hole).buffer(0)
            if not poly.is_empty:
                out[k] = translate(poly, xoff=geometry.start_x_mm, yoff=geometry.start_y_mm)
    return out


def printable_boundary(base_poly, track_gap, min_radius):
    """The wall track's centreline: half a gap inside the outline, with
    every corner rounded to at least `min_radius`."""
    gap = float(track_gap)
    min_rad = float(min_radius)
    boundary = base_poly.buffer(-gap / 2.0, join_style=1)
    if boundary.is_empty or min_rad <= 0:
        return boundary

    if boundary.geom_type == 'MultiPolygon':
        # Holes or narrow necks split the wall centreline into pieces (e.g. a
        # board whose pad windows nearly touch): round each piece.
        from shapely.ops import unary_union
        pieces = [fillet(Polygon(g.exterior), min_rad) for g in boundary.geoms if not g.is_empty]
        rounded = unary_union([g for g in pieces if not g.is_empty])
    else:
        rounded = fillet(boundary, min_rad)
    if rounded.is_empty:
        return boundary
    # fillet works on the outer boundary only; put the holes back (a slice of
    # a tube or pen holder kept its outer disc but lost its bore).
    holes = [Polygon(ring) for g in getattr(boundary, 'geoms', [boundary]) for ring in g.interiors]
    for hole in holes:
        rounded = rounded.difference(hole)
    return rounded
