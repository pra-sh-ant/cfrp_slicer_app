"""DIW toolpaths for a structural PCB: dielectric, conductors, encapsulation.

Unlike the fibre, ink can stop and start, so features are separate paths
joined by travel moves (Z-hop, no flow). Layer stack on top of the laminate:

    dielectric x N   conductors grown by the margin, raster at ±90° per layer
                     (carbon fibre conducts: this keeps the ink off it)
    conductor  x N   level-1 wires: narrow traces as centreline passes, wider
                     traces, pads and conductor zones as concentric fills
    crossover  x N   insulating bridges where a level-2 wire crosses another
                     net's level-1 wire (only if there are level-2 wires)
    conductor  x N   level-2 wires, their height following what lies under
                     them (over a bridge, down onto the insulation after it)
    encapsulation    over the traces, pads left open (optional)

Spacing and insets use the printed bead (line width x spread factor).

Trajectory points: x, y, z, v, dist, e_mult, layer, feature
('dielectric' | 'conductor' | 'crossover' | 'encapsulation' | 'travel'),
extruding (False on travel), width, height.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from shapely.affinity import rotate
from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

from .config import DiwConfig, DiwLayerSpec
from .design import BoardDesign, board_polygon, conductors, crossovers, hole_shape, pad_shape

RAMP_SLOPE = math.tan(math.radians(30.0))         # steepest climb of a level-2 wire onto a bridge


@dataclass
class DiwLayer:
    index: int
    feature: str
    paths: list = field(default_factory=list)      # [(coords, closed)]
    z: float = 0.0
    # Conformal layers (level-2 wires): [(region, height mm)] stacked under
    # it, and the height of its own kind's layers below it; z then follows
    # what lies under each point.
    stack: list | None = None
    base_mm: float = 0.0
    height: float = 0.0                            # 0 = the DIW layer height
    speed: float = 0.0                             # 0 = the usual speed for its feature
    cure: bool = False                             # UV cure right after it


@dataclass
class DiwResult:
    trajectory: list
    layers: list
    gcode: str
    validation: dict
    stats: dict
    regions: dict                                 # feature -> shapely geometry


# --- fill primitives ----------------------------------------------------------------

def _polys(geom):
    if geom is None or geom.is_empty:
        return []
    return [g for g in getattr(geom, 'geoms', [geom]) if g.geom_type == 'Polygon' and not g.is_empty]


def concentric(region, lw):
    """Closed rings lw/2, 3lw/2, ... inside region (outer boundary and holes),
    outermost first; a region narrower than one bead gets one line along its
    long axis."""
    out = []
    for poly in _polys(region):
        d, got = lw / 2.0, False
        chain = []                    # rings of a simple shape: one path, stepping inward
        while True:
            inset = poly.buffer(-d, join_style=1)
            if inset.is_empty or inset.area < 1e-4:
                break
            parts = _polys(inset)
            if len(parts) == 1 and not parts[0].interiors and (not chain or len(poly.interiors) == 0):
                chain.append(list(parts[0].exterior.coords))
            else:
                for p in parts:
                    for ring in (p.exterior, *p.interiors):
                        out.append((list(ring.coords), True))
            got = True
            d += lw
        if chain:
            out.append(_chain_rings(chain, lw))
        if not got:
            rect = poly.minimum_rotated_rectangle
            c = list(rect.exterior.coords)[:4] if rect.geom_type == 'Polygon' else []
            if len(c) == 4:
                e1, e2 = (c[0], c[1]), (c[1], c[2])
                a, b = (e1, e2) if math.dist(*e1) >= math.dist(*e2) else (e2, e1)
                mid0 = ((b[0][0] + b[1][0]) / 2 - (a[1][0] - a[0][0]) / 2,
                        (b[0][1] + b[1][1]) / 2 - (a[1][1] - a[0][1]) / 2)
                mid1 = (mid0[0] + (a[1][0] - a[0][0]), mid0[1] + (a[1][1] - a[0][1]))
                line = LineString([mid0, mid1])
                shrink = min(lw / 2.0, line.length / 2.0 - 1e-3)
                if line.length > 2 * shrink + 1e-3:
                    line = LineString([line.interpolate(shrink).coords[0],
                                       line.interpolate(line.length - shrink).coords[0]])
                out.append((list(line.coords), False))
            else:
                pt = poly.representative_point()
                out.append(([(pt.x - 0.01, pt.y), (pt.x + 0.01, pt.y)], False))
    return out


def _chain_rings(rings, lw):
    """Nested closed rings, outermost first, as one open path: each lap
    ends where the next ring is nearest, so the inward step is one bead."""
    path = list(rings[0])
    for ring in rings[1:]:
        arr = np.asarray(ring[:-1])
        k = int(np.argmin(np.hypot(arr[:, 0] - path[-1][0], arr[:, 1] - path[-1][1])))
        lap = [tuple(q) for q in np.vstack([arr[k:], arr[:k], arr[k:k + 1]])]
        if math.dist(path[-1], lap[0]) > 2.5 * lw:
            break
        path.extend(lap)
    return (path, False)


def raster(region, spacing, angle):
    """Raster of region at `angle` as few paths as possible: every scan
    segment is chained to a free segment on the next scan line whose end is
    within a step of the current end (the step staying inside the region),
    so each part of the region becomes one zigzag; a new path (travel)
    starts only where no such neighbour is left."""
    result = []
    for poly in _polys(region):
        c = poly.centroid
        rp = rotate(poly, -angle, origin=c)
        zone = rp.buffer(1e-6)
        minx, miny, maxx, maxy = rp.bounds
        rows, y = [], miny + spacing / 2.0
        while y < maxy:
            inter = LineString([(minx - 1, y), (maxx + 1, y)]).intersection(rp)
            rows.append([tuple(sorted((g.coords[0][0], g.coords[-1][0]))) + (y,)
                         for g in getattr(inter, 'geoms', [inter])
                         if g.geom_type == 'LineString' and g.length > 1e-6])
            y += spacing
        used = [[False] * len(r) for r in rows]
        paths = []
        for i0 in range(len(rows)):
            for j0 in range(len(rows[i0])):
                if used[i0][j0]:
                    continue
                used[i0][j0] = True
                x0, x1, yy = rows[i0][j0]
                path = [(x0, yy), (x1, yy)]
                i = i0
                while i + 1 < len(rows):
                    end, best = path[-1], None
                    for j, (a0, a1, ya) in enumerate(rows[i + 1]):
                        if used[i + 1][j]:
                            continue
                        for near, far in (((a0, ya), (a1, ya)), ((a1, ya), (a0, ya))):
                            d = math.dist(end, near)
                            if d <= 2.5 * spacing and (best is None or d < best[0])                                     and zone.covers(LineString([end, near])):
                                best = (d, j, near, far)
                    if best is None:
                        break
                    _, j, near, far = best
                    used[i + 1][j] = True
                    path += [near, far]
                    i += 1
                paths.append(path)
        result += [(list(rotate(LineString(q), angle, origin=c).coords), False) for q in paths]
    return result


def trace_paths(trace, lw):
    """Centreline passes for a trace up to ~2 beads wide (1 or 2 side by
    side); wider traces are filled concentrically."""
    line = LineString(trace.points)
    w = trace.width_mm
    if w <= 1.4 * lw:
        return [(list(line.coords), False)]
    if w <= 2.4 * lw:
        off = (w - lw) / 2.0
        return [(list(line.offset_curve(s * off, join_style=1).coords), False) for s in (1, -1)]
    return concentric(line.buffer(w / 2.0, quad_segs=8), lw)


# --- ordering ------------------------------------------------------------------------

def order_paths(paths, start=None):
    """Nearest-neighbour order (open paths may be reversed, closed rings
    start at their nearest vertex), then 2-opt on the travel legs."""
    left = [(list(c), closed) for c, closed in paths if len(c) >= 2]
    if not left:
        return []
    cur = start
    out = []
    while left:
        best = None
        for i, (c, closed) in enumerate(left):
            if cur is None:
                cand = (0.0, i, c)
            elif closed:
                arr = np.asarray(c[:-1] if c[0] == c[-1] else c)
                k = int(np.argmin(np.hypot(arr[:, 0] - cur[0], arr[:, 1] - cur[1])))
                ring = list(arr[k:]) + list(arr[:k]) + [arr[k]]
                cand = (math.dist(cur, ring[0]), i, [tuple(p) for p in ring])
            else:
                d0, d1 = math.dist(cur, c[0]), math.dist(cur, c[-1])
                cand = (d0, i, c) if d0 <= d1 else (d1, i, c[::-1])
            if best is None or cand[0] < best[0]:
                best = cand
        _, i, c = best
        out.append(c)
        left.pop(i)
        cur = c[-1]
    # 2-opt on path order (reversing a run of open paths)
    n = len(out)
    if 3 <= n <= 400:
        improved, passes = True, 0
        while improved and passes < 6:
            improved, passes = False, passes + 1
            for a in range(0, n - 2):
                for b in range(a + 2, n):
                    pa_end, pb_end = out[a][-1], out[b][-1]
                    nxt = out[b + 1][0] if b + 1 < n else None
                    old = math.dist(pa_end, out[a + 1][0]) + (math.dist(pb_end, nxt) if nxt else 0.0)
                    new = math.dist(pa_end, out[b][-1]) + (math.dist(out[a + 1][0], nxt) if nxt else 0.0)
                    if new < old - 1e-6:
                        out[a + 1:b + 1] = [p[::-1] for p in reversed(out[a + 1:b + 1])]
                        improved = True
    return out


# --- layers ----------------------------------------------------------------------------

def default_stack(cfg: DiwConfig, level2: bool = False) -> tuple[DiwLayerSpec, ...]:
    """The automatic stack from the layer counts: insulation (rasters at
    0°/90°), level-1 ink, level-2 ink (only with level-2 wires), cover
    (rasters at ±45°)."""
    specs = []
    if cfg.dielectric and cfg.dielectric_layers:
        specs += [DiwLayerSpec(kind="dielectric", covers=cfg.insulation_coverage,
                               angle_deg=0.0 if i % 2 == 0 else 90.0) for i in range(cfg.dielectric_layers)]
    specs += [DiwLayerSpec(kind="conductor", level=1) for _ in range(cfg.conductor_layers)]
    if level2:
        specs += [DiwLayerSpec(kind="conductor", level=2) for _ in range(cfg.conductor_layers)]
    if cfg.encapsulate and cfg.encapsulation_layers:
        specs += [DiwLayerSpec(kind="encapsulation", angle_deg=45.0 if i % 2 == 0 else -45.0)
                  for i in range(cfg.encapsulation_layers)]
    return tuple(specs)


def layer_stack(design: BoardDesign, cfg: DiwConfig) -> tuple[DiwLayerSpec, ...]:
    """The ink layers printed, bottom first: the user's stack (its enabled
    layers) when set, else the automatic one."""
    if cfg.stack:
        return tuple(s for s in cfg.stack if s.enabled)
    return default_stack(cfg, level2=any(t.level == 2 for t in design.traces))


def insulation_covers(cfg: DiwConfig) -> str:
    """'board' when any insulation layer covers the whole board, else 'wires'."""
    if cfg.stack:
        return "board" if any(s.enabled and s.kind == "dielectric" and s.covers == "board"
                              for s in cfg.stack) else "wires"
    return cfg.insulation_coverage


def has_insulation(cfg: DiwConfig) -> bool:
    if cfg.stack:
        return any(s.enabled and s.kind == "dielectric" for s in cfg.stack)
    return bool(cfg.dielectric and cfg.dielectric_layers)


def ink_height_mm(cfg: DiwConfig, level: int = 1) -> float:
    """Wet height of all ink layers of one wire level."""
    if cfg.stack:
        return sum(s.height_mm or cfg.layer_height_mm for s in cfg.stack
                   if s.enabled and s.kind == "conductor" and s.level == level)
    return cfg.conductor_layers * cfg.layer_height_mm


def dielectric_region(design: BoardDesign, cfg: DiwConfig, covers: str | None = None):
    """Where the insulation is printed: every conductor grown by the
    dielectric margin, plus dielectric zones -- or, covering the board, the
    whole board -- cut to where a whole bead fits on the board. Insulation
    reaches as far as any conductor can (it was trimmed back further than
    the ink near edges: 288 uninsulated points in the audit)."""
    elems = conductors(design)
    if not elems:
        return Polygon()
    printable = board_polygon(design).buffer(-cfg.bead_mm / 2.0, join_style=2)
    if (covers or insulation_covers(cfg)) == "board":
        return printable
    cond = unary_union([e["geom"] for e in elems])
    extra = [Polygon(z.points).buffer(0) for z in design.zones if z.kind == "dielectric"]
    return unary_union([cond.buffer(cfg.dielectric_margin_mm, join_style=1), *extra]).intersection(printable)


def ink_area(design: BoardDesign, cfg: DiwConfig):
    """Where ink may go: the board minus edge/hole clearance and keepouts."""
    board = Polygon(design.outline).buffer(0).buffer(-cfg.edge_clearance_mm, join_style=2)
    for h in design.holes:
        board = board.difference(hole_shape(h).buffer(cfg.edge_clearance_mm))
    for z in design.zones:
        if z.kind == "keepout":
            board = board.difference(Polygon(z.points).buffer(0))
    return board


def plan_layers(design: BoardDesign, cfg: DiwConfig):
    """The ink layers, bottom first, from layer_stack: insulation, ink at
    wire level 1, the crossover bridges (before the first level-2 ink
    layer, when level-2 wires cross another net), ink at level 2 following
    what lies under it, cover. Returns (layers, regions)."""
    lw = cfg.bead_mm
    lh = cfg.layer_height_mm
    elems = conductors(design)
    cond = unary_union([e["geom"] for e in elems]) if elems else Polygon()
    area = ink_area(design, cfg)
    board = board_polygon(design)
    # Where a whole bead fits: the board minus holes, shrunk by half a bead.
    # Nothing is printed outside it, even for a design that fails the DRC
    # (ink past the edge or over a hole hangs in the air and drips).
    printable = board.buffer(-lw / 2.0, join_style=2)
    regions = {"printable": printable}
    layers: list[DiwLayer] = []
    clipped = [0.0]
    regions["conductor"] = cond
    has_level2 = any(e.get("level", 1) == 2 for e in elems)
    diel_union, diel_h, level1_h, bridge_h, level2_h = Polygon(), 0.0, 0.0, 0.0, 0.0
    under = None
    pads = unary_union([pad_shape(p).buffer(0.3) for p in design.pads]) if design.pads else Polygon()

    def paths_of(level):
        paths = []
        for e in elems:
            if e.get("level", 1) != level:
                continue
            if e["kind"] == "trace":
                paths += trace_paths(design.traces[e["index"]], lw)
            else:
                paths += concentric(e["geom"], lw)
        return paths

    def fill(region, spec):
        return raster(region, lw, spec.angle_deg) if spec.fill == "raster" else concentric(region, lw)

    def add(feature, paths, spec=None, **kw):
        layers.append(DiwLayer(len(layers), feature, paths, height=(spec.height_mm if spec else 0.0) or lh,
                               speed=spec.speed_mm_s if spec else 0.0, cure=bool(spec and spec.cure), **kw))
        return layers[-1].height

    for spec in layer_stack(design, cfg):
        if spec.kind == "dielectric":
            if cond.is_empty:
                continue
            region = dielectric_region(design, cfg, spec.covers)
            diel_union = unary_union([diel_union, region])
            diel_h += add("dielectric", fill(region, spec), spec)
        elif spec.kind == "conductor" and spec.level == 1:
            level1_h += add("conductor", _clip(paths_of(1), printable, lw, clipped), spec)
        elif spec.kind == "conductor":
            if not has_level2:
                continue
            if under is None:
                bridges = unary_union([c["geom"] for c in crossovers(design, cfg.bridge_margin_mm)])
                bridges = bridges.intersection(printable) if not bridges.is_empty else Polygon()
                if not bridges.is_empty:
                    regions["crossover"] = bridges
                    for i in range(cfg.bridge_layers):
                        bridge_h += add("crossover", raster(bridges, lw, 0.0 if i % 2 == 0 else 90.0))
                # What lies under a level-2 wire, as printed: raster lines run
                # to their region's edge, so the beads reach half a bead past
                # it, and a wire narrower than the bead prints bead-wide.
                grow = lw / 2.0
                level1 = unary_union([LineString(design.traces[e["index"]].points).buffer(lw / 2.0, quad_segs=8)
                                      if e["kind"] == "trace" and design.traces[e["index"]].width_mm < lw
                                      else e["geom"] for e in elems if e.get("level", 1) == 1])
                wide_bridges = bridges.buffer(grow) if not bridges.is_empty else bridges
                under = [(diel_union.buffer(grow) if not diel_union.is_empty else diel_union, diel_h),
                         (unary_union([level1, wide_bridges]), level1_h), (wide_bridges, bridge_h)]
            level2_h += add("conductor", _clip(paths_of(2), printable, lw, clipped), spec, stack=under,
                            base_mm=level2_h)
        else:                                       # cover
            if cond.is_empty:
                continue
            region = (area if spec.covers == "board"
                      else cond.buffer(cfg.dielectric_margin_mm * 0.6, join_style=1).intersection(area))
            region = region.difference(pads) if not pads.is_empty else region
            regions["encapsulation"] = unary_union([regions.get("encapsulation", Polygon()), region])
            add("encapsulation", fill(region, spec), spec)
    if not diel_union.is_empty:
        regions["dielectric"] = diel_union
    regions["clipped_mm"] = clipped[0]
    regions["ink_layers"] = sum(1 for layer in layers if layer.feature == "conductor")
    return layers, regions


def _clip(paths, region, lw, clipped):
    """Paths cut to `region`; pieces shorter than a bead are dropped.
    Adds the length removed (once per layer stack) to clipped[0]."""
    out = []
    for coords, closed in paths:
        line = LineString(coords)
        if region.covers(line):
            out.append((coords, closed))
            continue
        inside = line.intersection(region)
        kept = 0.0
        for g in getattr(inside, 'geoms', [inside]):
            if g.geom_type == 'LineString' and g.length > lw:
                out.append((list(g.coords), False))
                kept += g.length
        clipped[0] += max(0.0, line.length - kept)
    return out


def conformal_z(coords, stack, z0, step=0.3, slope=RAMP_SLOPE):
    """(points, z per point) for a path over stacked regions [(region,
    height)]: each point sits at z0 plus the height of the layers under it,
    the path densified to `step` and every climb started early enough that
    it is never steeper than `slope` (it rises before a bridge and comes
    down after)."""
    import shapely
    pts = [tuple(coords[0])]
    for a, b in zip(coords[:-1], coords[1:], strict=False):
        n = max(1, int(math.ceil(math.dist(a, b) / step)))
        pts += [(a[0] + (b[0] - a[0]) * t / n, a[1] + (b[1] - a[1]) * t / n) for t in range(1, n + 1)]
    xy = np.asarray(pts, dtype=np.float64)
    z = np.full(len(xy), float(z0))
    for region, height in stack:
        if height and region is not None and not region.is_empty:
            z += height * shapely.contains_xy(region.buffer(1e-6), xy[:, 0], xy[:, 1])
    ds = np.hypot(*np.diff(xy, axis=0).T)
    for i in range(1, len(z)):                     # the climb may not be steeper than slope ...
        z[i] = max(z[i], z[i - 1] - ds[i - 1] * slope)
    for i in range(len(z) - 2, -1, -1):            # ... nor the descent after it
        z[i] = max(z[i], z[i + 1] - ds[i] * slope)
    return pts, z.tolist()


def build_trajectory(layers, cfg: DiwConfig, z_base=0.0, offset=(0.0, 0.0), layer_offset=0):
    """Ordered points for all layers (travel between paths, Z-hop)."""
    dx, dy = offset
    lh = cfg.layer_height_mm
    traj: list[dict] = []
    cur = None
    top = z_base                                   # top of the stack so far
    for layer in layers:
        h = layer.height or lh
        top += h
        z = top
        layer.z = z if layer.stack is None else z_base     # conformal: its highest point, below
        speed = layer.speed or (cfg.speed_conductor_mm_s if layer.feature == "conductor"
                                else cfg.speed_dielectric_mm_s)
        tag = layer.index + 1 + layer_offset
        first = len(traj)
        for coords in order_paths(layer.paths, cur):
            if layer.stack is not None:
                pts, zs = conformal_z(coords, layer.stack, z_base + layer.base_mm + h)
                layer.z = max(layer.z, max(zs))
            else:
                pts, zs = coords, [z] * len(coords)
            pts = [(x + dx, y + dy) for x, y in pts]
            hop = max(zs) + cfg.z_hop_mm
            if traj:
                last = traj[-1]
                _pt(traj, last['x'], last['y'], max(hop, last['z']), cfg.travel_speed_mm_s, "travel", False,
                    tag, cfg, h)
            _pt(traj, pts[0][0], pts[0][1], hop, cfg.travel_speed_mm_s, "travel", False, tag, cfg, h)
            _pt(traj, pts[0][0], pts[0][1], zs[0], cfg.travel_speed_mm_s, "travel", False, tag, cfg, h)
            for (x, y), zz in zip(pts[1:], zs[1:], strict=True):
                _pt(traj, x, y, zz, speed, layer.feature, True, tag, cfg, h)
            cur = (coords[-1][0], coords[-1][1])
        if layer.cure and len(traj) > first:
            traj[-1]["cure_after"] = True
        if layer.stack is not None:
            top = max(top, layer.z)
    return traj


def _pt(traj, x, y, z, v, feature, extruding, layer, cfg, height=None):
    dist = math.dist((x, y, z), (traj[-1]['x'], traj[-1]['y'], traj[-1]['z'])) if traj else 0.0
    traj.append({"x": float(x), "y": float(y), "z": float(z), "v": float(v), "dist": dist, "e_mult": 1.0,
                 "layer": int(layer), "feature": feature, "extruding": bool(extruding),
                 "width": cfg.bead_mm, "height": float(height or cfg.layer_height_mm),
                 "layer_start": False, "is_transition": False})


def diw_stats(traj, cfg: DiwConfig):
    lw, lh = cfg.line_width_mm, cfg.layer_height_mm
    length, t, starts = {}, 0.0, 0
    prev_ext = False
    layers = []
    for p in traj:
        if p['extruding']:
            length[p['feature']] = length.get(p['feature'], 0.0) + p['dist']
            if not prev_ext:
                starts += 1
            if not layers or layers[-1] != p['layer']:
                layers.append(p['layer'])
        t += p['dist'] / max(p['v'], 1e-6)
        prev_ext = p['extruding']
    t += starts * (cfg.on_delay_ms + cfg.off_delay_ms) / 1000.0
    # waits between ink layers (drying, UV cure), as compile_diw_gcode emits them
    changes = max(len(layers) - 1, 0)
    t += changes * cfg.layer_wait_s
    if cfg.uv_cure_between_layers and layers:
        t += len(layers) * cfg.uv_cure_s
    elif layers:
        t += len({p['layer'] for p in traj if p.get('cure_after')}) * cfg.uv_cure_s
    return {
        "ink_length_mm": length,
        "ink_volume_mm3": {k: v * lw * lh for k, v in length.items()},
        "print_time_s": t,
        "starts": starts,
        "travel_mm": sum(p['dist'] for p in traj if not p['extruding']),
    }


def slice_diw(design: BoardDesign, cfg: DiwConfig, z_base=0.0, offset=(0.0, 0.0), layer_offset=0):
    """Plan, order and check the DIW print; G-code for the DIW head."""
    from .gcode import compile_diw_gcode
    from .validation import drc
    layers, regions = plan_layers(design, cfg)
    traj = build_trajectory(layers, cfg, z_base, offset, layer_offset)
    validation = drc(design, cfg)
    cut = regions.pop("clipped_mm", 0.0) / max(regions.pop("ink_layers", 0), 1)
    if cut > 0.05:
        validation = dict(validation, messages=[f"{cut:.1f} mm of conductive ink was left out: it lies off the "
                                                f"board or over a hole. Move those wires or pads inward."]
                          + [m for m in validation["messages"] if not m.startswith("DRC passed")],
                          status="Fail" if validation["status"] == "Fail" else "Warning")
    return DiwResult(traj, layers, compile_diw_gcode(traj, cfg, design.name), validation,
                     diw_stats(traj, cfg), regions)


__all__ = ["DiwLayer", "DiwResult", "concentric", "conformal_z", "default_stack", "dielectric_region",
           "has_insulation", "ink_area", "ink_height_mm", "insulation_covers", "layer_stack", "order_paths",
           "plan_layers", "raster", "slice_diw", "trace_paths"]
