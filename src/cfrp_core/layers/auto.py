"""Auto (Best Fit) infill: on each layer every pattern is tried -- the three
spirals filled solid, zigzags at the layer's layup angle, across it, along
the part's long side and at ±45° -- and the one that keeps the fibre rules
best is laid: never over a hole, no crossings, fewest bends tighter than
the minimum, most of the layer covered, a short way in from the previous
layer. Choices are reused for later layers that look the same (same
direction, same angle, same outline), so a 20-layer part is judged once
or twice, not 20 times."""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from shapely.geometry import LineString

from ..config import ARCHIMEDEAN, CORNER_STEP, FERMAT, ORIENTED_SERPENTINE
from ..geometry._common import BEND_WINDOW_MM, count_crossings, local_bend_radii

SPIRAL_CANDIDATES = (ARCHIMEDEAN, CORNER_STEP, FERMAT)
TIGHT_WEIGHT = 3.0          # one share of the path in tight bends costs three shares of coverage
CROSSING_COST = 0.1         # per crossing (validation warns about each)
JUMP_COST = 0.002           # per mm from the previous layer's end to the start


@dataclass(frozen=True)
class Choice:
    pattern: str
    angle: float
    coverage: float         # share of the layer covered by the tow
    tight: float            # share of the path in bends tighter than the minimum
    crossings: int
    score: float


def zigzag_angles(part, layup_angle):
    """Zigzag directions worth trying (mod 180): the layup angle, across
    it, along the part's long side (fewest turns), across that, ±45."""
    out = [float(layup_angle), float(layup_angle) + 90.0, 45.0, -45.0]
    rect = part.minimum_rotated_rectangle
    if rect.geom_type == 'Polygon':
        c = list(rect.exterior.coords)
        e1, e2 = (c[0], c[1]), (c[1], c[2])
        a, b = e1 if math.dist(*e1) >= math.dist(*e2) else e2
        long_side = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
        out += [long_side, long_side + 90.0]
    seen, uniq = set(), []
    for ang in out:
        key = round(((ang + 90.0) % 180.0) - 90.0, 3)
        if key not in seen:
            seen.add(key)
            uniq.append(key)
    return uniq


def measure(path, part, holes, gap, r_min, last_pt=None):
    """(coverage, tight share, crossings, jump mm) of a candidate path, or
    None if it lays fibre over a hole."""
    pts = np.asarray(path, dtype=np.float64)
    if len(pts) < 2:
        return None
    line = LineString(pts)
    if any(line.intersects(h) for h in holes):
        return None
    seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    rad = local_bend_radii(pts)
    # a tight vertex stands for the bend round it, at most a bend window long
    # (a zigzag line is two points: weighting by its half length counted the
    # whole line as tight)
    half = BEND_WINDOW_MM / 2.0
    near = (np.minimum(seg[:-1], half) + np.minimum(seg[1:], half)) if len(seg) > 1 else np.zeros(0)
    tight = float(near[rad < 0.98 * r_min].sum() / max(seg.sum(), 1e-9)) if r_min > 0 and len(near) else 0.0
    coverage = line.buffer(gap / 2.0).intersection(part).area / max(part.area, 1e-9)
    jump = math.dist(last_pt, pts[0]) if last_pt is not None else 0.0
    return coverage, tight, count_crossings(pts), jump


def choose(generate, candidates, part, holes, gap, r_min, last_pt=None, strict=False, repair=None):
    """Best of `candidates` [(pattern, angle)]: `generate(pattern, angle)`
    returns a path (or None / raises ValueError when it cannot). Returns
    (Choice, path), or (None, None) if nothing could be laid. `strict`
    (Strict Bend Radius): a candidate with any bend under r_min is out.

    `repair` (Strict Bend Radius): makes a generated path keep r_min. It is
    costly, so candidates are first judged unrepaired and repaired best
    first; a repair only leaves fibre out, so one whose unrepaired score
    (tight bends not counted) cannot beat the best repaired one so far is
    never repaired."""
    if repair is not None:
        return _choose_repaired(generate, candidates, part, holes, gap, r_min, last_pt, repair)
    best = None
    for pattern, angle in candidates:
        try:
            path = generate(pattern, angle)
        except ValueError:
            continue
        best = _better(best, pattern, angle, path, part, holes, gap, r_min, last_pt, strict)
    return best if best is not None else (None, None)


