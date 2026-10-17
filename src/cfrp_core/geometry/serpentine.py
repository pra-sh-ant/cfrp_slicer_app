"""Zigzag patterns: oriented serpentine infill (spec §5.6)."""
from __future__ import annotations

import math

import numpy as np
from shapely.affinity import rotate
from shapely.geometry import LineString

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


def oriented_serpentine(poly, angle, gap, min_rad, start_from_end=False):
    """Scan-line zigzag at `angle` degrees inside `poly`.

    Rotate by -angle; erode by max(r_min, gap) so turns stay inside; scan lines at
    miny + gap/2 + k*gap keeping the longest segment per line; alternate
    direction; join with semicircular U-turns (_uturn);
    rotate back by +angle.
    """
    check_gap_radius(gap, min_rad)

    rot_poly = rotate(poly, -angle, origin='centroid')
    # Line ends sit this far inside: min_rad, or at least one gap, which is
    # what a lat/2 U-turn plus half a tow needs to stay inside the outline.
    safe_poly = rot_poly.buffer(-max(min_rad, gap), join_style=1)
    if safe_poly.is_empty or safe_poly.area < gap:
        safe_poly = rot_poly

    minx, miny, maxx, maxy = safe_poly.bounds
    path = []
    y = miny + gap / 2.0
    lines = []

    while y <= maxy:
        line = LineString([(minx - gap * 2, y), (maxx + gap * 2, y)])
        inter = line.intersection(safe_poly)
        if inter.geom_type == 'LineString' and not inter.is_empty:
            lines.append(inter)
        elif inter.geom_type == 'MultiLineString' and list(inter.geoms):
            longest = max(list(inter.geoms), key=lambda g: g.length)
            lines.append(longest)
        y += gap

    if start_from_end:
        lines = list(reversed(lines))
        direction = -1
    else:
        direction = 1

    for i, line in enumerate(lines):
        coords = list(line.coords)
        if direction == -1:
            coords.reverse()

        if i > 0 and path:
            p0 = np.array(path[-1])
            p3 = np.array(coords[0])
            # The previous line ran along -direction (direction flips per line).
            path.extend(_uturn(p0, p3, (-direction, 0.0)))

        path.extend(coords)
        direction *= -1

    if not path:
        return []
    final_line = LineString(path)
    final_line = rotate(final_line, angle, origin='centroid')
    return list(final_line.coords)
