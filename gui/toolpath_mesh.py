"""Toolpath geometry for the 3D viewer, numpy only (no GL, testable
offscreen): shape-preserving decimation, tube meshes with the bead's
elliptical cross-section, and per-point colour modes with their legends.

The fibre stroke is ordered by layer, so any layer range or move range is a
contiguous run of points; the mesh is laid out so a point range [a, b) is
the contiguous face range [a*F, (b-1)*F) (F = faces per segment). Sliders
then only change what is drawn, never the geometry.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

# --- colour maps ---------------------------------------------------------------

TURBO = np.array([
    [0.19, 0.07, 0.23], [0.21, 0.18, 0.47], [0.18, 0.31, 0.66],
    [0.13, 0.46, 0.78], [0.06, 0.60, 0.84], [0.04, 0.72, 0.82],
    [0.15, 0.81, 0.72], [0.35, 0.88, 0.60], [0.56, 0.92, 0.48],
    [0.74, 0.94, 0.37], [0.89, 0.93, 0.25], [0.98, 0.87, 0.14],
    [0.99, 0.76, 0.08], [0.96, 0.62, 0.07], [0.88, 0.47, 0.07],
    [0.76, 0.33, 0.07], [0.60, 0.21, 0.06], [0.42, 0.12, 0.04],
], dtype=np.float32)
VIRIDIS = np.array([
    [0.27, 0.00, 0.33], [0.28, 0.17, 0.48], [0.23, 0.32, 0.55], [0.17, 0.45, 0.56],
    [0.13, 0.57, 0.55], [0.12, 0.68, 0.51], [0.30, 0.78, 0.40], [0.56, 0.86, 0.23],
    [0.99, 0.91, 0.14],
], dtype=np.float32)
# Bend radius: red below r_min, amber at it, green/teal well above.
BEND = np.array([
    [0.55, 0.05, 0.08], [0.90, 0.16, 0.14], [0.98, 0.55, 0.12], [0.98, 0.84, 0.25],
    [0.55, 0.85, 0.40], [0.20, 0.72, 0.62], [0.16, 0.52, 0.78],
], dtype=np.float32)
LAYER_PALETTE = np.array([
    [0.95, 0.55, 0.20], [0.30, 0.65, 0.98], [0.55, 0.85, 0.35], [0.90, 0.35, 0.55],
    [0.98, 0.82, 0.25], [0.55, 0.45, 0.95], [0.25, 0.82, 0.78], [0.98, 0.45, 0.30],
    [0.70, 0.70, 0.75], [0.85, 0.55, 0.95],
], dtype=np.float32)

FEATURES = (
    ("fibre", "Fibre (carbon tow)", (0.40, 0.42, 0.48)),
    ("ramp", "Layer transition", (0.20, 0.85, 0.95)),
    ("anchor", "Lead-in anchor", (0.72, 0.48, 0.96)),
    ("bridge", "Bridge between islands", (0.98, 0.28, 0.34)),
    ("floored", "Pump floor engaged", (0.98, 0.86, 0.26)),
    ("conductor", "Conductive ink", (0.86, 0.87, 0.92)),
    ("dielectric", "Dielectric ink", (0.30, 0.80, 0.62)),
    ("encapsulation", "Encapsulation", (0.52, 0.62, 0.78)),
    ("crossover", "Crossover insulation", (0.62, 0.92, 0.55)),
    ("travel", "Travel (no flow)", (0.42, 0.42, 0.52)),
    ("end", "End of path", (0.98, 0.20, 0.55)),
)
RESIN = (0.96, 0.72, 0.36)          # resin coat drawn round the fibre (Feature mode)
FIBRE_CORE = (0.48, 0.62)           # dry tow inside the resin bead: share of bead width, height
END_MM = 10.0                       # the last END_MM of the fibre are "End of path"
FEATURE_INDEX = {key: i for i, (key, _, _) in enumerate(FEATURES)}
TOOLS = (("Fibre head", (0.95, 0.56, 0.20)), ("DIW head", (0.80, 0.84, 0.95)))

MODES = ("Feature", "Speed", "Layer", "Height (Z)", "Flow", "Bend radius", "Fibre direction", "Tool")


def sample(table: np.ndarray, t: np.ndarray) -> np.ndarray:
    """Linear interpolation into a colour table at t in [0, 1]."""
    t = np.clip(np.nan_to_num(np.asarray(t, dtype=np.float64)), 0.0, 1.0)
    x = t * (len(table) - 1)
    i = np.minimum(x.astype(int), len(table) - 2)
    f = (x - i)[:, None]
    return (table[i] * (1 - f) + table[i + 1] * f).astype(np.float32)


def hue(t: np.ndarray) -> np.ndarray:
    """Cyclic colour wheel (HSV, s 0.72, v 0.96) at t in [0, 1)."""
    h = (np.asarray(t, dtype=np.float64) % 1.0) * 6.0
    i = np.floor(h).astype(int) % 6
    f = h - np.floor(h)
    v, s = 0.96, 0.72
    p, q, r = v * (1 - s), v * (1 - s * f), v * (1 - s * (1 - f))
    rgb = np.empty((len(h), 3))
    for k, (a, b, c) in enumerate(((v, r, p), (q, v, p), (p, v, r), (p, q, v), (r, p, v), (v, p, q))):
        m = i == k
        rgb[m, 0] = a[m] if np.ndim(a) else a
        rgb[m, 1] = b[m] if np.ndim(b) else b
        rgb[m, 2] = c[m] if np.ndim(c) else c
    return rgb.astype(np.float32)


# --- toolpath data -------------------------------------------------------------

@dataclass
class ToolpathData:
    """One drawable toolpath. `breaks[i]` means no bead between point i-1
    and i (a travel move); `tool` 0 = fibre, 1 = DIW."""
    xyz: np.ndarray
    v: np.ndarray
    e_mult: np.ndarray
    layer: np.ndarray
    feature: np.ndarray
    breaks: np.ndarray
    tool: np.ndarray
    source: np.ndarray            # index into the original trajectory
    width: np.ndarray             # bead width per point (mm)
    height: np.ndarray            # bead height per point (mm)
    _bend: np.ndarray | None = field(default=None, repr=False)

    def __len__(self):
        return len(self.xyz)

    def take(self, idx):
        return ToolpathData(*(getattr(self, f)[idx] for f in (
            "xyz", "v", "e_mult", "layer", "feature", "breaks", "tool", "source", "width", "height")))

    def layer_ranges(self) -> dict[int, tuple[int, int]]:
        """{layer: (first point, one past last point)} for contiguous layers."""
        out: dict[int, tuple[int, int]] = {}
        if not len(self):
            return out
        change = np.nonzero(np.diff(self.layer))[0] + 1
        starts = np.concatenate([[0], change])
        ends = np.concatenate([change, [len(self)]])
        for a, b in zip(starts.tolist(), ends.tolist(), strict=False):
            lay = int(self.layer[a])
            lo, hi = out.get(lay, (a, b))
            out[lay] = (min(lo, a), max(hi, b))
        return out

    def bend_radii(self) -> np.ndarray:
        """Windowed bend radius at every point (XY), as the validation audit
        measures it; 9999 on straights and at breaks."""
        if self._bend is None:
            from cfrp_core.geometry._common import local_bend_radii
            r = np.full(len(self), 9999.0)
            cuts = np.concatenate([[0], np.nonzero(self.breaks)[0], [len(self)]])
            for a, b in zip(cuts[:-1], cuts[1:], strict=False):
                if b - a >= 3:
                    r[a + 1:b - 1] = local_bend_radii(self.xyz[a:b, :2].astype(np.float64))
            self._bend = r
        return self._bend


def from_trajectory(trajectory, bead_width=1.0, bead_height=0.2, tool=0, layer_offset=0) -> ToolpathData:
    """ToolpathData from a trajectory (list of point dicts). Fibre points get
    feature fibre/ramp/anchor/bridge/floored; DIW points carry their own
    'feature' and 'extruding' keys (no bead where 'extruding' is False)."""
    n = len(trajectory)
    xyz = np.empty((n, 3), dtype=np.float32)
    v = np.empty(n, dtype=np.float32)
    e = np.empty(n, dtype=np.float32)
    layer = np.empty(n, dtype=np.int32)
    feat = np.zeros(n, dtype=np.int8)
    brk = np.zeros(n, dtype=bool)
    width = np.full(n, bead_width, dtype=np.float32)
    height = np.full(n, bead_height, dtype=np.float32)
    for i, p in enumerate(trajectory):
        xyz[i] = (p['x'], p['y'], p['z'])
        v[i] = p.get('v', 0.0)
        e[i] = p.get('e_mult', 1.0)
        layer[i] = int(p.get('layer', 1)) + layer_offset
        if 'feature' in p:
            feat[i] = FEATURE_INDEX.get(p['feature'], 0)
            brk[i] = not p.get('extruding', True)
            if 'width' in p:
                width[i] = p['width']
            if 'height' in p:
                height[i] = p['height']
        elif p.get('is_bridge'):
            feat[i] = FEATURE_INDEX['bridge']
        elif p.get('is_transition'):
            feat[i] = FEATURE_INDEX['ramp']
        elif p.get('is_anchor'):
            feat[i] = FEATURE_INDEX['anchor']
        elif p.get('pump_floored'):
            feat[i] = FEATURE_INDEX['floored']
    if n:
        brk[0] = True
    if n and 'feature' not in trajectory[-1]:
        # Mark the end of the fibre stroke (where the tow is cut).
        run = 0.0
        for i in range(n - 1, 0, -1):
            feat[i] = FEATURE_INDEX['end']
            run += float(np.linalg.norm(xyz[i] - xyz[i - 1]))
            if run >= END_MM:
                break
    return ToolpathData(xyz, v, e, layer, feat, brk, np.full(n, tool, dtype=np.int8),
                        np.arange(n), width, height)


@dataclass
class Prepared:
    """What the viewer draws, built off the UI thread: the drawable
    (simplified) data and, for one display mode, its tube meshes."""
    data: ToolpathData
    core_mode: bool
    sides: int
    tube: TubeMesh | None = None
    resin: TubeMesh | None = None


def drawable(data: ToolpathData, max_points: int) -> ToolpathData:
    """Data thinned for drawing: collinear runs merged, then coarser angle
    tolerances until it fits `max_points`."""
    keep = simplify(data)
    tol = 1.0
    while len(keep) > max_points and tol < 16:
        tol *= 2
        keep = simplify(data, angle_deg=tol, max_len=1.5 * tol)
    return data.take(keep)


def prepare(trajectory, bead_width, bead_height, max_points, core_mode, extra=None) -> Prepared:
    """from_trajectory + drawable + the tube meshes for one display mode
    (`core_mode`: the fibre tow inside a resin coat). Pure numpy, so the
    slicing thread can do it while the window stays responsive."""
    import dataclasses
    fibre = from_trajectory(trajectory or [], bead_width, bead_height)
    data = concat([fibre, extra]) if extra is not None else fibre
    if not len(data):
        return Prepared(data, core_mode, 0)
    data = drawable(data, max_points)
    sides = sides_for(len(data))
    if core_mode:
        tube = tube_mesh(data, sides=sides, width_scale=FIBRE_CORE[0], height_scale=FIBRE_CORE[1])
    else:
        tube = tube_mesh(data, sides=sides, width_scale=0.95, height_scale=0.95)
    resin = None
    if (data.tool == 0).any():
        fibre_only = dataclasses.replace(data, breaks=data.breaks | (data.tool != 0))
        resin = tube_mesh(fibre_only, sides=sides, width_scale=1.0, height_scale=1.0)
    return Prepared(data, core_mode, sides, tube, resin)


def concat(parts) -> ToolpathData:
    parts = [p for p in parts if p is not None and len(p)]
    if not parts:
        return from_trajectory([])
    out = ToolpathData(*(np.concatenate([getattr(p, f) for p in parts]) for f in (
        "xyz", "v", "e_mult", "layer", "feature", "breaks", "tool", "source", "width", "height")))
    return out


def simplify(data: ToolpathData, angle_deg=1.0, max_len=1.5) -> np.ndarray:
    """Indices of the points to draw: drop points on straights and gentle
    arcs (accumulated turning under `angle_deg`, under `max_len` since the
    last kept point) but keep every point where anything drawn changes
    (layer, feature, tool, travel). Unlike taking every n-th point this
    never cuts corners."""
    n = len(data)
    if n <= 2:
        return np.arange(n)
    xyz = data.xyz.astype(np.float64)
    seg = np.diff(xyz, axis=0)
    seg_len = np.linalg.norm(seg, axis=1)
    unit = seg / np.maximum(seg_len, 1e-12)[:, None]
    cos = np.einsum('ij,ij->i', unit[:-1], unit[1:])
    turn = np.concatenate([[0.0], np.arccos(np.clip(cos, -1.0, 1.0)), [0.0]])
    must = np.zeros(n, dtype=bool)
    must[[0, -1]] = True
    for arr in (data.layer, data.feature, data.tool):
        ch = np.nonzero(np.diff(arr))[0]
        must[ch] = True
        must[ch + 1] = True
    br = np.nonzero(data.breaks)[0]
    must[br] = True
    must[np.maximum(br - 1, 0)] = True
    tol = math.radians(angle_deg)
    keep = np.zeros(n, dtype=bool)
    acc_turn = acc_len = 0.0
    turn_l, len_l, must_l = turn.tolist(), np.concatenate([[0.0], seg_len]).tolist(), must.tolist()
    for i in range(n):          # sequential: accumulates since the last KEPT point
        acc_turn += turn_l[i]
        acc_len += len_l[i]
        if must_l[i] or acc_turn >= tol or acc_len >= max_len:
            keep[i] = True
            acc_turn = acc_len = 0.0
        elif i + 1 < n and turn_l[i + 1] + acc_turn >= tol:
            keep[i] = True          # keep the point before a corner too
            acc_turn = acc_len = 0.0
    return np.nonzero(keep)[0]


# --- tube mesh -------------------------------------------------------------------

@dataclass
class TubeMesh:
    vertices: np.ndarray      # (N*S, 3) float32
    normals: np.ndarray       # (N*S, 3) float32
    faces: np.ndarray         # ((N-1)*2S, 3) uint32
    sides: int

    @property
    def faces_per_segment(self):
        return 2 * self.sides

    def face_range(self, a, b):
        """Faces for the point range [a, b): (first face, face count)."""
        a, b = max(0, int(a)), max(0, int(b))
        if b - a < 2:
            return a * self.faces_per_segment, 0
        return a * self.faces_per_segment, (b - 1 - a) * self.faces_per_segment


def sides_for(n_points: int) -> int:
    """Cross-section resolution that keeps the mesh a sane size."""
    return 8 if n_points <= 120_000 else 6 if n_points <= 300_000 else 4


def tube_mesh(data: ToolpathData, sides=8, width_scale=0.86, height_scale=0.9) -> TubeMesh:
    """Shaded tube along the path with an elliptical cross-section (bead
    width x height), vertex rings shared by neighbouring segments. Segments
    starting at a break are degenerate (no bead) but keep their slot, so
    face ranges stay a fixed size per segment."""
    xyz = data.xyz.astype(np.float64)
    n = len(xyz)
    if n < 2:
        return TubeMesh(np.zeros((0, 3), np.float32), np.zeros((0, 3), np.float32),
                        np.zeros((0, 3), np.uint32), sides)
    d = np.diff(xyz, axis=0)
    ln = np.linalg.norm(d, axis=1)
    u = d / np.maximum(ln, 1e-12)[:, None]
    u[ln < 1e-12] = 0.0
    t = np.empty((n, 3))
    t[0], t[-1] = u[0], u[-1]
    t[1:-1] = u[:-1] + u[1:]
    # At a break the segment into the point is travel: follow the next one.
    br = data.breaks
    inner_break = np.nonzero(br[1:-1])[0] + 1
    t[inner_break] = u[inner_break]
    end_before_break = np.nonzero(br[1:])[0]
    t[end_before_break] = u[np.maximum(end_before_break - 1, 0)]
    tn = np.linalg.norm(t, axis=1)
    bad = tn < 1e-9
    t[bad] = np.vstack([u, u[-1:]])[bad]
    t /= np.maximum(np.linalg.norm(t, axis=1), 1e-12)[:, None]
    up = np.array([0.0, 0.0, 1.0])
    side = np.cross(t, up)
    sn = np.linalg.norm(side, axis=1)
    side[sn < 1e-6] = (1.0, 0.0, 0.0)          # vertical tangent
    side /= np.maximum(np.linalg.norm(side, axis=1), 1e-12)[:, None]
    nup = np.cross(side, t)
    hw = (data.width.astype(np.float64) * width_scale / 2.0)[:, None]
    hh = (data.height.astype(np.float64) * height_scale / 2.0)[:, None]
    ang = 2.0 * np.pi * np.arange(sides) / sides
    ca, sa = np.cos(ang)[None, :, None], np.sin(ang)[None, :, None]
    # Every tube (resin bead, fibre core inside it) is centred half a BEAD
    # height below the nozzle point, whatever its own scale.
    centre = xyz - nup * (data.height.astype(np.float64) / 2.0)[:, None]
    offs = ca * (hw[:, None] * side[:, None, :]) + sa * (hh[:, None] * nup[:, None, :])
    verts = (centre[:, None, :] + offs).reshape(-1, 3)
    nrm = ca * (side[:, None, :] / hw[:, None]) + sa * (nup[:, None, :] / hh[:, None])
    nrm = nrm.reshape(-1, 3)
    nrm /= np.maximum(np.linalg.norm(nrm, axis=1), 1e-12)[:, None]
    k = np.arange(sides)
    k1 = (k + 1) % sides
    base = (np.arange(n - 1) * sides)[:, None]
    a, b = base + k, base + k1
    c, dd = a + sides, b + sides
    tri = np.stack([np.stack([a, c, b], -1), np.stack([b, c, dd], -1)], axis=2).reshape(n - 1, 2 * sides, 3)
    gap = br[1:]                                  # segment i -> i+1 is travel
    tri[gap] = tri[gap][:, :, :1]                 # degenerate: all corners equal
    return TubeMesh(verts.astype(np.float32), nrm.astype(np.float32),
                    tri.reshape(-1, 3).astype(np.uint32), sides)


# --- colour modes ------------------------------------------------------------------

def _gradient(table, lo, hi, unit, ticks=None):
    return {"kind": "gradient", "table": table, "lo": lo, "hi": hi, "unit": unit, "ticks": ticks or []}


def mode_colors(mode: str, data: ToolpathData, r_min: float = 3.0):
    """(rgb float32 (N, 3), legend dict) for a colour mode."""
    n = len(data)
    if not n:
        return np.zeros((0, 3), np.float32), {"kind": "none"}
    if mode == "Speed":
        lo, hi = float(data.v.min()), float(data.v.max())
        t = (data.v - lo) / (hi - lo) if hi > lo else np.zeros(n)
        return sample(TURBO, t), _gradient(TURBO, lo, hi, "mm/s")
    if mode == "Flow":
        lo, hi = float(data.e_mult.min()), float(data.e_mult.max())
        t = (data.e_mult - lo) / (hi - lo) if hi > lo else np.zeros(n)
        return sample(TURBO, t), _gradient(TURBO, lo, hi, "x flow")
    if mode == "Height (Z)":
        z = data.xyz[:, 2]
        lo, hi = float(z.min()), float(z.max())
        t = (z - lo) / (hi - lo) if hi > lo else np.zeros(n)
        return sample(VIRIDIS, t), _gradient(VIRIDIS, lo, hi, "mm")
    if mode == "Layer":
        lay = data.layer - data.layer.min()
        rgb = LAYER_PALETTE[lay % len(LAYER_PALETTE)]
        return rgb.astype(np.float32), {"kind": "categories", "items": [
            (f"Layer {int(data.layer.min()) + k}" + ("…" if lay.max() >= len(LAYER_PALETTE) and k == len(LAYER_PALETTE) - 1 else ""), tuple(c))
            for k, c in enumerate(LAYER_PALETTE[:min(len(LAYER_PALETTE), int(lay.max()) + 1)])]}
    if mode == "Bend radius":
        r = np.maximum(data.bend_radii(), 1e-3)
        rm = max(float(r_min), 1e-3)
        # log scale: r_min/4 .. r_min .. 4·r_min -> 0 .. 0.5 .. 1
        t = 0.5 + np.log(r / rm) / (2.0 * math.log(4.0))
        tight = int(np.count_nonzero(r < rm * 0.98))
        legend = _gradient(BEND, rm / 4.0, rm * 4.0, "mm",
                           ticks=[(0.5, f"r_min {rm:g}")])
        legend["note"] = f"{tight} point(s) below r_min" if tight else "all bends ≥ r_min"
        return sample(BEND, t), legend
    if mode == "Fibre direction":
        d = np.zeros((n, 2))
        d[:-1] = np.diff(data.xyz[:, :2].astype(np.float64), axis=0)
        d[-1] = d[-2] if n > 1 else (1.0, 0.0)
        ang = np.degrees(np.arctan2(d[:, 1], d[:, 0])) % 180.0
        wheel = hue(np.linspace(0, 1, 13)[:-1])
        return hue(ang / 180.0), {"kind": "gradient", "table": np.vstack([wheel, wheel[:1]]),
                                  "lo": 0.0, "hi": 180.0, "unit": "° fibre angle",
                                  "ticks": [(0.25, "45°"), (0.5, "90°"), (0.75, "135°")]}
    if mode == "Tool":
        cols = np.array([c for _, c in TOOLS], dtype=np.float32)
        used = sorted(set(data.tool.tolist()))
        return cols[data.tool.astype(int)], {"kind": "categories",
                                             "items": [(TOOLS[k][0], TOOLS[k][1]) for k in used]}
    # Feature (default)
    cols = np.array([c for _, _, c in FEATURES], dtype=np.float32)
    counts = np.bincount(data.feature.astype(int), minlength=len(FEATURES))
    items = [(label, rgb) for k, (_, label, rgb) in enumerate(FEATURES) if counts[k]]
    return cols[data.feature.astype(int)], {"kind": "categories", "items": items}


def vertex_colors(point_rgb: np.ndarray, sides: int) -> np.ndarray:
    """Per-vertex RGBA uint8 (each point's colour on its whole ring)."""
    rgba = np.empty((len(point_rgb), 4), dtype=np.uint8)
    rgba[:, :3] = np.clip(point_rgb * 255.0 + 0.5, 0, 255).astype(np.uint8)
    rgba[:, 3] = 255
    return np.repeat(rgba, sides, axis=0)


def nearest_point(screen_xy: np.ndarray, click, max_px=12.0, candidates=None):
    """Index of the projected point nearest `click` (within max_px), or None."""
    pts = screen_xy if candidates is None else screen_xy[candidates]
    if not len(pts):
        return None
    d = np.hypot(pts[:, 0] - click[0], pts[:, 1] - click[1])
    k = int(np.argmin(d))
    if d[k] > max_px:
        return None
    return int(candidates[k]) if candidates is not None else k
