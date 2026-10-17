"""Corner-Step Spiral: concentric rings with smooth transitions.

For rectangles the rings are computed analytically with rounded corners
(r = min_radius) and the step to the next ring is a CW quarter-circle arc
inside the bottom-left corner.

For all other shapes (triangle, hexagon, circle, …) the rings are concentric
polygon offsets (Shapely buffer) resampled CW, connected by a smooth arc
transition that respects min_radius.

Path starts heading +Y so the tangent-following anchor extends in −Y.
"""
from __future__ import annotations

import math

from ._common import check_gap_radius, dedupe_path, fillet, largest_polygon, resample_closed_ring

_ARC_PTS = 32
_RING_PTS = 256


# ---------------------------------------------------------------------------
# Rectangle detection
# ---------------------------------------------------------------------------

def _is_rectangle(poly):
    x0, y0, x1, y1 = poly.bounds
    W = x1 - x0
    H = y1 - y0
    if W < 1e-6 or H < 1e-6:
        return False
    return poly.area >= W * H * 0.90


# ---------------------------------------------------------------------------
# Analytical rectangular path
# ---------------------------------------------------------------------------

def _rect_path(base_poly, gap, r, N):
    x0, y0, x1, y1 = base_poly.bounds
    W = x1 - x0
    H = y1 - y0

    min_dim = (2 * N - 1) * gap + 2 * r
    if W < min_dim:
        max_n = max(1, int((W - 2 * r + gap) / (2 * gap)))
        raise ValueError(
            f"Corner-Step Spiral: width {W:.1f} mm < {min_dim:.1f} mm needed "
            f"for {N} tracks (gap={gap:.2f}, r={r:.2f}). "
            f"Maximum tracks_per_layer = {max_n}."
        )
    if H < min_dim:
        max_n = max(1, int((H - 2 * r + gap) / (2 * gap)))
        raise ValueError(
            f"Corner-Step Spiral: height {H:.1f} mm < {min_dim:.1f} mm needed "
            f"for {N} tracks (gap={gap:.2f}, r={r:.2f}). "
            f"Maximum tracks_per_layer = {max_n}."
        )

    def xl(k):
        return x0 + gap / 2 + k * gap

    def xr(k):
        return x1 - gap / 2 - k * gap

    def yb(k):
        return y0 + gap / 2 + k * gap

    def yt(k):
        return y1 - gap / 2 - k * gap

    path: list[tuple[float, float]] = []

    def arc_cw(cx, cy, a0, a1):
        for i in range(_ARC_PTS + 1):
            a = a0 + (a1 - a0) * i / _ARC_PTS
            path.append((cx + r * math.cos(a), cy + r * math.sin(a)))

    path.append((xl(0), yb(0) + r))

    for k in range(N):
        path.append((xl(k), yt(k) - r))
        arc_cw(xl(k) + r, yt(k) - r, math.pi, math.pi / 2)
        path.append((xr(k) - r, yt(k)))
        arc_cw(xr(k) - r, yt(k) - r, math.pi / 2, 0)
        path.append((xr(k), yb(k) + r))
        arc_cw(xr(k) - r, yb(k) + r, 0, -math.pi / 2)

        if k < N - 1:
            path.append((xl(k + 1) + r, yb(k)))
            arc_cw(xl(k + 1) + r, yb(k) + r, -math.pi / 2, -math.pi)
        else:
            path.append((xl(k) + r, yb(k)))

    return path


# ---------------------------------------------------------------------------
# Generic polygon path (triangle, hexagon, circle, …)
# ---------------------------------------------------------------------------

def _nearest_index(ring, point):
    """Index of the point in `ring` closest to `point`."""
    px, py = point
    best_i = 0
    best_d = math.hypot(ring[0][0] - px, ring[0][1] - py)
    for i in range(1, len(ring)):
        d = math.hypot(ring[i][0] - px, ring[i][1] - py)
        if d < best_d:
            best_d = d
            best_i = i
    return best_i


