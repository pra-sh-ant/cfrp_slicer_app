"""Wind turbine blade (Z): airfoil cross-sections along a span that runs up Z.

The blade stands on its root and is laid as one helical stroke to the tip,
like the other Z shells (layers/revolve.py). From the bed up, at height h:

    h < root length            the root: a rounded rectangle (width along the
                               chord x thickness, its flat faces square to the
                               thickness), a rounded square (width on every
                               side) or a circle, straight; it is clamped
                               between the hub's discs (geometry/hub.py)
    then, s = (h - root) / L   the blade, L = stem_height_mm long:
      s < SHOULDER             the root shape morphs into the airfoil
      SHOULDER <= s <= 1       the airfoil: chord tapers linearly from the max
                               chord to the tip chord; twist falls from the set
                               twist to 0 at the tip as (1 - u)², most of it
                               near the root, as on real blades

The airfoil is a NACA 4-digit section (2 % camber at 40 % chord) of the set
thickness with a blunt ("flatback") trailing edge `te_thickness` thick: the
tow cannot turn round a sharp trailing edge, so the fillet would cut a sharp
one back to where the section is 2·r_min thick, losing the aft chord. The
pitch axis -- 30 % chord on the camber line, near the thickest point -- sits
at (start_x, start_y), the root's centre. The airfoil is turned about it by
pitch + twist against the root, whose faces stay square to the hub: turning
the leading edge towards the pressure side (into the wind).

Each layer is the section's rings laid as exact laps, joined by lane changes
on one seam (blade_ring_path).
"""
from __future__ import annotations

import math

import numpy as np
import shapely
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient

from ._common import dedupe_path, fillet, hole_keepouts, inset_components, largest_polygon, resample_closed_ring
from .corner_step import RING_PTS, _nearest_index, _tangent_bezier, _unit

SHOULDER = 0.30
# Rings (and the hollow and lane changes) are rounded at this times the min
# bend radius: a ring's ends are all bend (the innermost ring of a solid
# section is a sliver), and an arc of exactly r_min, offset as a polygon
# and sampled, reads up to ~2 % under it -- the audit's limit.
BEND_MARGIN = 1.05
CAMBER, CAMBER_POS = 0.02, 0.4
PITCH_AXIS = 0.3
_SURFACE_PTS = 120        # samples per surface (cosine-spaced: dense at the nose and the trailing edge)


def _camber(x):
    """NACA 4-digit mean line y_c(x) and its slope (x, y in chords)."""
    m, p = CAMBER, CAMBER_POS
    fore = x < p
    yc = np.where(fore, m / p ** 2 * (2 * p * x - x ** 2), m / (1 - p) ** 2 * (1 - 2 * p + 2 * p * x - x ** 2))
    dyc = np.where(fore, 2 * m / p ** 2 * (p - x), 2 * m / (1 - p) ** 2 * (p - x))
    return yc, dyc


def _airfoil(beta, chord, thickness, te_thickness):
    """Section points at parameter `beta` (0 = trailing edge, upper surface
    to the nose at pi, lower surface back to 2*pi), in mm about the pitch
    axis, trailing edge towards +x. Thickness in chords, the closed-edge
    NACA formula plus a linear flatback that opens the edge to
    `te_thickness` mm."""
    x = (1.0 + np.cos(beta)) / 2.0
    yt = 5.0 * thickness * (0.2969 * np.sqrt(x) - 0.1260 * x - 0.3516 * x ** 2 + 0.2843 * x ** 3
                            - 0.1036 * x ** 4) + x * te_thickness / (2.0 * chord)
    yc, dyc = _camber(x)
    th = np.arctan(dyc)
    side = np.where(beta <= math.pi, 1.0, -1.0)
    px, py = x - side * yt * np.sin(th), yc + side * yt * np.cos(th)
    pitch_y = float(_camber(np.array(PITCH_AXIS))[0])
    return (px - PITCH_AXIS) * chord, (py - pitch_y) * chord


