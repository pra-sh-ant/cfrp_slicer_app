"""Shared polygon/path helpers for the path generators. Lengths in mm."""
from __future__ import annotations

import math

import numpy as np
import pyclipper
import shapely
from shapely.geometry import Point, Polygon

_SCALE = 1000

def _poly_to_clipper(poly):
    return [pyclipper.scale_to_clipper(list(poly.exterior.coords)[:-1], _SCALE)]


def _clipper_to_polys(tree):
    polys = []
    for path in tree:
        coords = pyclipper.scale_from_clipper(path, _SCALE)
        if len(coords) < 3:
            continue
        poly = Polygon(coords)
        if not poly.is_valid:
            # Clipper occasionally emits a micro self-intersecting loop on a
            # small offset circle; growing that back (fillet) turns it into
            # a spike that hits a hole and drops the whole ring.
            poly = largest_polygon(poly.buffer(0))
            if poly.is_empty:
                continue
        polys.append(poly)
    return polys


def _offset_polygon(poly, delta: float, arc_tolerance: float = 0.01):
    pco = pyclipper.PyclipperOffset(arc_tolerance=arc_tolerance * _SCALE)
    for path in _poly_to_clipper(poly):
        pco.AddPath(path, pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
    tree = pco.Execute(int(delta * _SCALE))
    return _clipper_to_polys(tree)


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


_QUAD_SEGS = 64


def fillet(poly, min_radius: float):
    """Morphological opening: shrink by r then grow by r. Corners sharper
    than radius r can't survive the shrink, so they come back as arcs."""
    if min_radius <= 0:
        return poly
    shrunk = _offset_polygon(poly, -min_radius)
    if not shrunk:
        return Polygon()
    grown = []
    for p in shrunk:
        grown.extend(_offset_polygon(p, min_radius))
    if not grown:
        return Polygon()
    if len(grown) == 1:
        return grown[0]
    from shapely.ops import unary_union
    return unary_union(grown)


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
    outer_only = Polygon(poly.exterior)
    offset_results = _offset_polygon(outer_only, -inset)
    if not offset_results:
        return None
    offset_poly = offset_results[0] if len(offset_results) == 1 else max(offset_results, key=lambda p: p.area)
    if offset_poly.is_empty or offset_poly.area < area_eps:
        return None
    filleted = fillet(offset_poly, min_radius)
    if isinstance(filleted, Polygon) and filleted.is_empty:
        return None
    if hasattr(filleted, 'geom_type') and filleted.geom_type == 'MultiPolygon':
        comps = list(filleted.geoms)
    elif isinstance(filleted, list):
        comps = filleted
    else:
        comps = [filleted]
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


_ARC_STEP_MM = 0.75   # sample spacing when tracing along a ring boundary


def arc_along_ring(ring_poly, p_from, p_to):
    """Points along the SHORTER arc of ring_poly.exterior connecting the
    projections of p_from and p_to onto that exterior.

    Used to bridge between regions without crossing an already-printed
    ring: instead of cutting across the ring in a straight line, the
    connector retraces the ring boundary (acceptable double-deposition).
    """
    ext = ring_poly.exterior
    length = ext.length
    if length < 1e-9:
        return [p_from, p_to]

    t_from = ext.project(Point(p_from))
    t_to = ext.project(Point(p_to))

    fwd = (t_to - t_from) % length
    bck = length - fwd

    if fwd <= bck:
        arc_len, direction = fwd, 1.0
    else:
        arc_len, direction = bck, -1.0

    n = max(2, int(arc_len / _ARC_STEP_MM))
    pts = []
    for i in range(n + 1):
        t = (t_from + direction * arc_len * i / n) % length
        pt = ext.interpolate(t)
        pts.append((pt.x, pt.y))
    return pts


def dedupe_path(points, tol: float = 0.05):
    """Drop consecutive points closer than `tol` apart -- the blend/
    resample generators all produce a few near-duplicate points at
    ring-transition seams; collapsing them keeps kinematics planning
    and the viewer from choking on effectively-zero-length segments."""
    if not points:
        return []
    pts = np.array(points, dtype=np.float64)
    if pts.ndim == 1:
        return list(points)
    xs, ys = pts[:, 0].tolist(), pts[:, 1].tolist()
    kept = [0]
    lx, ly = xs[0], ys[0]
    for i in range(1, len(xs)):   # sequential: each point is compared to the last KEPT one
        if math.hypot(xs[i] - lx, ys[i] - ly) > tol:
            kept.append(i)
            lx, ly = xs[i], ys[i]
    return [(xs[i], ys[i]) for i in kept]


def resample_closed_ring(poly, samples: int, phase_start=None):
    """Resample poly's exterior into `samples` evenly arc-length-spaced
    points, starting as close as possible to phase_start so consecutive
    rings stay angularly aligned for blending."""
    coords = np.asarray(poly.exterior.coords, dtype=np.float64)[:, :2]
    cum = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(coords, axis=0), axis=1))])
    length = float(cum[-1])
    offset = poly.exterior.project(Point(phase_start)) if phase_start is not None else 0.0
    t = (offset + np.arange(samples) / samples * length) % length
    return list(zip(np.interp(t, cum, coords[:, 0]).tolist(), np.interp(t, cum, coords[:, 1]).tolist(), strict=False))


