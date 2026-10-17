"""Calibration coupons: short test prints that measure the values the slicer
otherwise has to assume (docs/SLICER_BENCHMARK.md item 2, after Orca's
calibration tools). They are separate G-code files and never change how a
part is sliced.

Every coupon is ONE continuous stroke on one layer, like a part (no travel,
no cut). All turns respect the configured min bend radius, except in the
bend-radius coupon, where the radius is what is being tested. Each test band
is labelled with a G-code comment ("; PUMP FLOOR TEST v=4.0 mm/s"), and the
file header says how to read the result.

    pump_floor        straight lines at descending speed      -> Pump-Stall Floor
    bend_radius       U-turns at descending radius            -> Min Bend Radius
    pressure_advance  slow/fast/slow lines, one per advance   -> Pressure Advance
    track_gap         one spiral whose ring spacing grows     -> Track Gap
"""
from __future__ import annotations

import math

import numpy as np

from .config import ProcessConfig
from .gcode import compile_gcode
from .kinematics import _advance_indices, _limit_accel, calculate_trajectory
from .machine import DEFAULT_MACHINE, MachineProfile, fit_trajectory_to_bed

STEP_MM = 0.5
LINE_MM = 40.0

COUPONS = ("pump_floor", "bend_radius", "pressure_advance", "track_gap")

_INSTRUCTIONS = {
    "pump_floor": (
        "Each line runs at one speed, fastest first; flow follows speed as in a part.",
        "Find the first line where the fibre stops feeding or the resin bead breaks.",
        "Set Pump-Stall Floor to the speed of the LAST GOOD line above it.",
    ),
    "bend_radius": (
        "U-turns get tighter one by one, at Min Bend Speed.",
        "Find the first turn where the tow lifts, kinks, twists or the fibre breaks.",
        "Set Min Bend Radius to the radius of the LAST GOOD turn before it.",
    ),
    "pressure_advance": (
        "Each line goes slow -> fast -> slow; lines differ only in Pressure Advance.",
        "Pick the line whose bead width stays even through BOTH speed changes",
        "(too low: thin after speeding up / blob after slowing; too high: the reverse).",
    ),
    "track_gap": (
        "One spiral; the spacing between its rings grows band by band (see comments).",
        "Pick the band where neighbouring tows just touch: no gap, no tow on tow.",
        "Set Track Gap to that band's spacing.",
    ),
}


class _Stroke:
    """Accumulates trajectory points (one layer, one continuous stroke)."""

    def __init__(self, x, y, z):
        self.pts = [{'x': x, 'y': y, 'z': z, 'dist': 0.0, 'v': 1.0, 'e_mult': 0.0,
                     'layer': 1, 'layer_start': True, 'is_transition': False}]
        self.z = z

    @property
    def end(self):
        return self.pts[-1]['x'], self.pts[-1]['y']

    def to(self, x, y, v, e_mult, comment=None):
        px, py = self.end
        d = math.hypot(x - px, y - py)
        if d < 1e-9:
            return
        pt = {'x': x, 'y': y, 'z': self.z, 'dist': d, 'v': float(v), 'e_mult': float(e_mult),
              'layer': 1, 'layer_start': False, 'is_transition': False}
        if comment:
            pt['comment'] = comment
        self.pts.append(pt)

    def line(self, x, y, v, e_mult, comment=None):
        px, py = self.end
        n = max(1, int(math.ceil(math.hypot(x - px, y - py) / STEP_MM)))
        for k in range(1, n + 1):
            self.to(px + (x - px) * k / n, py + (y - py) * k / n, v, e_mult,
                    comment if k == 1 else None)

    def uturn(self, direction, radius, v, e_mult, comment=None):
        """Semicircle of `radius` from the current end (heading `direction`,
        +1 = +x) up to the next line, 2·radius higher in Y."""
        px, py = self.end
        cx, cy = px, py + radius
        n = max(8, int(math.ceil(math.pi * radius / STEP_MM)))
        for k in range(1, n + 1):
            a = -math.pi / 2 + math.pi * k / n          # bottom -> top of the circle
            self.to(cx + direction * radius * math.cos(a), cy + radius * math.sin(a), v, e_mult,
                    comment if k == 1 else None)