def root_size(geometry):
    """(width along the chord, thickness) of the blade root, mm."""
    if geometry.blade_root_shape == "Round":
        d = 2.0 * max(float(geometry.base_radius_mm), 1.0)
        return d, d
    if geometry.blade_root_shape == "Square":
        return float(geometry.blade_root_width_mm), float(geometry.blade_root_width_mm)
    return float(geometry.blade_root_width_mm), float(geometry.blade_root_thickness_mm)


def root_label(geometry):
    """The root in words: 'a 50 x 28 mm rectangle', 'a 36 mm square',
    'round, 40 mm across'."""
    w, t = root_size(geometry)
    if geometry.blade_root_shape == "Round":
        return f"round, {w:g} mm across"
    if geometry.blade_root_shape == "Square":
        return f"a {w:g} mm square"
    return f"a {w:g} x {t:g} mm rectangle"


def root_corner(geometry, gap, min_radius):
    """Corner radius of a rectangular or square root: its outer ring, half a
    gap in, keeps its bend (BEND_MARGIN·r_min)."""
    w, t = root_size(geometry)
    return min(BEND_MARGIN * float(min_radius) + 0.5 * float(gap), 0.5 * min(w, t))


def _root_points(geometry, angle, corner):
    """The root's outline where rays from its centre at `angle` (radians)
    leave it: a circle, or a rounded rectangle found by bisection on its
    signed distance (convex, so each ray leaves it once); a square is a
    rectangle with equal sides."""
    if geometry.blade_root_shape == "Round":
        r = max(float(geometry.base_radius_mm), 1.0)
        return r * np.cos(angle), r * np.sin(angle)
    w, t = root_size(geometry)
    a, b = 0.5 * w - corner, 0.5 * t - corner
    dx, dy = np.cos(angle), np.sin(angle)
    lo, hi = np.zeros_like(angle), np.full_like(angle, math.hypot(0.5 * w, 0.5 * t) + 1.0)
    for _ in range(48):
        mid = 0.5 * (lo + hi)
        qx, qy = np.abs(mid * dx) - a, np.abs(mid * dy) - b
        sdf = np.hypot(np.maximum(qx, 0.0), np.maximum(qy, 0.0)) + np.minimum(np.maximum(qx, qy), 0.0) - corner
        inside = sdf < 0.0
        lo, hi = np.where(inside, mid, lo), np.where(inside, hi, mid)
    return lo * dx, lo * dy


def blade_height(geometry):
    """Root length + blade length: the whole print's height, mm."""
    return float(geometry.blade_root_length_mm) + max(float(geometry.stem_height_mm), 1e-6)


def blade_planform(geometry, h):
    """(chord mm, airfoil angle deg = pitch + twist, morph 0..1) at height h
    above the first layer: morph 0 = the root shape, 1 = the airfoil."""
    g = geometry
    s = min(max((h - float(g.blade_root_length_mm)) / max(float(g.stem_height_mm), 1e-6), 0.0), 1.0)
    if s >= SHOULDER:
        u = (s - SHOULDER) / (1.0 - SHOULDER)
        chord = g.blade_chord_mm + (g.blade_tip_chord_mm - g.blade_chord_mm) * u
        return chord, g.blade_pitch_deg + g.blade_twist_deg * (1.0 - u) ** 2, 1.0
    t = s / SHOULDER
    return g.blade_chord_mm, g.blade_pitch_deg + g.blade_twist_deg, t * t * (3.0 - 2.0 * t)