_REPAIR_SLACK = 0.01        # a repair's curve may cover a little more than the stretch it replaced


def _choose_repaired(generate, candidates, part, holes, gap, r_min, last_pt, repair):
    raw = []
    for pattern, angle in candidates:
        try:
            path = generate(pattern, angle)
        except ValueError:
            continue
        if not path or len(path) < 2:
            continue
        m = measure(path, part, holes, gap, r_min, last_pt)
        if m is None:
            continue
        coverage, _, _, jump = m
        raw.append((coverage + _REPAIR_SLACK - JUMP_COST * jump, pattern, angle, path))
    raw.sort(key=lambda r: -r[0])
    best = None
    for bound, pattern, angle, path in raw:
        if best is not None and bound <= best[0].score + 1e-9:
            break
        best = _better(best, pattern, angle, repair(path), part, holes, gap, r_min, last_pt, True)
    return best if best is not None else (None, None)


def _better(best, pattern, angle, path, part, holes, gap, r_min, last_pt, strict):
    """`best` or this candidate, whichever scores higher."""
    if not path or len(path) < 2:
        return best
    m = measure(path, part, holes, gap, r_min, last_pt)
    if m is None:
        return best
    coverage, tight, crossings, jump = m
    if strict:
        if _strict_tight(path, r_min, last_pt):
            return best
        tight = 0.0             # only the join to the last layer, which the climb replaces
    score = coverage - TIGHT_WEIGHT * tight - CROSSING_COST * crossings - JUMP_COST * jump
    if best is None or score > best[0].score + 1e-9:
        return (Choice(pattern, angle, coverage, tight, crossings, score), path)
    return best


def _strict_tight(path, r_min, last_pt):
    """True when the path bends under r_min anywhere but at its join to
    the previous layer's end (the climb replaces that)."""
    from ..geometry.strict import BEND_GUARD, tight_clusters
    start = 0.0
    if last_pt is not None and len(path) > 1 and math.dist(path[0], last_pt) < 1e-6:
        start = math.dist(path[0], path[1]) + BEND_GUARD
    return bool(tight_clusters(path, r_min, start))


def candidates_for(part, layup_angle):
    return [(p, 0.0) for p in SPIRAL_CANDIDATES] + [(ORIENTED_SERPENTINE, a)
                                                     for a in zigzag_angles(part, layup_angle)]


def summary(chosen) -> str:
    """'Auto (Best Fit): layer(s) 1-4 Corner-Step Spiral (90 % covered, no
    tight bends); ...' from [(layer number, Choice)]."""
    if not chosen:
        return ""
    groups: dict[tuple, list[int]] = {}
    for layer, c in chosen:
        name = c.pattern if c.pattern != ORIENTED_SERPENTINE else f"zigzag at {c.angle:g}°"
        tight = "no tight bends" if c.tight < 0.005 else f"{c.tight * 100:.0f} % of the path in tight bends"
        groups.setdefault((name, f"{c.coverage * 100:.0f} % covered, {tight}"), []).append(layer)
    parts = []
    for (name, how), layers in groups.items():
        spans, start = [], layers[0]
        for prev, cur in zip(layers, [*layers[1:], None], strict=True):
            if cur != prev + 1:
                spans.append(f"{start}" if start == prev else f"{start}-{prev}")
                start = cur
        parts.append(f"layer(s) {', '.join(spans)}: {name} ({how})")
    return "Auto (Best Fit): " + "; ".join(parts) + "."
