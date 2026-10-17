"""Corner-Step Spiral: concentric rings with smooth transitions.

For rectangles the rings are computed analytically with rounded corners
(r = min_radius) and the step to the next ring is a CW quarter-circle arc
inside the bottom-left corner.

For all other shapes (triangle, hexagon, circle, …) the rings are concentric
polygon offsets (Shapely buffer) resampled CW, joined by lane changes on one
seam (middle of the longest straight) that respect min_radius.

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

def _rect_path(base_poly, gap, r, N, corner_lead=False):
    """Inward analytic rectangle path. `corner_lead` prefixes ring 0's
    bottom-left corner arc, starting where _rect_path_outward ends, so an
    inward layer after an outward one continues through that corner instead
    of cutting it diagonally."""
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

    if corner_lead:
        path.append((xl(0) + r, yb(0)))
        arc_cw(xl(0) + r, yb(0) + r, -math.pi / 2, -math.pi)
    else:
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


def _rect_path_outward(base_poly, gap, r, N):
    """_rect_path's partner for alternate layers: innermost ring first,
    stepping OUT, still clockwise, so a layer continues exactly where the
    inward one ended (bottom edge of the innermost ring, heading -x)
    instead of reversing the fibre there.

    Ring k runs its bottom edge one gap past its own (unused) bottom-left
    corner, to x = xl(k-1) + r, then a CW quarter arc of radius r lands on
    ring k-1's left side at yb(k) + r (above that ring's corner). That arc
    and the previous step's arc are the same arc offset by (gap, gap); their
    circles meet only outside the quadrants drawn, so steps never cross."""
    x0, y0, x1, y1 = base_poly.bounds
    _rect_path(base_poly, gap, r, N)          # same size checks / messages

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

    k = N - 1
    path.append((xl(k) + r, yb(k)))           # where the inward layer ended
    arc_cw(xl(k) + r, yb(k) + r, -math.pi / 2, -math.pi)
    for k in range(N - 1, -1, -1):
        path.append((xl(k), yt(k) - r))
        arc_cw(xl(k) + r, yt(k) - r, math.pi, math.pi / 2)
        path.append((xr(k) - r, yt(k)))
        arc_cw(xr(k) - r, yt(k) - r, math.pi / 2, 0)
        path.append((xr(k), yb(k) + r))
        arc_cw(xr(k) - r, yb(k) + r, 0, -math.pi / 2)
        if k > 0:
            path.append((xl(k - 1) + r, yb(k)))
            arc_cw(xl(k - 1) + r, yb(k) + r, -math.pi / 2, -math.pi)
        else:
            path.append((xl(0) + r, yb(0)))
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


def _poly_path(base_poly, gap, r, N, outward=False, seam_hint=None):
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
        # At most 0.5 mm apart: a fixed count left big rings too coarse to
        # follow their r_min corner arcs (1.6 mm chords on a 135 mm hexagon).
        n_pts = max(_RING_PTS, int(math.ceil(filleted.exterior.length / 0.5)))
        coords = _resample_cw(list(filleted.exterior.coords), n_pts)
        rings.append(coords)

    if not rings:
        return []

    # Lane-change seam: every step sits on ONE seam line, at the middle of
    # the outer ring's longest straight run. Ring k stops L short of its
    # start and S-curves over that L into ring k+1 at the seam. A sideways
    # shift of one gap over length L bends at about L²/(6·gap), so
    # L = 1.5·√(6·gap·r) keeps the step above min radius; landing at the
    # same seam each time means the steps do not drift round onto corners.
    lane_len = 1.5 * math.sqrt(6.0 * gap * r) if r > 0 else gap
    # Seam where the previous layer ended, if known (on a sliced mesh every
    # side of a polygonised circle is "longest", so that choice would jump
    # between layers); else the middle of the longest straight.
    seam_pt = rings[0][_nearest_index(rings[0], seam_hint) if seam_hint is not None
                       else _seam_index(rings[0])]
    # Outward (alternate layers): innermost ring first, lane changes outward,
    # same winding, so a layer starts where the inward one ended without
    # reversing the fibre.
    order = rings[::-1] if outward else rings
    path: list[tuple[float, float]] = []
    for k, ring in enumerate(order):
        start = _nearest_index(ring, seam_pt)
        lap = ring[start:] + ring[:start]
        if k < len(order) - 1:
            spacing = math.dist(ring[0], ring[1]) or 1e-9
            cut = int(math.ceil(lane_len / spacing))
            if 2 * cut < len(lap):
                lap = lap[:len(lap) - cut]
        if path:
            path.extend(_tangent_bezier(path[-1], _unit(path[-2], path[-1]), lap[0], _unit(lap[0], lap[1])))
        path.extend(lap)
    return path


def _unit(a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy)
    return (dx / n, dy / n) if n > 1e-9 else (1.0, 0.0)


def _seam_index(ring):
    """Middle of the ring's longest run of near-straight vertices (turning
    < 1 deg), so lane changes land on a straight; lowest point if none."""
    n = len(ring)
    straight = []
    for i in range(n):
        a, b, c = ring[i - 1], ring[i], ring[(i + 1) % n]
        u, v = _unit(a, b), _unit(b, c)
        straight.append(u[0] * v[0] + u[1] * v[1] > math.cos(math.radians(1.0)))
    best_len = best_mid = 0
    if not all(straight):
        first = straight.index(False)
        run = 0
        for j in range(1, n + 1):
            i = (first + j) % n
            if straight[i]:
                run += 1
                if run > best_len:
                    best_len, best_mid = run, (i - run // 2) % n
            else:
                run = 0
    if best_len == 0:
        return min(range(n), key=lambda i: (ring[i][1], ring[i][0]))
    return best_mid


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def corner_step_path(base_poly, track_gap, min_radius, track_count, outward=False,
                     corner_lead=False, seam_hint=None):
    """Concentric CW rings with corner-step transitions.

    Rectangles use an analytical path; other shapes use polygon offsets with
    lane-change steps on one seam. `outward` (alternate layers) runs from
    the innermost ring out with the same winding, starting where the inward
    path ends; `corner_lead` makes an inward rectangle path start where the
    outward one ends. Returns (path, base_poly).
    """
    check_gap_radius(track_gap, min_radius)
    if base_poly.is_empty:
        return [], base_poly

    N = int(track_count)
    gap = track_gap
    r = min_radius

    if _is_rectangle(base_poly):
        raw = (_rect_path_outward(base_poly, gap, r, N) if outward
               else _rect_path(base_poly, gap, r, N, corner_lead=corner_lead))
    else:
        raw = _poly_path(base_poly, gap, r, N, outward=outward, seam_hint=seam_hint)

    if not raw:
        return [], base_poly

    return dedupe_path(raw), base_poly
