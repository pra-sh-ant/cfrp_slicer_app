"""Zigzag patterns: oriented serpentine infill (spec §5.6) and the
"Serpentine Infill" shape's own fixed zigzag (spec §5.7)."""
from __future__ import annotations

import math

import numpy as np
from shapely.affinity import rotate, translate
from shapely.geometry import LineString, Point

from ._common import check_gap_radius, dedupe_path
from .outlines import serpentine_print_extent


def oriented_serpentine(poly, angle, gap, min_rad, start_from_end=False):
    """Scan-line zigzag at `angle` degrees inside `poly`.

    Rotate by −angle; erode by r_min so turns stay inside (fall back to the
    un-eroded area if that leaves too little); scan lines at
    miny + gap/2 + k·gap keeping the longest segment per line; alternate
    direction; join lines with a cubic Bézier whose control points extend
    ext = max(1.5·r_min, gap) outward; rotate back by +angle.
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
            ext_mag = max(min_rad * 1.5, gap)

            p1 = p0 + np.array([ext_dir * ext_mag, 0])
            p2 = p3 + np.array([ext_dir * ext_mag, 0])

            t = np.linspace(0, 1, 15)
            t_mat = np.array([t]).T
            curve = (1 - t_mat)**3 * p0 + 3*(1 - t_mat)**2 * t_mat * p1 + \
                    3*(1 - t_mat) * t_mat**2 * p2 + t_mat**3 * p3
            path.extend(curve.tolist())

        path.extend(coords)
        direction *= -1

    if not path:
        return []
    final_line = LineString(path)
    final_line = rotate(final_line, angle, origin='centroid')
    return list(final_line.coords)


def serpentine_infill(width, height, min_radius, track_gap, invert_bends, angle, start_x, start_y):
    """The "Serpentine Infill" shape: parallel lines joined by semicircular
    U-turns of radius line_gap/2 (15 points), rotated by `angle` about
    (w/2, h/2), then translated to (start_x, start_y)."""
    check_gap_radius(track_gap, min_radius)
    sx, ex, line_gap, num_lines = serpentine_print_extent(width, height, min_radius, track_gap, invert_bends)
    path = []

    def u_turn(x, y_start, y_end, side):
        center_y = (y_start + y_end) / 2.0
        radius = abs(y_end - y_start) / 2.0
        angles = np.linspace(-math.pi / 2.0, math.pi / 2.0, 15)
        if side < 0:
            angles = math.pi - angles
        return [(x + side * radius * math.cos(a),
                 center_y + radius * math.sin(a)) for a in angles[1:]]

    for i in range(num_lines + 1):
        y = i * line_gap
        if i % 2 == 0:
            path.extend([(sx, y), (ex, y)])
            if i < num_lines:
                path.extend(u_turn(ex, y, y + line_gap, 1))
        else:
            path.extend([(ex, y), (sx, y)])
            if i < num_lines:
                path.extend(u_turn(sx, y, y + line_gap, -1))

    filtered_path = dedupe_path(path)
    if not filtered_path:
        return []

    linestring = LineString(filtered_path)
    if angle != 0.0:
        linestring = rotate(linestring, angle, origin=Point(width / 2, height / 2))
    return list(translate(linestring, xoff=start_x, yoff=start_y).coords)
