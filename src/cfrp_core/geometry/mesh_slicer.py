"""Mesh loading and planar slicing.

Supports STL, OBJ, PLY, 3MF, STEP and every format Trimesh can read.
Slicing is done here in numpy + shapely (no scipy/networkx, which Trimesh's
own `section()` / `fill_holes()` need): triangle-plane segments are chained
into rings and the rings combined with the even-odd rule, so slices keep
holes and every island, in world XY coordinates (Cura / Orca behaviour).
"""
from __future__ import annotations

from functools import reduce
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from shapely.geometry import MultiLineString, Polygon
from shapely.ops import linemerge

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
    rings = []
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
    return rings


def segments_to_region(segs: np.ndarray, closing_radius: float = DEFAULT_CLOSING_RADIUS_MM):
    """Chain section segments into rings and combine them with the even-odd
    rule (an island inside a hole inside a part stays an island). Returns a
    Polygon or MultiPolygon (possibly empty)."""
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
    closed.extend(_join_open_chains(open_chains, tol))

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


def slice_mesh_regions(
    mesh,
    layer_height: float,
    z_offset: float = 0.0,
    closing_radius: float = DEFAULT_CLOSING_RADIUS_MM,
) -> list[tuple[float, object]]:
    """Slice a mesh at mid-layer planes, keeping holes and all islands.

    Returns [(z_print, Polygon | MultiPolygon), ...] for each non-empty
    slice. z_print = z_offset + k * layer_height, k counted from the first
    non-empty slice, so an empty slice keeps its Z slot (no silent collapse).
    """
    tris = _triangles(mesh)
    z_min, z_max = float(tris[:, :, 2].min()), float(tris[:, :, 2].max())
    z_planes = np.arange(z_min + layer_height / 2.0, z_max, layer_height)

    slices = []
    first = None
    for k, z in enumerate(z_planes):
        region = segments_to_region(section_segments(tris, float(z)), closing_radius)
        if region.is_empty or region.area <= _MIN_REGION_AREA_MM2:
            continue
        if first is None:
            first = k
        slices.append((z_offset + (k - first) * layer_height, region))
    return slices


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
