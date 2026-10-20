"""Mesh loading and planar slicing.

Supports STL, OBJ, PLY, 3MF, STEP and every format Trimesh can read.
Slicing is done here in numpy + shapely (no scipy/networkx, which Trimesh's
own `section()` / `fill_holes()` need): triangle-plane segments are chained
into rings and the rings combined with the even-odd rule, so slices keep
holes and every island, in world XY coordinates (Cura / Orca behaviour).
"""
from __future__ import annotations

import math
from functools import reduce
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from shapely.geometry import MultiLineString, Polygon
from shapely.ops import linemerge, unary_union

from ._common import largest_polygon

if TYPE_CHECKING:
    import trimesh

# Orca's default `slice_closing_radius`: gaps narrower than ~2x this between
# chain ends or region edges are closed.
DEFAULT_CLOSING_RADIUS_MM = 0.049
_MIN_REGION_AREA_MM2 = 0.01


def load_stl(path: str | Path) -> trimesh.Trimesh:
    """Load a mesh file (STL, OBJ, PLY, 3MF, ...).

    Returns a Trimesh mesh object. The function name is kept for
    backwards compatibility but it accepts any format Trimesh supports.
    """
    try:
        import trimesh
    except ImportError as exc:
        raise ImportError(
            "trimesh is required for mesh import. Install it with:  pip install trimesh"
        ) from exc
    mesh = trimesh.load(str(path), force="mesh")
    if not isinstance(mesh, trimesh.Trimesh):
        raise ValueError(f"File did not load as a single mesh: {path}")
    if not mesh.is_watertight:
        # Best effort: repair needs optional deps (networkx/scipy). Small
        # leaks are closed later by the slice closing radius instead.
        try:
            mesh.fill_holes()
            mesh.fix_normals()
        except Exception:
            pass
    return mesh


def mesh_bounds(mesh) -> tuple[float, float, float, float, float, float]:
    """(x_min, y_min, z_min, x_max, y_max, z_max) of the mesh."""
    if isinstance(mesh, np.ndarray):
        mins = mesh.reshape(-1, 3).min(axis=0)
        maxs = mesh.reshape(-1, 3).max(axis=0)
        return (*mins, *maxs)
    return (*mesh.bounds[0], *mesh.bounds[1])


def _triangles(mesh) -> np.ndarray:
    if isinstance(mesh, np.ndarray):
        return np.asarray(mesh, dtype=np.float64).reshape(-1, 3, 3)
    return np.asarray(mesh.triangles, dtype=np.float64)


def _edge_point(lo, hi, d_lo, d_hi):
    """Plane crossing on edge lo->hi, always interpolated from the vertex
    below the plane, so the two triangles sharing an edge produce bitwise
    identical points and chains join exactly."""
    t = d_lo / (d_lo - d_hi)
    return lo + (hi - lo) * t[:, None]


def section_segments(tris: np.ndarray, z: float) -> np.ndarray:
    """(M, 2, 2) XY segments where the plane Z=z cuts the triangles.
    A vertex exactly on the plane counts as above it (consistent tie rule)."""
    d = tris[:, :, 2] - z
    below = d < 0
    n_below = below.sum(axis=1)
    sel = (n_below == 1) | (n_below == 2)
    if not sel.any():
        return np.empty((0, 2, 2))
    t, d, below = tris[sel], d[sel], below[sel]
    # The lone vertex is the one on its own side of the plane.
    lone_mask = np.where((below.sum(axis=1) == 1)[:, None], below, ~below)
    i0 = lone_mask.argmax(axis=1)
    rows = np.arange(len(t))
    pts, ds = [], []
    for k in (0, 1, 2):
        idx = (i0 + k) % 3
        pts.append(t[rows, idx])
        ds.append(d[rows, idx])
    seg_ends = []
    for other in (1, 2):
        a, b, da, db = pts[0], pts[other], ds[0], ds[other]
        a_below = da < 0
        lo = np.where(a_below[:, None], a, b)
        hi = np.where(a_below[:, None], b, a)
        d_lo = np.where(a_below, da, db)
        d_hi = np.where(a_below, db, da)
        seg_ends.append(_edge_point(lo, hi, d_lo, d_hi)[:, :2])
    segs = np.stack(seg_ends, axis=1)
    keep = np.linalg.norm(segs[:, 1] - segs[:, 0], axis=1) > 1e-9
    return segs[keep]