def blade_section(geometry, h, te_thickness, corner):
    """The blade's outline at height h above the first layer (a Polygon,
    CCW), about (start_x, start_y). Root and airfoil are matched point for
    point (the root's ray angle = the airfoil's parameter, both turned by
    the airfoil's angle: trailing edge at 0, nose at pi) and blended in the
    transition."""
    g = geometry
    c, angle_deg, morph = blade_planform(g, h)
    a = math.radians(angle_deg)
    beta = np.concatenate([np.linspace(0.0, math.pi, _SURFACE_PTS + 1),
                           np.linspace(math.pi, 2.0 * math.pi, _SURFACE_PTS + 1)[1:]])
    rx, ry = _root_points(g, beta + a, corner)
    if morph > 0.0:
        ax, ay = _airfoil(beta, c, g.blade_thickness_pct / 100.0, te_thickness)
        ca, sa = math.cos(a), math.sin(a)
        ax, ay = ax * ca - ay * sa, ax * sa + ay * ca
        rx, ry = (1.0 - morph) * rx + morph * ax, (1.0 - morph) * ry + morph * ay
    # beta 2*pi is the lower trailing-edge corner; closing onto beta 0 lays the blunt edge
    poly = Polygon(list(zip((g.start_x_mm + rx).tolist(), (g.start_y_mm + ry).tolist(), strict=True)))
    return poly if poly.is_valid else poly.buffer(0)


def blade_cross_section(geometry, h, te_thickness, wall, min_radius, gap):
    """One Z-slice of the blade at height h: the section with its hollow
    (the section `wall` mm in, rounded like the rings at
    BEND_MARGIN·min_radius: they round their corners inwards, so a
    sharp-cornered hollow would come within their clearance there and stop
    them), or the whole section once the hollow closes."""
    outer = blade_section(geometry, h, te_thickness, root_corner(geometry, gap, min_radius))
    if outer.is_empty or wall <= 0:
        return outer
    inner = fillet(outer.buffer(-wall, join_style=1), min_radius * BEND_MARGIN)
    if inner.is_empty or inner.area < 1.0:
        return outer
    return outer.difference(largest_polygon(inner))


def blade_trailing_edge(min_radius, gap):
    """Thickness (mm) of the blunt trailing edge: the outer ring, half a gap
    in, keeps its bend (BEND_MARGIN·r_min) plus a gap's room to turn round it."""
    return 2.0 * (BEND_MARGIN * float(min_radius) + float(gap))


def _seam_point(poly, axis):
    """Midway along poly's (anticlockwise) outline from its far end along
    `axis` (dx, dy) to its near end: on an airfoil whose trailing edge lies
    along +axis, the middle of the upper surface, its flattest stretch."""
    ring = shapely.segmentize(poly.exterior, 0.25)
    closed = np.asarray(ring.coords, dtype=np.float64)[:, :2]
    cum = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(closed, axis=0), axis=1))])
    total = float(cum[-1])
    h = closed[:-1] @ np.asarray(axis, dtype=np.float64)

    def end(far):
        # the middle of the run of points at the end (a flat end square to
        # the axis); 0.01 mm slack: offsetting snaps points to a 1 um grid
        k0 = int(np.argmin(h) if far else np.argmax(h))    # start at the other end: the run cannot wrap
        order = (np.arange(len(h)) + k0) % len(h)
        lim = h.max() if far else h.min()
        run = order[np.abs(h[order] - lim) <= 0.01]
        return cum[run[0]] + ((cum[run[-1]] - cum[run[0]]) % total) / 2.0

    s_far, s_near = end(True), end(False)
    p = ring.interpolate((s_far + 0.5 * ((s_near - s_far) % total)) % total)
    return (p.x, p.y)


