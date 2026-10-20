"""Strict bend radius (Phase 43): no fibre bend tighter than r_min.

A bend tighter than r_min (a zigzag U-turn, the Fermat centre turn, a
join between runs, a lane change) is replaced by the shortest curve whose
radius never drops below r_min (a Dubins path: arcs of radius R and
straight lines) between a point before it and a point after it. The curve
must stay on the part, clear of holes, and must not cross or lie on fibre
of the same layer. If no such curve exists, the stretch between the two
points is left empty (a longer piece is skipped), and as a last resort the
layer starts later or ends earlier. Lengths in mm, angles in radians.
"""
from __future__ import annotations

import math

import numpy as np
import shapely
from shapely.geometry import LineString
from shapely.prepared import prep

from ._common import CROSSING_MIN_ANGLE_DEG, local_bend_radii

TIGHT = 0.995          # repair bends under this share of r_min (the audit fails under 0.98)
ARC_FACTOR = 1.03      # Dubins arcs at 3 % over r_min: sampling and splices read up to ~1 % under
_STEP = 0.25           # sample spacing of a Dubins curve
# How far before / after a tight stretch a replacing curve may start / end
# (the same both ways: an outward layer is an inward one run backwards).
_REACH = (1.0, 2.0, 3.5, 5.0, 8.0, 12.0, 18.0, 27.0, 40.0, 60.0, 90.0, 135.0, 200.0, 300.0, 450.0, 700.0)
_MAX_WINDOWS = 160
_SPLICE_CHECK = 3.0    # mm of path either side of a splice whose bends are re-checked
_MAX_REPAIRS = 400


def _mod2pi(a):
    return a % (2.0 * math.pi)


def dubins_words(p0, h0, p1, h1, r):
    """Every Dubins word (LSL, RSR, LSR, RSL, RLR, LRL) from p0 heading h0
    to p1 heading h1 with turning radius r: [(word, (t, p, q))], segment
    lengths in units of r (arcs: angle turned). Formulas after Shkel and
    Lumelsky (2001); each word is re-checked by sampling it (dubins_points)."""
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    D = math.hypot(dx, dy)
    d = D / r
    th = math.atan2(dy, dx) if D > 1e-12 else 0.0
    a, b = _mod2pi(h0 - th), _mod2pi(h1 - th)
    sa, sb, ca, cb = math.sin(a), math.sin(b), math.cos(a), math.cos(b)
    cab = math.cos(a - b)
    out = []
    tmp = 2.0 + d * d - 2.0 * cab + 2.0 * d * (sa - sb)
    if tmp >= 0.0:
        t1 = math.atan2(cb - ca, d + sa - sb)
        out.append(("LSL", (_mod2pi(-a + t1), math.sqrt(tmp), _mod2pi(b - t1))))
    tmp = 2.0 + d * d - 2.0 * cab + 2.0 * d * (sb - sa)
    if tmp >= 0.0:
        t1 = math.atan2(ca - cb, d - sa + sb)
        out.append(("RSR", (_mod2pi(a - t1), math.sqrt(tmp), _mod2pi(-b + t1))))
    tmp = -2.0 + d * d + 2.0 * cab + 2.0 * d * (sa + sb)
    if tmp >= 0.0:
        p = math.sqrt(tmp)
        t1 = math.atan2(-ca - cb, d + sa + sb) - math.atan2(-2.0, p)
        out.append(("LSR", (_mod2pi(-a + t1), p, _mod2pi(-b + t1))))
    tmp = d * d - 2.0 + 2.0 * cab - 2.0 * d * (sa + sb)
    if tmp >= 0.0:
        p = math.sqrt(tmp)
        t1 = math.atan2(ca + cb, d - sa - sb) - math.atan2(2.0, p)
        out.append(("RSL", (_mod2pi(a - t1), p, _mod2pi(b - t1))))
    tmp = (6.0 - d * d + 2.0 * cab + 2.0 * d * (sa - sb)) / 8.0
    if abs(tmp) <= 1.0:
        p = _mod2pi(2.0 * math.pi - math.acos(tmp))
        t = _mod2pi(a - math.atan2(ca - cb, d - sa + sb) + p / 2.0)
        out.append(("RLR", (t, p, _mod2pi(a - b - t + p))))
    tmp = (6.0 - d * d + 2.0 * cab + 2.0 * d * (sb - sa)) / 8.0
    if abs(tmp) <= 1.0:
        p = _mod2pi(2.0 * math.pi - math.acos(tmp))
        t = _mod2pi(-a - math.atan2(ca - cb, d + sa - sb) + p / 2.0)
        out.append(("LRL", (t, p, _mod2pi(b - a - t + p))))
    return out