def _join_open_chains(chains, tol):
    """Greedily join open chains whose ends lie within `tol`; returns
    (closed_rings, still_open)."""
    chains = [list(c) for c in chains]
    rings, still_open = [], []
    while chains:
        cur = chains.pop()
        grew = True
        while grew:
            if np.hypot(cur[0][0] - cur[-1][0], cur[0][1] - cur[-1][1]) <= tol and len(cur) >= 4:
                break
            grew = False
            for j, other in enumerate(chains):
                end = cur[-1]
                for cand in (other, other[::-1]):
                    if np.hypot(cand[0][0] - end[0], cand[0][1] - end[1]) <= tol:
                        cur = cur + cand[1:]
                        chains.pop(j)
                        grew = True
                        break
                if grew:
                    break
        if len(cur) >= 4 and np.hypot(cur[0][0] - cur[-1][0], cur[0][1] - cur[-1][1]) <= tol:
            rings.append(cur[:-1] + [cur[0]])
        else:
            still_open.append(cur)
    return rings, still_open


def _stitch(chains, max_gap, where=None):
    """Close what a hole in the mesh left open (as Cura's extensive
    stitching does): repeatedly bridge the closest pair of open ends -- a
    chain's own two ends, or the end of one chain to an end of another --
    while that gap is at most max_gap. Returns (rings, still_open, stitches);
    `where` (list) receives the middle of every gap bridged."""
    chains = [list(c) for c in chains]
    rings, stitches = [], 0
    while chains:
        best = None
        for i, a in enumerate(chains):
            if len(a) >= 3:
                d = math.dist(a[0], a[-1])
                if best is None or d < best[0]:
                    best = (d, i, None, None)
            for j in range(i + 1, len(chains)):
                b = chains[j]
                for flip_a in (False, True):
                    for flip_b in (False, True):
                        end = a[0] if flip_a else a[-1]
                        start = b[-1] if flip_b else b[0]
                        d = math.dist(end, start)
                        if best is None or d < best[0]:
                            best = (d, i, j, (flip_a, flip_b))
        if best is None or best[0] > max_gap:
            break
        _, i, j, flips = best
        stitches += 1
        if where is not None:
            if j is None:
                a, b = chains[i][-1], chains[i][0]
            else:
                a = chains[i][0] if flips[0] else chains[i][-1]
                b = chains[j][-1] if flips[1] else chains[j][0]
            where.append(((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0))
        if j is None:
            ring = chains.pop(i)
            rings.append(ring + [ring[0]])
            continue
        a = chains[i][::-1] if flips[0] else chains[i]
        b = chains[j][::-1] if flips[1] else chains[j]
        chains[i] = a + b
        chains.pop(j)
    return rings, chains, stitches


def segments_to_region(segs: np.ndarray, closing_radius: float = DEFAULT_CLOSING_RADIUS_MM,
                       stitch_mm: float = 0.0, report: dict | None = None, z: float | None = None):
    """Chain section segments into rings and combine them with the even-odd
    rule (an island inside a hole inside a part stays an island). Returns a
    Polygon or MultiPolygon (possibly empty).

    Chains still open after joining ends within ~2 x closing_radius come from
    holes in the mesh; with `stitch_mm` > 0, gaps up to that size are bridged
    straight. `report` (dict) counts 'stitched' gaps and 'open' chains that
    could not be closed (dropped); with `z`, report['markers'] also gets
    (x, y, z, kind) for each, kind 'stitched gap' or 'open outline'."""
    if len(segs) == 0:
        return Polygon()
    merged = linemerge(MultiLineString([s.tolist() for s in segs]))
    lines = list(merged.geoms) if merged.geom_type == 'MultiLineString' else [merged]
    closed, open_chains = [], []
    for ln in lines:
        coords = list(ln.coords)
        if ln.is_ring:
            closed.append(coords)
        else:
            open_chains.append(coords)
    tol = max(float(closing_radius), 1e-6) * 2.0
    rings, still_open = _join_open_chains(open_chains, tol)
    closed.extend(rings)
    marks = report.setdefault('markers', []) if report is not None and z is not None else None
    if still_open and stitch_mm > 0:
        where = [] if marks is not None else None
        rings, still_open, n = _stitch(still_open, float(stitch_mm), where)
        closed.extend(rings)
        if report is not None:
            report['stitched'] = report.get('stitched', 0) + n
        if marks is not None:
            marks.extend((x, y, z, 'stitched gap') for x, y in where)
    if still_open and report is not None:
        report['open'] = report.get('open', 0) + len(still_open)
        if marks is not None:
            marks.extend((c[0][0], c[0][1], z, 'open outline') for c in still_open)

    polys = []
    for ring in closed:
        if len(ring) < 4:
            continue
        p = Polygon(ring)
        if not p.is_valid:
            p = p.buffer(0)
        if not p.is_empty and p.area > 1e-9:
            polys.append(p)
    if not polys:
        return Polygon()
    region = reduce(lambda a, b: a.symmetric_difference(b), polys)
    if closing_radius > 0:
        region = region.buffer(closing_radius, join_style=2).buffer(-closing_radius, join_style=2)
    if not region.is_valid:
        region = region.buffer(0)
    return region


def mesh_bodies(tris: np.ndarray):
    """Split a triangle soup into connected bodies (triangles sharing a
    vertex, coordinates matched to 1 micron). Returns [(triangles,
    signed_volume), ...]; a body whose normals point inwards has negative
    volume. numpy only (Trimesh's split needs scipy/networkx)."""
    keys = np.round(tris.reshape(-1, 3) * 1e3).astype(np.int64)
    _, vid = np.unique(keys, axis=0, return_inverse=True)
    vid = vid.reshape(-1, 3)
    parent = list(range(int(vid.max()) + 1))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for a, b, c in vid.tolist():
        ra = find(a)
        parent[find(b)] = ra
        parent[find(c)] = ra
    roots = np.array([find(v) for v in vid[:, 0].tolist()])
    bodies = []
    for r in np.unique(roots):
        t = tris[roots == r]
        vol = float(np.einsum('ij,ij->i', t[:, 0], np.cross(t[:, 1], t[:, 2])).sum() / 6.0)
        bodies.append((t, vol))
    return bodies


def slice_mesh_regions(
    mesh,
    layer_height: float,
    z_offset: float = 0.0,
    closing_radius: float = DEFAULT_CLOSING_RADIUS_MM,
    stitch_mm: float = 0.0,
    report: dict | None = None,
) -> list[tuple[float, object]]:
    """Slice a mesh at mid-layer planes, keeping holes and all islands.

    Returns [(z_print, Polygon | MultiPolygon), ...] for each non-empty
    slice. z_print = z_offset + k * layer_height, k counted from the first
    non-empty slice, so an empty slice keeps its Z slot (no silent collapse).

    A mesh of several bodies is sliced body by body (even-odd within each)
    and the bodies are unioned, so overlapping bodies stay solid instead of
    cancelling; a body whose normals oppose the mesh overall (e.g. a modelled
    cavity) is subtracted. `report` (dict) receives 'bodies', 'subtractive',
    'stitched', 'open' and 'empty_planes' (Z of planes inside the mesh that
    produced nothing).
    """
    tris = _triangles(mesh)
    z_min, z_max = float(tris[:, :, 2].min()), float(tris[:, :, 2].max())
    z_planes = np.arange(z_min + layer_height / 2.0, z_max, layer_height)
    bodies = mesh_bodies(tris) if len(tris) else []
    sign = 1.0 if sum(v for _, v in bodies) >= 0 else -1.0
    adds = [t for t, v in bodies if v * sign >= 0]
    subs = [t for t, v in bodies if v * sign < 0]
    if report is not None:
        report.update(bodies=len(bodies), subtractive=len(subs), empty_planes=[])

    def region_at(z):
        if len(bodies) <= 1:
            return segments_to_region(section_segments(tris, z), closing_radius, stitch_mm, report, z)
        parts = [segments_to_region(section_segments(t, z), closing_radius, stitch_mm, report, z)
                 for t in adds]
        parts = [p for p in parts if not p.is_empty]
        region = unary_union(parts) if parts else Polygon()
        for t in subs:
            hole = segments_to_region(section_segments(t, z), closing_radius, stitch_mm, report, z)
            if not hole.is_empty:
                region = region.difference(hole)
        return region

    slices = []
    first = None
    for k, z in enumerate(z_planes):
        region = region_at(float(z))
        if region.is_empty or region.area <= _MIN_REGION_AREA_MM2:
            if report is not None:
                report['empty_planes'].append(float(z))
            continue
        if first is None:
            first = k
        slices.append((z_offset + (k - first) * layer_height, region))
    if report is not None:
        # problem markers at print Z (a plane's Z as its slice is printed)
        base = float(z_planes[first]) if first is not None else z_min
        def to_print(zz):
            return z_offset + (zz - base)
        cx, cy = (tris[:, :, 0].min() + tris[:, :, 0].max()) / 2.0, (tris[:, :, 1].min() + tris[:, :, 1].max()) / 2.0
        marks = [(x, y, to_print(zz), kind) for x, y, zz, kind in report.get('markers', [])]
        marks += [(float(cx), float(cy), to_print(zz), 'empty slice') for zz in report['empty_planes']
                  if base <= zz <= z_max]
        report['markers'] = marks
        report['to_print_z'] = to_print
    return slices


def open_edges(mesh, limit: int = 400) -> np.ndarray:
    """Midpoints (N, 3) of the mesh's open edges -- edges only one triangle
    uses (coordinates matched to 1 micron) -- at most `limit`, spread over
    the mesh. Empty for a watertight mesh."""
    tris = _triangles(mesh)
    if not len(tris):
        return np.zeros((0, 3))
    keys = np.round(tris.reshape(-1, 3) * 1e3).astype(np.int64)
    _, vid = np.unique(keys, axis=0, return_inverse=True)
    vid = vid.reshape(-1, 3)
    edges = np.sort(np.concatenate([vid[:, [0, 1]], vid[:, [1, 2]], vid[:, [2, 0]]]), axis=1)
    uniq, inverse, counts = np.unique(edges, axis=0, return_inverse=True, return_counts=True)
    single = counts[inverse.ravel()] == 1
    if not single.any():
        return np.zeros((0, 3))
    pts = tris.reshape(-1, 3)
    a = np.concatenate([np.arange(len(tris)) * 3, np.arange(len(tris)) * 3 + 1, np.arange(len(tris)) * 3 + 2])
    b = np.concatenate([np.arange(len(tris)) * 3 + 1, np.arange(len(tris)) * 3 + 2, np.arange(len(tris)) * 3])
    mid = (pts[a[single]] + pts[b[single]]) / 2.0
    if len(mid) > limit:
        mid = mid[np.linspace(0, len(mid) - 1, limit).astype(int)]
    return mid


def slice_mesh(
    mesh,
    layer_height: float,
    z_offset: float = 0.0,
) -> list[tuple[float, Polygon]]:
    """Legacy single-region slicing: the largest polygon (with its holes)
    of each slice. `mesh` is a Trimesh or an (N, 3, 3) triangle array."""
    out = []
    for z, region in slice_mesh_regions(mesh, layer_height, z_offset):
        poly = largest_polygon(region)
        if not poly.is_empty and poly.area > _MIN_REGION_AREA_MM2:
            out.append((z, poly))
    return out


def islands(region, min_area: float = 0.0) -> list[Polygon]:
    """The region's polygons, largest first, dropping those under min_area."""
    if region.is_empty:
        return []
    polys = list(region.geoms) if region.geom_type == 'MultiPolygon' else [region]
    polys = [p for p in polys if p.geom_type == 'Polygon' and p.area > max(min_area, 0.0)]
    return sorted(polys, key=lambda p: p.area, reverse=True)


def support_map(regions, gap: float, rest: float | None = None) -> list[list[Polygon]]:
    """Per slice, the areas with nothing under them (overhangs): parts of
    `regions[i]` farther than `rest` (default half a track: a tow may sit half
    its width beyond the edge below) from what slice i-1 holds. The first
    filled slice lies on the bed; a slice over an empty one (None) has nothing
    under it.
    Spots narrower than one track are left out (a tow there rests on its
    neighbours). Each list holds the overhang polygons, largest first."""
    rest = 0.5 * gap if rest is None else float(rest)
    out: list[list[Polygon]] = []
    below, on_bed = None, True             # slices before the first filled one are empty
    for reg in regions:
        if reg is None or reg.is_empty:
            out.append([])
            below = None
            continue
        if on_bed:
            on_bed = False
            out.append([])
        else:
            free = reg if below is None else reg.difference(below.buffer(rest, join_style=1))
            # open by half a track: slivers along edges are not overhangs
            free = free.buffer(-0.5 * gap, join_style=1).buffer(0.5 * gap, join_style=1).intersection(reg)
            polys = [p for p in getattr(free, "geoms", [free])
                     if p.geom_type == "Polygon" and p.area >= gap * gap]
            out.append(sorted(polys, key=lambda p: -p.area))
        below = reg
    return out
