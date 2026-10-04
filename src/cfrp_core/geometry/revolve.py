"""Z-revolve shells: radius profiles and per-slice cross-sections (spec §7)."""
from __future__ import annotations

import math

from shapely.geometry import Point, Polygon


def revolve_cross_section(cx, cy, outer_radius, inner_radius, shape_type='round'):
    """One Z-slice's printable cross-section: an annulus (outer minus inner)
    when there's a hole, or a solid disc/square once the inner radius
    collapses to ~0. The inner radius is clamped to 0.999 × outer so the
    ring never inverts. A square has side 2·radius."""
    def build(radius):
        if radius <= 1e-6:
            return Polygon()
        if shape_type == 'square':
            return Polygon([
                (cx - radius, cy - radius), (cx + radius, cy - radius),
                (cx + radius, cy + radius), (cx - radius, cy + radius),
            ])
        return Point(cx, cy).buffer(radius)

    outer_poly = build(outer_radius)
    if outer_poly.is_empty or inner_radius <= 1e-6:
        return outer_poly
    inner_poly = build(min(inner_radius, outer_radius * 0.999))
    return outer_poly.difference(inner_poly)


def revolve_radius_profile(shape, base_radius, tracks, gap):
    """Return progress(0..1) -> (outer_radius, inner_radius) for a revolve
    shell. The wall is `tracks · gap` thick:  r_inner = max(0, r_outer − wall).

        Sphere            r = R·√(1 − (2p − 1)²)   (true circle; height 2R)
        Dome              r = R·√(1 − p²)
        Tapered cylinder  r = R(1 − p) + r₁·p,  r₁ = max(0.45R, 3)
        Cylinder / tube   r = R
    """
    outer_radius = max(float(base_radius), 1.0)
    wall = tracks * gap

    if shape == 'Hollow Sphere (Z)':
        def sphere(progress):
            r = outer_radius * math.sqrt(max(0.0, 1.0 - (2.0 * progress - 1.0) ** 2))
            return r, max(0.0, r - wall)

        return sphere

    if shape == 'Dome (Z)':
        def dome(progress):
            r = outer_radius * math.sqrt(max(0.0, 1.0 - progress * progress))
            return r, max(0.0, r - wall)

        return dome

    if shape == 'Tapered Cylinder (Z)':
        r1 = max(outer_radius * 0.45, 3.0)

        def taper(progress):
            r = outer_radius * (1.0 - progress) + r1 * progress
            return r, max(0.0, r - wall)

        return taper

    # Hollow Cylinder / Hollow Square Tube: constant outer radius.
    return lambda progress: (outer_radius, max(0.0, outer_radius - wall))
