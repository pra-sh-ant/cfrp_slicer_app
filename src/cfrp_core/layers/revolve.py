"""Z-revolve shells (sphere/dome/cylinder/tube/taper/cone): one helical
stroke with spiral Z (spec §7)."""

from __future__ import annotations

import math
from functools import lru_cache

import numpy as np

from .. import geometry as geo
from ..config import FERMAT, ProcessConfig
from ..geometry._common import enforce_min_radius
from .common import (
    KinematicsParams,
    _mark_anchor,
    add_arc_lead_in_anchor,
    add_lead_in_anchor,
    plan_layer,
)


def _skip_behind(prev_path, next_path, cx, cy, max_frac=0.3):
    """Drop the head of next_path while it still lies angularly BEHIND the
    end of prev_path (in the spiral's sense of rotation), so the connector is
    a short forward step instead of a ~360 deg lap. Never drops more than
    max_frac of the path."""
    tail = np.array(prev_path[-6:], dtype=np.float64) - (cx, cy)
    ang = np.unwrap(np.arctan2(tail[:, 1], tail[:, 0]))
    sense = 1.0 if ang[-1] - ang[0] >= 0 else -1.0
    a0 = ang[-1]
    nxt = np.asarray(next_path, dtype=np.float64) - (cx, cy)
    fwd = ((np.arctan2(nxt[:, 1], nxt[:, 0]) - a0) * sense) % (2.0 * math.pi)
    limit = max(1, int(len(next_path) * max_frac))
    for k in range(limit):
        if fwd[k] < math.pi:
            return list(next_path[k:])
    return list(next_path)


def _polar_profile(points, cx, cy, from_end):
    """r(theta) of the last (from_end) or first lap of `points` about
    (cx, cy), as a periodic interpolant. The lap is the run of points
    covering one full turn, so it lies on a single ring."""
    pts = np.asarray(points, dtype=np.float64) - (cx, cy)
    if from_end:
        pts = pts[::-1]
    ang = np.unwrap(np.arctan2(pts[:, 1], pts[:, 0]))
    swept = np.abs(ang - ang[0])
    n = int(np.searchsorted(swept, 2.0 * math.pi)) + 1
    lap, a = pts[:max(n, 3)], ang[:max(n, 3)]
    r = np.hypot(lap[:, 0], lap[:, 1])
    a = np.mod(a, 2.0 * math.pi)
    order = np.argsort(a)
    a, r = a[order], r[order]

    def radius(theta):
        return np.interp(np.mod(theta, 2.0 * math.pi), a, r, period=2.0 * math.pi)
    return radius


def _helical_connector(prev_path, next_start, cx, cy, step=0.75, next_path=None):
    """Seam between two revolve layers as ONE forward lap around the shell
    centre (vase-mode / spiralize style): keeps the spiral's sense of
    rotation, so the fibre never reverses, and moves radius from the old
    ring end to the new ring start with a smoothstep (zero radial slope at
    both ends). Z is ramped across the whole lap by the caller.

    Round shells lap on a circle. With `next_path` (non-round shells, e.g.
    the square tube) the lap instead follows the rings' own outline: at
    each angle the radius blends the previous ring's last lap into the next
    ring's first lap, so it stays between the rings instead of cutting the
    corners.

    Returns XY points strictly between prev_path[-1] and next_start."""
    ex, ey = prev_path[-1]
    sx, sy = next_start
    tail = np.array(prev_path[-6:], dtype=np.float64) - (cx, cy)
    ang = np.unwrap(np.arctan2(tail[:, 1], tail[:, 0]))
    sense = 1.0 if ang[-1] - ang[0] >= 0 else -1.0
    r0, r1 = math.hypot(ex - cx, ey - cy), math.hypot(sx - cx, sy - cy)
    a0, a1 = math.atan2(ey - cy, ex - cx), math.atan2(sy - cy, sx - cx)
    sweep = ((a1 - a0) * sense) % (2.0 * math.pi)
    r_mid = 0.5 * (r0 + r1)
    # Z already ramps over the whole ring + connector, so the connector only
    # needs to be long enough for a gentle radius change (slope <= ~0.25);
    # adding a full extra lap would retrace the ring (double fibre, and a
    # self-crossing warning on single-track shells).
    if sweep * r_mid < 4.0 * abs(r1 - r0):
        sweep += 2.0 * math.pi
    n = max(8, int(math.ceil(sweep * r_mid / step)))
    u = np.linspace(0.0, 1.0, n + 1)[1:-1]
    blend = 3.0 * u ** 2 - 2.0 * u ** 3
    theta = a0 + sense * sweep * u
    if next_path is None:
        radius = r0 + (r1 - r0) * blend
    else:
        r_prev = _polar_profile(prev_path, cx, cy, from_end=True)(theta)
        r_next = _polar_profile(next_path, cx, cy, from_end=False)(theta)
        radius = r_prev + (r_next - r_prev) * blend
    return list(zip((cx + radius * np.cos(theta)).tolist(),
                    (cy + radius * np.sin(theta)).tolist(), strict=False))