def dubins_points(p0, h0, word, params, r, step=_STEP):
    """Points of one Dubins word after p0, about `step` apart, ending at the
    goal. Returns (points, end heading)."""
    x, y, h = float(p0[0]), float(p0[1]), float(h0)
    parts = []
    for kind, s in zip(word, params, strict=True):
        length = s * r
        n = max(1, int(math.ceil(length / step)))
        f = np.arange(1, n + 1, dtype=np.float64) / n
        if kind == "S":
            parts.append(np.column_stack([x + length * f * math.cos(h), y + length * f * math.sin(h)]))
            x, y = x + length * math.cos(h), y + length * math.sin(h)
            continue
        sign = 1.0 if kind == "L" else -1.0
        cx, cy = x - sign * r * math.sin(h), y + sign * r * math.cos(h)
        hk = h + sign * s * f
        parts.append(np.column_stack([cx + sign * r * np.sin(hk), cy - sign * r * np.cos(hk)]))
        h += sign * s
        x, y = float(parts[-1][-1, 0]), float(parts[-1][-1, 1])
    return [tuple(q) for q in np.vstack(parts).tolist()], h


def dubins_curves(p0, h0, p1, h1, r, step=_STEP):
    """Every Dubins curve from (p0, h0) to (p1, h1), shortest first: lists
    of points after p0 ending at p1."""
    out = []
    for word, params in dubins_words(p0, h0, p1, h1, r):
        pts, h = dubins_points(p0, h0, word, params, r, step)
        if not pts or math.dist(pts[-1], p1) > 1e-3 * max(r, 1.0):
            continue
        if abs(math.remainder(h - h1, 2.0 * math.pi)) > 1e-3:
            continue
        pts[-1] = (float(p1[0]), float(p1[1]))
        out.append((sum(params) * r, pts))
    out.sort(key=lambda c: c[0])
    return [pts for _, pts in out]


def _heading(a, b):
    return math.atan2(b[1] - a[1], b[0] - a[0])


class _Path:
    """A polyline with its cumulative length, for cutting at a distance."""

    def __init__(self, pts):
        self.pts = np.asarray(pts, dtype=np.float64).reshape(-1, 2)
        seg = np.linalg.norm(np.diff(self.pts, axis=0), axis=1)
        self.cum = np.concatenate([[0.0], np.cumsum(seg)])
        self.length = float(self.cum[-1])

    def at(self, s):
        """(segment index k, point, heading of segment k) at distance s."""
        k = int(np.clip(np.searchsorted(self.cum, s, side='right') - 1, 0, len(self.pts) - 2))
        a, b = self.pts[k], self.pts[k + 1]
        seg = self.cum[k + 1] - self.cum[k]
        f = 0.0 if seg < 1e-12 else min(max((s - self.cum[k]) / seg, 0.0), 1.0)
        p = a + (b - a) * f
        return k, (float(p[0]), float(p[1])), _heading(a, b)


def tight_clusters(pts, r_min, start=0.0):
    """[(s_from, s_to)] path distances of runs of vertices bending tighter
    than TIGHT·r_min, at or after `start`; runs closer than 2·r_min merged."""
    P = _Path(pts)
    if len(P.pts) < 3 or r_min <= 0:
        return []
    rad = local_bend_radii(P.pts)
    idx = np.nonzero(rad < TIGHT * r_min)[0] + 1
    s = P.cum[idx]
    s = s[s >= start]
    out = []
    for v in s.tolist():
        if out and v - out[-1][1] <= 2.0 * r_min:
            out[-1][1] = v
        else:
            out.append([v, v])
    return [tuple(c) for c in out]


def _segments(pts):
    arr = np.asarray(pts, dtype=np.float64).reshape(-1, 2)
    if len(arr) < 2:
        return None, None, None
    segs = shapely.linestrings(np.stack([arr[:-1], arr[1:]], axis=1))
    d = np.diff(arr, axis=0)
    d /= np.maximum(np.linalg.norm(d, axis=1), 1e-12)[:, None]
    return shapely.STRtree(segs), segs, d


