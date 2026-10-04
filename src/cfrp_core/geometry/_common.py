"""Shared polygon/path helpers for the path generators. Lengths in mm."""
from __future__ import annotations

import math

from shapely.geometry import LineString, Point, Polygon


def check_gap_radius(track_gap: float, min_radius: float) -> None:
    """The one shared guard every path generator runs before looping on
    `track_gap` -- a value <= 0 would never advance the offset loops."""
    if track_gap <= 0:
        raise ValueError(f"Track Gap must be greater than 0 mm (got {track_gap}).")
    if min_radius < 0:
        raise ValueError(f"Min Bend Radius cannot be negative (got {min_radius}).")


def largest_polygon(geom):
    """The single biggest Polygon in geom (or geom itself if it already is one)."""
    if geom.geom_type == 'MultiPolygon':
        return max(geom.geoms, key=lambda g: g.area)
    return geom


def fillet(poly, min_radius: float):
    """Morphological opening: shrink by r then grow by r. Corners sharper
    than radius r can't survive the shrink, so they come back as arcs."""
    if min_radius <= 0:
        return poly
    return poly.buffer(-min_radius, join_style=1).buffer(min_radius, join_style=1)


def hole_keepouts(poly) -> list:
    """poly's holes as solid polygons (e.g. the bore of a hollow wall)."""
    if poly.is_empty or poly.geom_type != 'Polygon':
        return []
    return [Polygon(ring) for ring in poly.interiors]


def inset_components(poly, inset, min_radius, holes=(), clearance=0.0, area_eps=1.0):
    """One concentric ring step shared by both spiral generators: offset
    poly's OUTER boundary inward by `inset` and fillet it. Returns a list
    of Polygon components (2+ means the offset split), or None once it
    vanishes or its ring would come closer than `clearance` to a hole.

    Offsetting only the outer shell -- not the holed polygon itself -- is
    what lets rings march all the way across a hollow wall to the bore.
    Offsetting the annulus shrinks it from both edges at once, so it used
    to run out of room at the wall's midline and leave the inner half of
    every hollow-shell wall unprinted."""
    offset_poly = Polygon(poly.exterior).buffer(-inset, join_style=1)
    if offset_poly.is_empty or offset_poly.area < area_eps:
        return None
    filleted = fillet(offset_poly, min_radius)
    if filleted.is_empty:
        return None
    comps = list(filleted.geoms) if filleted.geom_type == 'MultiPolygon' else [filleted]
    comps = [c for c in comps if c.geom_type == 'Polygon' and c.area >= area_eps]
    if not comps:
        return None
    # 10% slack: shapely approximates round rings/bores as polygons, so a
    # ring sitting exactly `clearance` off a bore measures a hair under it.
    min_clearance = clearance * 0.9
    for comp in comps:
        if any(comp.exterior.distance(hole) < min_clearance for hole in holes):
            return None
    return comps


def dedupe_path(points, tol: float = 0.05):
    """Drop consecutive points closer than `tol` apart -- the blend/
    resample generators all produce a few near-duplicate points at
    ring-transition seams; collapsing them keeps kinematics planning
    and the viewer from choking on effectively-zero-length segments."""
    if not points:
        return []
    out = [points[0]]
    for pt in points[1:]:
        if math.hypot(pt[0] - out[-1][0], pt[1] - out[-1][1]) > tol:
            out.append(pt)
    return out


def resample_closed_ring(poly, samples: int, phase_start=None):
    """Resample poly's exterior into `samples` evenly arc-length-spaced
    points, starting as close as possible to phase_start so consecutive
    rings stay angularly aligned for blending."""
    line = LineString(list(poly.exterior.coords))
    length = line.length
    offset = line.project(Point(phase_start)) if phase_start is not None else 0.0
    return [
        (pt.x, pt.y)
        for pt in (line.interpolate((offset + i / samples * length) % length) for i in range(samples))
    ]


def blend_chain(rings, samples: int):
    """Continuous path threading through consecutive phase-aligned,
    resampled rings by linearly blending across one revolution per ring
    pair -- monotonic pitch, never crosses itself:
        P(i) = (1 - u)·A_i + u·B_i,  u = i / samples."""
    if not rings:
        return []
    if len(rings) == 1:
        return list(rings[0])
    path = []
    for ring_a, ring_b in zip(rings[:-1], rings[1:], strict=False):
        for i in range(samples):
            u = i / samples
            ax, ay = ring_a[i]
            bx, by = ring_b[i]
            path.append((ax * (1 - u) + bx * u, ay * (1 - u) + by * u))
    path.append(rings[-1][0])
    return path