def _flow(v, cfg):
    k = cfg.kinematics
    return max(float(v), float(k.pump_stall_floor_mm_s)) / float(k.v_max_mm_s)


def _turn_radius(cfg):
    return max(float(cfg.min_radius_mm), float(cfg.track_gap_mm))


def pump_floor_coupon(cfg: ProcessConfig, speeds=None):
    k = cfg.kinematics
    v_max = float(k.v_max_mm_s)
    if speeds is None:
        speeds = [round(v_max * f, 2) for f in (1.0, 0.8, 0.6, 0.5, 0.4, 0.3, 0.25, 0.2, 0.15, 0.1)]
    r = _turn_radius(cfg)
    x0, y0 = float(cfg.geometry.start_x_mm), float(cfg.geometry.start_y_mm)
    s = _Stroke(x0, y0, float(cfg.z_offset_mm))
    direction = 1
    for i, v in enumerate(speeds):
        if i:
            # Turn at the speed of the line just finished (no extra stall risk).
            s.uturn(direction, r, speeds[i - 1], speeds[i - 1] / v_max)
            direction = -direction
        x_end = x0 + LINE_MM if direction > 0 else x0
        # No pump floor here: flow must follow speed to find where it stalls.
        s.line(x_end, s.end[1], v, v / v_max, comment=f"PUMP FLOOR TEST v={v:g} mm/s")
    return s.pts


def bend_radius_coupon(cfg: ProcessConfig, radii=None):
    r_cfg = float(cfg.min_radius_mm)
    if radii is None:
        radii = [round(r_cfg * f, 2) for f in (3.0, 2.5, 2.0, 1.6, 1.3, 1.0, 0.8, 0.65, 0.5, 0.35)]
    v = float(cfg.kinematics.v_min_mm_s)
    e = _flow(v, cfg)
    x0, y0 = float(cfg.geometry.start_x_mm), float(cfg.geometry.start_y_mm)
    s = _Stroke(x0, y0, float(cfg.z_offset_mm))
    s.line(x0 + LINE_MM / 2, y0, v, e)
    direction = 1
    for rad in radii:
        s.uturn(direction, rad, v, e, comment=f"BEND RADIUS TEST R={rad:g} mm")
        direction = -direction
        s.line(x0 + LINE_MM / 2 if direction > 0 else x0, s.end[1], v, e)
    return s.pts


def pressure_advance_coupon(cfg: ProcessConfig, advances=None):
    k = cfg.kinematics
    v_max, v_min = float(k.v_max_mm_s), float(k.v_min_mm_s)
    if advances is None:
        advances = (0.0, 1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 10.0)
    r = _turn_radius(cfg)
    x0, y0 = float(cfg.geometry.start_x_mm), float(cfg.geometry.start_y_mm)
    seg = (15.0, 30.0, 15.0)                      # slow / fast / slow, mm
    total = sum(seg)
    s = _Stroke(x0, y0, float(cfg.z_offset_mm))
    direction = 1
    for i, adv in enumerate(advances):
        if i:
            s.uturn(direction, r, v_min, _flow(v_min, cfg))
            direction = -direction
        n = int(math.ceil(total / STEP_MM))
        pos = np.linspace(0.0, total, n + 1)
        target = np.where((pos > seg[0]) & (pos < seg[0] + seg[1]), v_max, v_min)
        dist = np.append(np.diff(pos), 0.0)
        v = _limit_accel(target, dist, float(k.accel_mm_s2), float(k.decel_mm_s2))
        flow = np.maximum(v, float(k.pump_stall_floor_mm_s)) / v_max
        e = flow[_advance_indices(dist, float(adv))]   # same look-ahead as a part
        sx, sy = s.end
        for j in range(1, n + 1):
            x = sx + direction * pos[j]
            s.to(x, sy, v[j], e[j], comment=f"PRESSURE ADVANCE TEST advance={adv:g} mm" if j == 1 else None)
    return s.pts


