"""Shared polygon/path helpers for the path generators. Lengths in mm."""
from __future__ import annotations

import math

import numpy as np
import pyclipper
import shapely
from shapely.geometry import Point, Polygon
from shapely.geometry.polygon import orient

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
            # buffer(0) returns the ring clockwise, Clipper counter-clockwise.
            # Spirals pair ring points along each ring's own direction, so one
            # reversed ring blended into its neighbour drew a teardrop loop
            # across the other rings (sliced cylinder, 2 layers of 75).
            poly = orient(poly, sign=1.0)
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


def notched_rings(poly, gap, min_radius, max_rings=None):
    """Concentric ring polygons for a part WITH HOLES that wrap around the
    holes instead of stopping at the first one.

    Ring k is the outer shell inset by gap/2 + k*gap, minus a keep-out per
    hole: gap/2 around the hole until a ring first reaches it (ring k_h),
    then growing one gap per ring, gap/2 + (k - k_h)*gap. So once a ring
    meets a hole the following rings bend round it one gap apart (a notch
    that deepens ring by ring), like fibre steered round a bolt hole, and
    the area round the hole is filled. Each ring is filleted to
    min_radius; the largest piece is kept. Returns simple polygons
    (exteriors), outermost first. (inset_components stops the whole
    spiral at the first hole: a 60 x 40 board with four corner holes got
    one ring, 17 % coverage.)"""
    shell = Polygon(poly.exterior)
    holes = hole_keepouts(poly)
    first_touch = [None] * len(holes)
    rings, k = [], 0
    while max_rings is None or len(rings) < max_rings:
        inset = _offset_polygon(shell, -(gap / 2.0 + k * gap))
        if not inset:
            break
        inset = max(inset, key=lambda g: g.area)
        region = inset
        for i, hole in enumerate(holes):
            if first_touch[i] is None and inset.exterior.distance(hole) <= gap / 2.0 + 1e-6:
                first_touch[i] = k
            grow = 0 if first_touch[i] is None else k - first_touch[i]
            region = region.difference(hole.buffer(gap / 2.0 + grow * gap, quad_segs=16))
        if region.is_empty:
            break
        region = largest_polygon(region)
        rounded = fillet(Polygon(region.exterior), min_radius) if min_radius > 0 else region
        if rounded.is_empty or rounded.area < 1.0:
            break
        rounded = largest_polygon(rounded)
        # the fillet grows back over the keep-outs it rounded; cut them again
        for i, hole in enumerate(holes):
            if first_touch[i] is not None:
                rounded = rounded.difference(hole.buffer(gap / 2.0 + (k - first_touch[i]) * gap, quad_segs=16))
        if rounded.is_empty:
            break
        rounded = orient(Polygon(largest_polygon(rounded).exterior), 1.0)
        if rounded.area < 1.0:
            break
        rings.append(rounded)
        k += 1
    return rings


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


def ring_tail(ring_pts, start, length):
    """The `length` mm of a closed ring (point list, no repeated end) that
    come just BEFORE index `start`, in traversal order: a lead-in that lies
    on the ring itself, tangent to where the path starts."""
    n = len(ring_pts)
    if length <= 0 or n < 3:
        return []
    out, run, k = [], 0.0, start
    while run < length and len(out) < n - 1:
        a, b = ring_pts[(k - 1) % n], ring_pts[k % n]
        run += math.dist(a, b)
        out.append(a)
        k -= 1
    return out[::-1]


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


# Two tows of width w meeting at angle a share w / sin(a) of length: ~3.5 w
# at 20 deg (a crossing), ~11 w at 5 deg (side-by-side overlap, e.g. a
# helical connector lapping its own ring at a slightly higher Z).
CROSSING_MIN_ANGLE_DEG = 20.0


def crossing_pairs(coords, min_angle_deg=CROSSING_MIN_ANGLE_DEG):
    """(i, j): the segment numbers of each crossing count_crossings counts."""
    none = np.zeros(0, dtype=np.int64)
    pts = np.asarray(coords, dtype=np.float64)
    if pts.ndim != 2 or len(pts) < 4:
        return none, none
    pts = pts[:, :2]
    segs = shapely.linestrings(np.stack([pts[:-1], pts[1:]], axis=1))
    i, j = shapely.STRtree(segs).query(segs, predicate='crosses')
    keep = j > i + 1
    if not keep.any():
        return none, none
    i, j = i[keep], j[keep]
    d = np.diff(pts, axis=0)
    d /= np.maximum(np.linalg.norm(d, axis=1), 1e-12)[:, None]
    cos = np.abs(np.einsum('ij,ij->i', d[i], d[j]))
    sharp = cos < math.cos(math.radians(min_angle_deg))
    return i[sharp], j[sharp]


def count_crossings(coords, min_angle_deg=CROSSING_MIN_ANGLE_DEG):
    """Pairs of non-adjacent segments that properly cross at more than
    `min_angle_deg`. Touching, retracing or near-parallel overlap at a seam
    (numerically an X at ~0 deg) is not a crossing; tow laid on tow is
    measured by the coverage metric instead. One bulk STRtree query."""
    return len(crossing_pairs(coords, min_angle_deg)[0])


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


def enforce_min_radius_clear(path, min_rad, keepouts=(), clearance=0.0, max_iter=6):
    """enforce_min_radius, but never moving fibre into a keepout (a hole
    grown by `clearance`): a point the smoothing pushed in goes back to where
    it was (the original path keeps clear of holes), and so do both ends of
    any segment that still cuts a keepout. The bend left there is tighter
    than min_rad and is reported by the curvature audit, instead of the
    fibre being laid over the hole. (Rings that bend round a hole meet the
    straight part at a sharp inside corner; rounding it cut ~2 mm across a
    hole edge -- found by the property tests.) Nor across the path's own
    fibre: a point whose move made a crossing goes back too (a strict Fermat
    layer's corner nudged across the arm a gap away)."""
    smooth = enforce_min_radius(path, min_rad)
    if len(smooth) < 2:
        return smooth
    orig = [(float(x), float(y)) for x, y in path]
    out = list(smooth)
    zones = [k.buffer(clearance) if clearance else k for k in keepouts if not k.is_empty]
    if zones:
        region = shapely.union_all(zones)
        for _ in range(max_iter):
            pts = np.asarray(out, dtype=np.float64)
            inside = shapely.contains_xy(region, pts[:, 0], pts[:, 1])
            segs = shapely.linestrings(np.stack([pts[:-1], pts[1:]], axis=1))
            hit = shapely.intersects(segs, region)
            bad = set(np.nonzero(inside)[0].tolist())
            for i in np.nonzero(hit)[0].tolist():
                bad.update((i, i + 1))
            bad = [i for i in bad if out[i] != orig[i]]
            if not bad:
                break
            for i in bad:
                out[i] = orig[i]
    if out != orig:
        before = count_crossings(orig)
        for _ in range(max_iter):
            i, j = crossing_pairs(out)
            if len(i) <= before:
                break
            bad = [k for k in set(np.concatenate([i, i + 1, j, j + 1]).tolist()) if out[k] != orig[k]]
            if not bad:
                break
            for k in bad:
                out[k] = orig[k]
    return out


def audit_min_radius(path, min_rad):
    """Count vertices where circumradius < min_rad. Returns (violations, total_interior)."""
    if min_rad <= 0 or len(path) < 3:
        return 0, 0
    pts = np.array(path, dtype=np.float64)[:, :2]
    radii = _circumradii(pts)
    violations = int(np.sum(radii < min_rad))
    return violations, len(radii)