def _tangent_bezier(p0, tan_in, p1, tan_out):
    """Cubic Bézier from p0 to p1 with tangent continuity.

    tan_in/tan_out are unit tangent vectors at p0/p1.  Control-point
    extension is set to chord/3 (the Hermite-interpolation default).
    Point count adapts to chord length so segments stay ~0.3 mm.
    """
    dx = p1[0] - p0[0]
    dy = p1[1] - p0[1]
    dist = math.hypot(dx, dy)
    if dist < 1e-6:
        return []
    ext = dist / 3.0
    c1 = (p0[0] + tan_in[0] * ext, p0[1] + tan_in[1] * ext)
    c2 = (p1[0] - tan_out[0] * ext, p1[1] - tan_out[1] * ext)
    n_pts = max(4, int(dist / 0.3))
    pts: list[tuple[float, float]] = []
    for i in range(1, n_pts):
        t = i / n_pts
        s = 1.0 - t
        x = s**3 * p0[0] + 3*s**2*t * c1[0] + 3*s*t**2 * c2[0] + t**3 * p1[0]
        y = s**3 * p0[1] + 3*s**2*t * c1[1] + 3*s*t**2 * c2[1] + t**3 * p1[1]
        pts.append((x, y))
    return pts


def _resample_cw(coords, n_pts):
    from shapely.geometry import Polygon as ShapelyPolygon
    poly = ShapelyPolygon(coords)
    if poly.exterior.is_ccw:
        coords = list(reversed(coords))
    return resample_closed_ring(poly, n_pts)


def _poly_path(base_poly, gap, r, N):
    rings: list[list[tuple[float, float]]] = []
    for k in range(N):
        offset = gap / 2.0 + k * gap
        shrunk = base_poly.buffer(-offset, join_style=1)
        if shrunk.is_empty:
            break
        shrunk = largest_polygon(shrunk)
        filleted = fillet(shrunk, r)
        if filleted.is_empty:
            break
        filleted = largest_polygon(filleted)
        coords = _resample_cw(list(filleted.exterior.coords), _RING_PTS)
        rings.append(coords)

    if not rings:
        return []

    path: list[tuple[float, float]] = []

    for k, ring in enumerate(rings):
        if k == 0:
            best_i = 0
            best_y = ring[0][1]
            for i, (x, y) in enumerate(ring):
                if y < best_y or (y == best_y and x < ring[best_i][0]):
                    best_y = y
                    best_i = i
            reordered = ring[best_i:] + ring[:best_i]
            path.extend(reordered)
        else:
            prev_pt = path[-1]
            start_idx = _nearest_index(ring, prev_pt)
            reordered = ring[start_idx:] + ring[:start_idx]

            target = reordered[0]

            if len(path) >= 2:
                dx_in = prev_pt[0] - path[-2][0]
                dy_in = prev_pt[1] - path[-2][1]
                d_in = math.hypot(dx_in, dy_in)
                if d_in > 1e-9:
                    tan_in = (dx_in / d_in, dy_in / d_in)
                else:
                    tan_in = (target[0] - prev_pt[0], target[1] - prev_pt[1])
                    d_t = math.hypot(tan_in[0], tan_in[1])
                    tan_in = (tan_in[0] / d_t, tan_in[1] / d_t) if d_t > 1e-9 else (1.0, 0.0)
            else:
                tan_in = (target[0] - prev_pt[0], target[1] - prev_pt[1])
                d_t = math.hypot(tan_in[0], tan_in[1])
                tan_in = (tan_in[0] / d_t, tan_in[1] / d_t) if d_t > 1e-9 else (1.0, 0.0)

            if len(reordered) >= 2:
                dx_out = reordered[1][0] - reordered[0][0]
                dy_out = reordered[1][1] - reordered[0][1]
                d_out = math.hypot(dx_out, dy_out)
                tan_out = (dx_out / d_out, dy_out / d_out) if d_out > 1e-9 else (1.0, 0.0)
            else:
                tan_out = tan_in

            arc_pts = _tangent_bezier(prev_pt, tan_in, target, tan_out)
            if arc_pts:
                path.extend(arc_pts)

            path.extend(reordered)

    return path


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def corner_step_path(base_poly, track_gap, min_radius, track_count):
    """Concentric CW rings with corner-step transitions.

    Rectangles use an analytical path; other shapes use polygon offsets.
    Path starts heading +Y.  Returns (path, base_poly).
    """
    check_gap_radius(track_gap, min_radius)
    if base_poly.is_empty:
        return [], base_poly

    N = int(track_count)
    gap = track_gap
    r = min_radius

    if _is_rectangle(base_poly):
        raw = _rect_path(base_poly, gap, r, N)
    else:
        raw = _poly_path(base_poly, gap, r, N)

    if not raw:
        return [], base_poly

    return dedupe_path(raw), base_poly