def _spiral_z(xy, section_starts, z_nodes):
    """Z per point. Section k spans point indices section_starts[k] up to
    section_starts[k+1] (the last one runs to the final point); inside it Z
    rises linearly with cumulative XY length from z_nodes[k] to
    z_nodes[k+1]. len(z_nodes) == len(section_starts) + 1."""
    pts = np.asarray(xy, dtype=np.float64)
    cum = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))])
    z = np.empty(len(pts))
    bounds = list(section_starts) + [len(pts) - 1]
    for k in range(len(section_starts)):
        a, b = bounds[k], bounds[k + 1]
        hi = b + 1 if k == len(section_starts) - 1 else b
        span = cum[b] - cum[a]
        frac = (cum[a:hi] - cum[a]) / span if span > 1e-9 else np.zeros(hi - a)
        z[a:hi] = z_nodes[k] + (z_nodes[k + 1] - z_nodes[k]) * frac
    return z.tolist()

@lru_cache(maxsize=2048)
def _archimedean_cached(cross_section_wkb, gap, min_rad, start_from_inside, polar_center=None):
    """Per-layer spiral for a revolve cross-section. Pure in its arguments, so
    changing speed / anchor / seam parameters re-slices without redoing the
    (dominant) polygon offsetting. Returns a tuple so the entry is immutable."""
    from shapely import wkb
    path, _ = geo.archimedean_path(
        wkb.loads(cross_section_wkb), gap, min_rad, track_count=None,
        start_from_inside=start_from_inside, polar_center=polar_center)
    return tuple(path)