def resample_polar_ring(poly, samples: int, center, phase_start=None):
    """Resample poly's exterior at `samples` equal ANGLES about `center`
    (poly must be star-shaped about it), starting at phase_start's angle and
    turning the same way as the exterior. Blending two nested rings sampled
    this way, P = (1-u)A + uB, stays radially between them at every angle,
    so the spiral cannot cross itself even when the rings' corner radii
    differ (arc-length pairing drifts at such corners)."""
    cx, cy = center
    # Densify first: between two far-apart vertices of a straight side,
    # linear interpolation in (angle, radius) would bulge outwards.
    ring = shapely.segmentize(poly.exterior, 0.1)
    coords = np.asarray(ring.coords, dtype=np.float64)[:-1, :2] - (cx, cy)
    ang = np.arctan2(coords[:, 1], coords[:, 0])
    rad = np.hypot(coords[:, 0], coords[:, 1])
    order = np.argsort(ang)
    ang_s, rad_s = ang[order], rad[order]
    sense = 1.0 if poly.exterior.is_ccw else -1.0
    if phase_start is not None:
        a0 = math.atan2(phase_start[1] - cy, phase_start[0] - cx)
    else:
        a0 = float(ang[0])
    theta = a0 + sense * 2.0 * math.pi * np.arange(samples) / samples
    r = np.interp(np.mod(theta + math.pi, 2.0 * math.pi) - math.pi, ang_s, rad_s, period=2.0 * math.pi)
    return list(zip((cx + r * np.cos(theta)).tolist(), (cy + r * np.sin(theta)).tolist(), strict=False))


def blend_chain(rings, samples: int):
    """Continuous path threading through consecutive phase-aligned,
    resampled rings by linearly blending across one revolution per ring
    pair -- monotonic pitch, never crosses itself:
        P(i) = (1 - u)·A_i + u·B_i,  u = i / samples."""
    if not rings:
        return []
    if len(rings) == 1:
        return list(rings[0])
    arr = np.array(rings, dtype=np.float64)
    u = np.linspace(0, 1, samples, endpoint=False).reshape(-1, 1)
    segments = []
    for k in range(len(arr) - 1):
        blended = arr[k, :samples] * (1.0 - u) + arr[k + 1, :samples] * u
        segments.append(blended)
    path_arr = np.vstack(segments)
    path = [(float(r[0]), float(r[1])) for r in path_arr]
    path.append((float(arr[-1, 0, 0]), float(arr[-1, 0, 1])))
    return path


def _circumradii(pts_arr):
    """Vectorized circumradius for all interior vertices. Returns array of
    length n-2 (one radius per interior vertex)."""
    a = pts_arr[1:-1] - pts_arr[:-2]   # vectors prev->cur
    b = pts_arr[2:] - pts_arr[1:-1]    # vectors cur->next
    c = pts_arr[2:] - pts_arr[:-2]     # vectors prev->next
    cross = np.abs(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0])
    da = np.linalg.norm(a, axis=1)
    db = np.linalg.norm(b, axis=1)
    dc = np.linalg.norm(c, axis=1)
    safe = (cross > 1e-12) & (da > 0.01) & (db > 0.01)
    radii = np.full(len(cross), 9999.0)
    radii[safe] = (da[safe] * db[safe] * dc[safe]) / (2.0 * cross[safe])
    return radii