def walk(pts, length):
    """The leading points of `pts` up to `length` mm of travel, plus the
    next one (so every bend within `length` is an interior vertex)."""
    out, run = list(pts[:1]), 0.0
    for q in pts[1:]:
        out.append(q)
        if run >= length:
            break
        run += math.dist(out[-2], q)
    return out


class StrictPath(list):
    """A repaired path: `lost` is how much of the path was left out (mm);
    `events` [(kind, from mm, to mm)] what each repair did: 'curve' (a
    Dubins curve replaced that stretch), 'later start', 'early end'."""
    lost = 0.0
    repairs = 0
    events = ()


def strict_path(path, r_min, allowed, gap, keep_head=0):
    """`path` with every bend under TIGHT·r_min replaced by a Dubins curve
    at ARC_FACTOR·r_min that stays in `allowed` (where fibre may lie),
    crosses no other fibre of the path and keeps half a gap off it.
    The first `keep_head` points (the previous layer's end, prepended to
    keep the stroke continuous) are never changed, and the bend at the last
    of them is not judged: the layer climb replaces it.

    Bends that cannot be replaced: the stretch round them is skipped by a
    longer curve, or the layer starts after it (near its start) or ends
    before it. Returns a StrictPath."""
    out = StrictPath(path)
    if r_min <= 0 or len(path) < 3:
        return out
    pts = _dedupe([(float(x), float(y)) for x, y in path])
    original_len = _Path(pts).length
    R = ARC_FACTOR * r_min
    zone = prep(allowed) if allowed is not None and not allowed.is_empty else None
    keep = max(int(keep_head), 0)
    head_s = _Path(pts[:keep + 1]).length if keep else 0.0    # where the layer proper starts
    low = head_s + (BEND_GUARD if keep else 0.0)
    cursor, repairs, events = low, 0, []
    while repairs < _MAX_REPAIRS:
        P = _Path(pts)
        clusters = tight_clusters(pts, r_min, cursor)
        if not clusters and cursor > low:
            # a repair's own end can leave a bend behind the cursor: look again
            # from the layer's start before giving up
            cursor = low
            clusters = tight_clusters(pts, r_min, cursor)
        if not clusters:
            break
        sa, sb = clusters[0]
        repairs += 1
        fixed = _repair(P, pts, sa, sb, R, r_min, zone, gap, head_s, keep)
        if fixed is None:
            break
        pts, cursor, event = fixed
        events.append(event)
    out = StrictPath(pts)
    out.lost = max(0.0, original_len - _Path(pts).length) if repairs else 0.0
    out.repairs = repairs
    out.events = tuple(events)
    # Anything still tight (a pass limit): end the layer before it.
    left = tight_clusters(pts, r_min, low)
    if left:
        P = _Path(pts)
        k, p, _ = P.at(max(left[0][0] - 1.0, head_s))
        cut = StrictPath([tuple(q) for q in pts[:k + 1]] + [p])
        cut.lost = out.lost + (P.length - left[0][0] + 1.0)
        cut.repairs = repairs
        cut.events = tuple(events) + (("early end", left[0][0] - 1.0, P.length),)
        return cut
    return out


BEND_GUARD = 1.0       # mm after the kept head where the climb's own join is not judged