def track_gap_coupon(cfg: ProcessConfig, gaps=None, rings_per_gap=2):
    """Outside-in spiral of a rectangle sized to fit all rings; ring k+1 sits
    gaps[k // rings_per_gap] inside ring k. Planned like a part (corner speeds).

    The outline is filleted once with r + (total inset), and every ring is a
    pure inward offset of it, so all corners are concentric and no ring is
    tighter than r. Each point is blended towards its normal projection on
    the next ring: the blend at fraction u is then exactly the offset at
    the intermediate inset, never tighter than the innermost ring."""
    from shapely.geometry import Point, box

    from .geometry._common import fillet
    r = float(cfg.min_radius_mm)
    g0 = float(cfg.track_gap_mm)
    if gaps is None:
        gaps = [round(g0 * f, 2) for f in (0.6, 0.75, 0.9, 1.0, 1.1, 1.25, 1.4)]
    x0, y0 = float(cfg.geometry.start_x_mm), float(cfg.geometry.start_y_mm)
    steps = [g for g in gaps for _ in range(rings_per_gap)]
    insets = list(np.cumsum([g0 / 2.0] + steps))
    # Innermost ring keeps a straight run of >= 10 mm on its short side.
    height = 2.0 * (insets[-1] + r) + 10.0
    # +5%: clipper's polygonal fillet comes out a little under the request,
    # and the offsets carry that into the innermost ring's corners.
    outline = fillet(box(x0, y0, x0 + height + 25.0, y0 + height), 1.05 * r + insets[-1])
    rings = []
    for d in insets:
        ring = outline.buffer(-d, join_style=1, quad_segs=32)
        if ring.is_empty or ring.geom_type != 'Polygon':
            raise ValueError("Track gap coupon: the rings do not fit at this min bend radius.")
        rings.append(ring)

    def resample(ring, start):
        ext = ring.exterior
        n = max(240, int(ext.length / STEP_MM))
        d0 = ext.project(Point(start)) if start is not None else 0.0
        return [ext.interpolate((d0 + ext.length * i / n) % ext.length).coords[0] for i in range(n)]

    path, labels = [], []
    lap = resample(rings[0], None)
    for k in range(len(rings) - 1):
        nxt = rings[k + 1].exterior
        n = len(lap)
        start_band = len(path)
        for i, (ax, ay) in enumerate(lap):
            bx, by = nxt.interpolate(nxt.project(Point(ax, ay))).coords[0]
            u = i / n
            path.append((ax + (bx - ax) * u, ay + (by - ay) * u))
        labels.append((start_band, steps[k]))
        lap = resample(rings[k + 1], path[-1])
    path.extend(lap)          # last ring, full lap
    k = cfg.kinematics
    traj = calculate_trajectory(
        tuple(path), float(k.v_max_mm_s), float(k.v_min_mm_s), float(k.accel_mm_s2),
        float(k.decel_mm_s2), float(k.pressure_advance_mm), z_height=float(cfg.z_offset_mm),
        min_rad=r, pump_floor=float(k.pump_stall_floor_mm_s),
    )
    for p in traj:
        p['layer'] = 1
    for idx, g in labels:
        traj[max(idx, 1)]['comment'] = f"TRACK GAP TEST gap={g:g} mm (this lap)"
    return traj


_BUILDERS = {
    "pump_floor": pump_floor_coupon,
    "bend_radius": bend_radius_coupon,
    "pressure_advance": pressure_advance_coupon,
    "track_gap": track_gap_coupon,
}


def make_coupon(kind: str, cfg: ProcessConfig, machine: MachineProfile = DEFAULT_MACHINE):
    """(trajectory, gcode) for one coupon, fitted to the bed, with a header
    explaining how to read it."""
    if kind not in _BUILDERS:
        raise ValueError(f"Unknown calibration coupon {kind!r}; choose from {', '.join(COUPONS)}.")
    traj, _, _ = fit_trajectory_to_bed(_BUILDERS[kind](cfg), machine)
    header = [f"; ===== CALIBRATION COUPON: {kind.replace('_', ' ').upper()} ====="]
    header += [f"; {line}" for line in _INSTRUCTIONS[kind]]
    header += ["; (Settings below are the current profile; this file is not a part.)", ""]
    return traj, "\n".join(header) + compile_gcode(traj, cfg)