def blade_ring_path(section, gap, min_radius, outward, axis, brim=0):
    """One layer of the blade: rings gap/2 + k·gap in from the section's
    outline (each rounded at BEND_MARGIN·min_radius; until one would come
    within half a gap of the hollow, or vanishes), laid as exact laps
    joined by lane changes, as corner_step's stitched rings are, each ring
    starting midway along its side from the trailing to the leading edge
    (`axis` points to the trailing edge): the flattest stretch, where the
    layer connectors run too. Blending ring into ring over a lap, as the
    Archimedean spiral does, bends far tighter than either ring here: near
    the blunt trailing edge each ring is cut back well short of the one
    outside it, so the blend swings between two ends far apart.

    An inner ring with less straight side than a lane change is left out
    (and the rings inside it): a lane change onto or off it would land on
    its end. `outward`: innermost ring first.

    `brim` (first layer, laid inward): that many rings outside the section,
    a gap apart (the innermost touching its outer ring), laid first and
    joined to the part by the same lane changes. Returns (the path, a list of
    (x, y); how many of its points are brim, up to the part's outer ring)."""
    holes = hole_keepouts(section)
    bend = min_radius * BEND_MARGIN
    # a sideways shift of one gap over L bends at about L²/(6·gap) (as stitch_rings)
    lane_len = 1.5 * math.sqrt(6.0 * gap * bend) if bend > 0 else gap
    polys, inset = [], gap / 2.0
    while True:
        comps = inset_components(section, inset, bend, holes, gap / 2.0)
        if comps is None:
            break
        ring = orient(max(comps, key=lambda g: g.area), 1.0)
        if polys and ring.exterior.length < 2.0 * math.pi * bend + 2.0 * lane_len:
            break
        polys.append(ring)
        inset += gap
    if not polys:
        return [], 0
    outline = Polygon(section.exterior)
    brims = [orient(Polygon(outline.buffer(k * gap - 0.5 * gap, join_style=1).exterior), 1.0)
             for k in range(int(brim), 0, -1)] if brim and not outward else []
    polys = brims + polys
    rings = [resample_closed_ring(p, max(RING_PTS, int(math.ceil(p.exterior.length / 0.5)))) for p in polys]
    seams = [_seam_point(p, axis) for p in polys]
    order = list(zip(rings, seams, strict=True))
    if outward:
        order.reverse()
    path: list[tuple[float, float]] = []
    brim_end = 0
    for k, (ring, seam) in enumerate(order):
        start = _nearest_index(ring, seam)
        lap = ring[start:] + ring[:start]
        if k < len(order) - 1:      # stop a lane change short of the seam
            cut = int(math.ceil(lane_len / (math.dist(ring[0], ring[1]) or 1e-9)))
            if 2 * cut < len(lap):
                lap = lap[:len(lap) - cut]
        if path:
            path.extend(_tangent_bezier(path[-1], _unit(path[-2], path[-1]), lap[0], _unit(lap[0], lap[1])))
        if k == len(brims):
            brim_end = len(path)
        path.extend(lap)
    head = dedupe_path(path[:brim_end])
    return head + dedupe_path(path[brim_end:]), len(head)


def blade_notes(geometry, gap, min_radius) -> list[str]:
    """Plain-language notes on a blade's shape: its root, its blunt trailing
    edge, and how far the root transition leans out (the outer rings there
    are laid partly over air)."""
    g = geometry
    w, _t = root_size(g)
    notes = [f"Wind Turbine Blade: the root is {root_label(g)}, {g.blade_root_length_mm:g} mm long, for the hub's discs to "
             f"clamp; then {g.stem_height_mm:g} mm of blade ({blade_height(g):g} mm printed). The airfoil is set at "
             f"{g.blade_pitch_deg:g} degrees pitch against the root's faces.",
             f"Wind Turbine Blade: the trailing edge is cut blunt, {blade_trailing_edge(min_radius, gap):.1f} mm "
             f"thick, so the tow can turn round it at the Min Bend Radius."]
    span = float(g.stem_height_mm) * SHOULDER
    out = max((1.0 - PITCH_AXIS) * float(g.blade_chord_mm), PITCH_AXIS * float(g.blade_chord_mm)) - 0.5 * w
    if out > 0 and span > 0:
        lean = math.degrees(math.atan2(out, span))
        if lean > 45.0:
            notes.append(f"Wind Turbine Blade: from the root to the widest chord the trailing edge reaches "
                         f"{out:.0f} mm further out over {span:.0f} mm of height, {lean:.0f} degrees from upright: "
                         f"its outer rings there lie partly over air. A longer blade (Height), a wider root "
                         f"or a shorter Max Chord makes the climb gentler.")
    return notes