def _repair(P, pts, sa, sb, R, r_min, zone, gap, head_s, keep):
    """Replace the tight stretch [sa, sb]: returns (new points, distance
    from which to look for the next bend, event)."""
    total = P.length
    low = head_s + (BEND_GUARD if keep else 0.0)
    whole = _segments(pts)
    windows = []
    for back in _REACH:
        si = sa - back
        if si < low:
            break
        for fwd in _REACH:
            sj = sb + fwd
            if sj > total - 0.5:
                break
            windows.append((sj - si, si, sj))
    windows.sort()
    # Two passes: the shortest curve of every window first, then the
    # longer ones (loops), which are rarely needed (the Fermat centre turn)
    # and mostly lie on other fibre.
    found = {}
    for first in (True, False):
        for _, si, sj in windows[:_MAX_WINDOWS]:
            ki, pi, hi = P.at(si)
            kj, pj, hj = P.at(sj)
            if kj <= ki and math.dist(pi, pj) < 1e-6:
                continue
            if (si, sj) not in found:
                found[si, sj] = dubins_curves(pi, hi, pj, hj, R)
            curves = found[si, sj][:1] if first else found[si, sj][1:]
            done = _try_curves(P, pts, curves, si, sj, ki, kj, pi, pj, r_min, zone, gap, whole)
            if done is not None:
                return done
    # Nothing joins round it: start the layer after it, or end before it.
    lost_head = sb + 1.0 - head_s
    lost_tail = total - (sa - 1.0)
    if lost_head < lost_tail and sb + 1.0 < total:
        kj, pj, _ = P.at(sb + 1.0)
        new = [tuple(q) for q in pts[:max(keep, 0)]] + [pj] + [tuple(q) for q in pts[kj + 1:]]
        return _dedupe(new), low, ("later start", head_s, sb + 1.0)
    ki, pi, _ = P.at(max(sa - 1.0, low))
    new = [tuple(q) for q in pts[:ki + 1]] + [pi]
    return _dedupe(new), total + 1.0, ("early end", max(sa - 1.0, low), total)


def _try_curves(P, pts, curves, si, sj, ki, kj, pi, pj, r_min, zone, gap, whole):
    """The first of `curves` (from pi at si to pj at sj) that may replace
    that stretch: (new points, where to look next, event), or None."""
    exempt = None
    for curve in curves:
        line = shapely.linestrings(np.asarray([pi] + curve, dtype=np.float64))
        if zone is not None and not zone.covers(line):
            continue
        if not line.is_simple:                     # a Dubins loop can cross itself
            continue
        if exempt is None:
            exempt = _already_near(P, whole, ki, kj, pi, pj, gap)
        if not _clear_of_rest(curve, pi, whole, ki, kj, gap, P.cum, exempt):
            continue
        # the kept pieces of the two cut segments
        ends = [LineString([tuple(P.pts[ki]), pi]) if math.dist(tuple(P.pts[ki]), pi) > 1e-6 else None,
                LineString([pj, tuple(P.pts[kj + 1])]) if math.dist(pj, tuple(P.pts[kj + 1])) > 1e-6 else None]
        if any(e is not None and line.crosses(e) for e in ends):
            continue
        # Only the splice changes: dedupe it alone (the rest already is)
        # and re-check the bends round it on that stretch alone.
        splice = _dedupe([tuple(pts[ki]), pi] + curve[:-1] + [pj, tuple(pts[kj + 1])])
        k0 = min(int(np.searchsorted(P.cum, si - _SPLICE_CHECK - 2.0)), ki)
        k1 = int(np.searchsorted(P.cum, sj + _SPLICE_CHECK + 2.0))
        local = [tuple(q) for q in pts[k0:ki]] + splice + [tuple(q) for q in pts[kj + 2:k1 + 1]]
        if _tight_near(local, r_min, si - _SPLICE_CHECK - P.cum[k0], si + line.length + _SPLICE_CHECK - P.cum[k0]):
            continue
        new = [tuple(q) for q in pts[:ki]] + splice + [tuple(q) for q in pts[kj + 2:]]
        return new, si + line.length, ("curve", si, sj)
    return None


def _tight_near(pts, r_min, s0, s1):
    """True if a vertex between path distances s0 and s1 bends under
    TIGHT·r_min."""
    Q = _Path(pts)
    if len(Q.pts) < 3:
        return False
    rad = local_bend_radii(Q.pts)
    s = Q.cum[1:-1]
    return bool(((rad < TIGHT * r_min) & (s >= s0) & (s <= s1)).any())


def _already_near(P, whole, ki, kj, pi, pj, gap):
    """Segments outside the replaced stretch that already lay near it:
    (within half a gap -- the new curve may come as near them; within
    0.1 mm, a retrace such as a spiral's outer lap closing over its own
    start -- the new curve may also cross them)."""
    tree = whole[0]
    none = np.zeros(0, dtype=np.int64)
    if tree is None:
        return none, none
    old = [pi] + [(float(q[0]), float(q[1])) for q in P.pts[ki + 1:kj + 1]] + [pj]
    if len(old) < 2:
        return none, none
    out = []
    for dist in (0.5 * gap + 0.05, 0.1):
        j = tree.query(LineString(old), predicate='dwithin', distance=dist)
        out.append(j[(j < ki) | (j > kj)])
    return out[0], out[1]


