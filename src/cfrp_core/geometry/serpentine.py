"""Zigzag patterns: oriented serpentine infill (spec §5.6)."""
from __future__ import annotations

import math

import numpy as np
from shapely.affinity import rotate
from shapely.geometry import LineString

from ._common import check_gap_radius


def _arc_uturn(p0, p3, ext_dir, min_rad, step=0.5):
    """Circular-arc U-turn from p0 to p3, radius = max(chord/2, min_rad).

    The arc center sits on the ext_dir side of the chord so the fiber
    never bends tighter than min_rad."""
    chord = np.linalg.norm(p3 - p0)
    if chord < 1e-9:
        return []
    R = max(chord / 2.0, float(min_rad))
    mid = (p0 + p3) / 2.0
    chord_vec = p3 - p0
    perp = np.array([chord_vec[1], -chord_vec[0]])
    perp_len = np.linalg.norm(perp)
    if perp_len < 1e-9:
        return []
    perp = perp / perp_len
    h = math.sqrt(max(0.0, R * R - (chord / 2.0) ** 2))
    center = mid + ext_dir * h * perp

    a_start = math.atan2(p0[1] - center[1], p0[0] - center[0])
    a_end = math.atan2(p3[1] - center[1], p3[0] - center[0])

    da = a_end - a_start
    if ext_dir > 0:
        if da > 0:
            da -= 2.0 * math.pi
    else:
        if da < 0:
            da += 2.0 * math.pi

    arc_len = abs(da) * R
    n_pts = max(8, int(math.ceil(arc_len / step)))
    pts = []
    for k in range(1, n_pts + 1):
        frac = k / n_pts
        angle = a_start + da * frac
        pts.append([center[0] + R * math.cos(angle),
                     center[1] + R * math.sin(angle)])
    return pts


def oriented_serpentine(poly, angle, gap, min_rad, start_from_end=False):
    """Scan-line zigzag at `angle` degrees inside `poly`.

    Rotate by -angle; erode by r_min so turns stay inside; scan lines at
    miny + gap/2 + k*gap keeping the longest segment per line; alternate
    direction; join with circular-arc U-turns of radius max(gap/2, min_rad)
    so minimum bend radius is always respected; rotate back by +angle.
    """
    check_gap_radius(gap, min_rad)

    rot_poly = rotate(poly, -angle, origin='centroid')
    safe_poly = rot_poly.buffer(-min_rad, join_style=1)
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
            ext_dir = -direction
            arc_pts = _arc_uturn(p0, p3, ext_dir, min_rad)
            path.extend(arc_pts)

        path.extend(coords)
        direction *= -1

    if not path:
        return []
    final_line = LineString(path)
    final_line = rotate(final_line, angle, origin='centroid')
    return list(final_line.coords)