def build_revolve_shell(config: ProcessConfig):
    """Z-revolve shell (sphere/dome/cylinder/tube/taper/cone) with spiral
    Z-ramping and arc seam transitions.

        num_layers = max(2, round(H / layer_height) + 1),  layer_height ≥ 0.05
        Layer i: Z ramps from z0 + H·i/(n-1) to z0 + H·(i+1)/(n-1)
        Last layer stays flat at z0 + H.

    Layer-to-layer transitions follow the shell circumference (arc interpolation
    around the shell center) instead of a straight chord.

    Returns (trajectory, target_shapes, max_z)."""
    g = config.geometry
    shape = g.shape
    cx, cy = float(g.start_x_mm), float(g.start_y_mm)
    z0 = float(config.z_offset_mm)
    outer_radius = max(float(g.base_radius_mm), 1.0)
    gap = float(config.track_gap_mm)
    min_rad = float(config.min_radius_mm)
    tracks = max(1, int(config.tracks_per_layer))
    shape_type = 'square' if shape == 'Hollow Square Tube (Z)' else 'round'
    profile = geo.revolve_radius_profile(shape, g.base_radius_mm, tracks, gap)
    kin = KinematicsParams.from_config(config)
    use_fermat = config.infill_type == FERMAT

    if shape == 'Hollow Sphere (Z)':
        height = 2.0 * outer_radius
    else:
        height = max(float(g.stem_height_mm), 1e-6)

    layer_height = max(float(config.layer_height_mm), 0.05)
    num_layers = max(2, round(height / layer_height) + 1)

    layers_raw = []
    target_shapes = []
    max_z = z0
    anchored = False
    anchor_n = 0
    fermat_last_pt = None

    for i in range(num_layers):
        progress = i / (num_layers - 1)
        layer_z = z0 + height * progress
        max_z = layer_z
        current_outer, current_inner = profile(progress)

        wall_depth = (tracks - 0.5) * gap

        # Skip layers where the cross-section is too small for the spiral
        # path to maintain min_radius curvature at every ring, including
        # the innermost ring and its transitions.
        skip_threshold = min_rad + gap if shape_type == 'square' else min_rad + (tracks + 0.5) * gap
        if current_outer < skip_threshold:
            cross_section = geo.revolve_cross_section(
                cx, cy, current_outer, current_inner, shape_type,
                min_bend_radius=min_rad, wall_depth=wall_depth,
            )
            target_shapes.append((layer_z, cross_section, i + 1))
            continue

        cross_section = geo.revolve_cross_section(
            cx, cy, current_outer, current_inner, shape_type,
            min_bend_radius=min_rad, wall_depth=wall_depth,
        )
        target_shapes.append((layer_z, cross_section, i + 1))
        if cross_section.is_empty or cross_section.area < 1e-6:
            continue

        is_outward = (i % 2 != 0)
        try:
            if use_fermat:
                fermat_hint = None
                if fermat_last_pt is not None:
                    ccx = cross_section.centroid.x
                    ccy = cross_section.centroid.y
                    fermat_hint = (2.0 * ccx - fermat_last_pt[0],
                                   2.0 * ccy - fermat_last_pt[1])
                raw_path = geo.fermat_spiral_path(
                    cross_section, gap, min_rad, track_count=None,
                    start_from_inside=is_outward, entry_hint=fermat_hint
                )
            else:
                raw_path = list(_archimedean_cached(
                    cross_section.wkb, gap, min_rad, is_outward,
                    (cx, cy) if shape_type == 'square' else None))
        except ValueError:
            continue
        if not raw_path:
            continue

        if not anchored:
            n_before = len(raw_path)
            if shape_type == 'round':
                raw_path = add_arc_lead_in_anchor(raw_path, float(config.anchor_length_mm), cx, cy)
            else:
                raw_path = add_lead_in_anchor(raw_path, float(config.anchor_length_mm))
            anchor_n = len(raw_path) - n_before
            anchored = True

        layers_raw.append((i + 1, layer_z, enforce_min_radius(raw_path, min_rad)))
        if use_fermat:
            fermat_last_pt = layers_raw[-1][2][-1]

    if not layers_raw:
        raise ValueError(
            "No layer of this shell could be filled: every cross-section is too small for "
            "the wall at this Track Gap and Min Bend Radius. Enlarge the part or reduce "
            "Tracks Per Layer, Track Gap or Min Bend Radius."
        )

    # One continuous stroke for the whole shell (vase-mode style): every ring
    # is followed by a forward helical lap into the next ring, speed is
    # planned once over the whole path (no stop at seams), and Z climbs
    # steadily over each ring + its connector lap, so there is no seam block.
    # A layer that is just one closed ring (single-track walls) IS its own
    # helical lap: drop the ring and let the connector carry radius and Z, so
    # each layer costs one lap instead of ring + retraced lap. The first
    # layer (anchor) and last layer (top) stay whole.
    # Optional staggered seams (round shells only: a rotated ring is the same
    # ring, so only the seam moves; the helical connector laps the extra arc).
    stagger = math.radians(float(config.seam_stagger_deg)) if shape_type == 'round' else 0.0
    if stagger:
        for k in range(1, len(layers_raw)):
            num, lz, path = layers_raw[k]
            c, s = math.cos(k * stagger), math.sin(k * stagger)
            layers_raw[k] = (num, lz, [(cx + (x - cx) * c - (y - cy) * s,
                                        cy + (x - cx) * s + (y - cy) * c) for x, y in path])

    if shape_type == 'round':
        for k in range(1, len(layers_raw) - 1):
            num, lz, path = layers_raw[k]
            rr = np.hypot(*(np.asarray(path, dtype=np.float64) - (cx, cy)).T)
            if rr.max() - rr.min() < 0.1 * gap:
                layers_raw[k] = (num, lz, path[:2])

    xy, starts, owner = [], [], []
    for k, (layer_num, _, path) in enumerate(layers_raw):
        starts.append(len(xy))
        xy.extend(path)
        owner.extend([layer_num] * len(path))
        if k + 1 < len(layers_raw):
            nxt = layers_raw[k + 1]
            trimmed = _skip_behind(path, nxt[2], cx, cy)
            layers_raw[k + 1] = (nxt[0], nxt[1], trimmed)
            link = _helical_connector(path, trimmed[0], cx, cy,
                                      next_path=trimmed if shape_type == 'square' else None)
            xy.extend(link)
            owner.extend([layer_num] * len(link))
    xy = enforce_min_radius(xy, min_rad)   # also rounds ring/connector joins
    z_nodes = [lz for _, lz, _ in layers_raw] + [layers_raw[-1][1]]
    zs = _spiral_z(xy, starts, z_nodes)

    layer_z_of = {n: lz for n, lz, _ in layers_raw}
    trajectory = plan_layer(xy, z0, owner[0], kin)
    for idx, pt in enumerate(trajectory):
        pt['z'] = zs[idx]
        pt['layer'] = owner[idx]
        pt['z_layer'] = layer_z_of[owner[idx]]
        pt['layer_start'] = False
    for s in starts:
        trajectory[s]['layer_start'] = True
    _mark_anchor(trajectory, anchor_n)
    return trajectory, target_shapes, max_z