def _clear_of_rest(curve, start, whole, ki, kj, gap, cum, exempt=((), ())):
    """The curve crosses no segment outside the replaced stretch and keeps
    0.5·gap off them (away from its own two ends). `exempt` (_already_near):
    segments the old stretch already came that near / retraced."""
    tree, segs, dirs = whole
    if tree is None:
        return True
    ex = np.zeros(len(dirs), dtype=bool)
    on = np.zeros(len(dirs), dtype=bool)
    if len(exempt[0]):
        ex[np.asarray(exempt[0], dtype=np.int64)] = True
    if len(exempt[1]):
        on[np.asarray(exempt[1], dtype=np.int64)] = True
    arr = np.asarray([start] + list(curve), dtype=np.float64)
    lines = shapely.linestrings(np.stack([arr[:-1], arr[1:]], axis=1))
    i, j = tree.query(lines, predicate='crosses')
    keep = ((j < ki) | (j > kj)) & ~on[j]
    if keep.any():
        d = np.diff(arr, axis=0)
        d /= np.maximum(np.linalg.norm(d, axis=1), 1e-12)[:, None]
        cos = np.abs(np.einsum('ij,ij->i', d[i[keep]], dirs[j[keep]]))
        if np.count_nonzero(cos < math.cos(math.radians(CROSSING_MIN_ANGLE_DEG))):
            return False
    # Lying on fibre: points of the curve away from its ends closer than
    # half a gap to segments that are not next to the splice.
    seg_len = np.linalg.norm(np.diff(arr, axis=0), axis=1)
    run = np.concatenate([[0.0], np.cumsum(seg_len)])
    total = run[-1]
    inner = (run > 1.5 * gap) & (run < total - 1.5 * gap)
    if not inner.any():
        return True
    _, pj = tree.query(shapely.points(arr[inner]), predicate='dwithin', distance=0.5 * gap)
    if not len(pj):
        return True
    near_i = cum[min(ki + 1, len(cum) - 1)]
    near_j = cum[min(kj, len(cum) - 1)]
    seg_s = cum[pj]
    far = (seg_s < near_i - 2.0 * gap) | (seg_s > near_j + 2.0 * gap)
    far &= ((pj < ki) | (pj > kj)) & ~ex[pj]
    return not bool(far.any())


def _dedupe(pts, tol=0.05):
    out = [pts[0]]
    for q in pts[1:]:
        if math.dist(q, out[-1]) > tol:
            out.append(q)
    if len(out) > 1 and math.dist(pts[-1], out[-1]) > 1e-9:
        out[-1] = pts[-1]
    return out


def lost_area(before, after, region, gap):
    """mm² of `region` the tow covered on `before` but not on `after`."""
    if len(before) < 2 or region is None or region.is_empty:
        return 0.0
    try:
        a = shapely.make_valid(LineString(before).buffer(gap / 2.0)).intersection(region)
        b = shapely.make_valid(LineString(after).buffer(gap / 2.0)) if len(after) > 1 else None
        return float(a.area if b is None else a.difference(b).area)
    except shapely.errors.GEOSException:
        # a report figure only: the length left out times the tow width
        short = LineString(before).length - (LineString(after).length if len(after) > 1 else 0.0)
        return max(0.0, short) * gap


def _cut_points(pts, lo, hi, from_end):
    """Distances (from the start, or from the end with `from_end`) between
    lo and hi where a polyline may be cut for a join: its vertices, and
    points of straight segments over 3 mm at least 1.5 mm from their ends
    (a cut mid-arc reads ~2 % under the arc's radius); at most ~40."""
    seq = pts[::-1] if from_end else pts
    out, s = [0.0] if lo <= 0.0 else [], 0.0
    for a, b in zip(seq[:-1], seq[1:], strict=False):
        seg = math.dist(a, b)
        if seg >= 3.0:
            t = 1.5
            while t <= seg - 1.5:
                if lo <= s + t <= hi:
                    out.append(s + t)
                t += 1.0
        s += seg
        if lo <= s <= hi:
            out.append(s)
        if s > hi:
            break
    if len(out) > 40:
        step = (len(out) - 1) / 39.0
        out = [out[round(i * step)] for i in range(40)]
    return out