BEND_WINDOW_MM = 1.5   # ~one tow width: the length over which a bend is judged


def local_bend_radii(pts_arr, window=BEND_WINDOW_MM):
    """Bend radius at every interior vertex, judged over `window` mm of path:
        R = (path length spanned) / (total turning inside it).
    The span runs between the midpoints of the segments bounding the
    vertices within ±window/2, each end half-segment clipped to window/2.
    Exact on an evenly sampled arc at any spacing; a corner at the end of a
    long straight reads ~window/θ (small), where the three-point circumradius
    read it as huge; and a coarse polygonal arc resampled finely (turning
    bunched on single points) is not mistaken for a tight bend."""
    pts = np.asarray(pts_arr, dtype=np.float64)
    n = len(pts)
    if n < 3:
        return np.zeros(0)
    seg = np.diff(pts, axis=0)
    seg_len = np.linalg.norm(seg, axis=1)
    unit = seg / np.maximum(seg_len, 1e-12)[:, None]
    cos = np.einsum('ij,ij->i', unit[:-1], unit[1:])
    theta = np.arccos(np.clip(cos, -1.0, 1.0))           # at vertices 1..n-2
    theta[(seg_len[:-1] < 1e-9) | (seg_len[1:] < 1e-9)] = 0.0
    cum = np.concatenate([[0.0], np.cumsum(seg_len)])     # arc length at vertices
    pos = cum[1:-1]                                       # interior vertices
    half = 0.5 * window
    j = np.searchsorted(pos, pos - half, side='left')     # first vertex in window
    k = np.searchsorted(pos, pos + half, side='right') - 1
    turn_cum = np.concatenate([[0.0], np.cumsum(theta)])
    turning = turn_cum[k + 1] - turn_cum[j]
    # vertex v (interior index) sits between segments v and v+1
    length = (pos[k] - pos[j]
              + np.minimum(seg_len[j] / 2.0, half)
              + np.minimum(seg_len[k + 1] / 2.0, half))
    radii = np.full(n - 2, 9999.0)
    m = turning > 1e-9
    radii[m] = length[m] / turning[m]
    return radii


def enforce_min_radius(path, min_rad, max_iter=100):
    """Hard enforcement of minimum bend radius on every toolpath.

    Gauss-Seidel Laplacian smoothing with numpy-accelerated violation
    detection: each iteration uses vectorized circumradius to find all
    violations, then applies sequential in-place 50% blending toward
    neighbour midpoints (sequential for Gauss-Seidel convergence).

    Returns a new path list."""
    if min_rad <= 0 or len(path) < 3:
        return list(path)
    pts = np.array(path, dtype=np.float64)[:, :2].copy()

    # 0.1% tolerance: arcs built at exactly min_rad (fillets, Corner-Step
    # corners) read 2.99999... and smoothing them bent their line joins into
    # real kinks (R ~1.6 mm at r_min 3). Stricter than validation's 1%.
    limit = min_rad * (1.0 - 1e-3)
    for _ in range(max_iter):
        radii = _circumradii(pts)
        mask = radii < limit
        if not np.any(mask):
            break
        idxs = np.nonzero(mask)[0] + 1
        for i in idxs:
            mid = (pts[i - 1] + pts[i + 1]) * 0.5
            pts[i] = pts[i] * 0.5 + mid * 0.5

    return [(float(p[0]), float(p[1])) for p in pts]


def audit_min_radius(path, min_rad):
    """Count vertices where circumradius < min_rad. Returns (violations, total_interior)."""
    if min_rad <= 0 or len(path) < 3:
        return 0, 0
    pts = np.array(path, dtype=np.float64)[:, :2]
    radii = _circumradii(pts)
    violations = int(np.sum(radii < min_rad))
    return violations, len(radii)