def smooth_join(prev, nxt, r_min, gap, zone=None, back=25.0, ahead=25.0):
    """Join polyline `prev` (laid so far) to `nxt` (laid next) with a curve
    that never bends under r_min: cut up to `back` mm off prev's end and
    `ahead` mm off nxt's start, at vertices or inside straight runs, and lay
    a Dubins curve (ARC_FACTOR or 1.005 x r_min) between the cuts along
    their headings. Pairs are tried least fibre dropped first until the
    curve stays in `zone`, keeps every bend of the join at r_min, crosses
    neither line (over 5 degrees) and keeps half a gap off them (away from
    its ends). Returns (prev kept, curve points ending at nxt's cut, nxt
    after the cut), or None when no pair works."""
    if len(prev) < 2 or len(nxt) < 2 or r_min <= 0:
        return None
    A, B = _Path(prev), _Path(nxt)
    area = prep(zone) if zone is not None and not zone.is_empty else None
    tails = _cut_points(prev, 0.0, min(back, 0.45 * A.length), from_end=True)
    heads = _cut_points(nxt, 0.0, min(ahead, 0.45 * B.length), from_end=False)
    pairs = sorted(((t + d, t, d) for t in tails for d in heads), key=lambda q: q[0])[:240]
    cuts = {}       # each cut (and its segment index) once: the pairs share them

    def cut(side, at):
        key = (side, at)
        if key not in cuts:
            if side:
                k, q, h = A.at(A.length - at)
                part = _dedupe(list(map(tuple, A.pts[:k + 1].tolist())) + [q])
            else:
                k, q, h = B.at(at)
                part = _dedupe([q] + list(map(tuple, B.pts[k + 1:].tolist())))
            cuts[key] = (q, h, part, _Lines(part) if len(part) >= 2 else None)
        return cuts[key]

    for _, t, d in pairs:
        pi, hi, kept, kept_lines = cut(True, t)
        pj, hj, rest, rest_lines = cut(False, d)
        if kept_lines is None or rest_lines is None:
            continue
        for R in (ARC_FACTOR * r_min, 1.005 * r_min):
            for curve in dubins_curves(pi, hi, pj, hj, R):
                arr = np.asarray([pi] + curve, dtype=np.float64)
                line = shapely.linestrings(arr)
                if area is not None and not area.covers(line):
                    continue
                if not line.is_simple:
                    continue
                if kept_lines.hits(arr, gap, at_start=True) or rest_lines.hits(arr, gap, at_start=False):
                    continue
                joined = walk(kept[::-1], 3.0)[::-1][:-1] + [pi] + curve + walk(rest, 3.0)[1:]
                if _tight_near(joined, r_min, 0.0, _Path(joined).length):
                    continue
                return kept, curve, rest
    return None


class _Lines:
    """A polyline's segments in an STRtree, for curves joined to one end."""

    def __init__(self, pts):
        self.tree, _, self.dirs = _segments(pts)
        self.cum = _Path(pts).cum

    def hits(self, arr, gap, at_start):
        """True if the curve `arr` crosses the line (over 5 degrees) or
        comes within half a gap of it away from the end it shares with it
        (`at_start`: the line ends where arr starts; else it starts where
        arr ends)."""
        segs = shapely.linestrings(np.stack([arr[:-1], arr[1:]], axis=1))
        i, j = self.tree.query(segs, predicate='crosses')
        if len(i):
            d = np.diff(arr, axis=0)
            d /= np.maximum(np.linalg.norm(d, axis=1), 1e-12)[:, None]
            cos = np.abs(np.einsum('ij,ij->i', d[i], self.dirs[j]))
            if np.count_nonzero(cos < math.cos(math.radians(5.0))):
                return True
        run = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(arr, axis=0), axis=1))])
        free = (run > 1.5 * gap) if at_start else (run < run[-1] - 1.5 * gap)
        if not free.any():
            return False
        # the line's own stretch next to the shared end is exempt too
        _, j = self.tree.query(shapely.points(arr[free]), predicate='dwithin', distance=0.5 * gap)
        if not len(j):
            return False
        cum = self.cum
        near_end = (cum[j] > cum[-1] - 2.0 * gap) if at_start else (cum[j] < 2.0 * gap)
        return bool((~near_end).any())


__all__ = ["ARC_FACTOR", "BEND_GUARD", "StrictPath", "TIGHT", "dubins_curves", "lost_area", "smooth_join", "strict_path", "tight_clusters", "walk"]
