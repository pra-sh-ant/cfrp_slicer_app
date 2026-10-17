"""Mesh loading and planar slicing via Trimesh.

Supports STL, OBJ, PLY, 3MF, STEP and every format Trimesh can read.
Slicing and segment chaining use Trimesh's C-accelerated routines;
contours are converted to Shapely Polygons for the infill generators.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from shapely.geometry import Polygon

if TYPE_CHECKING:
    import trimesh


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
        mesh.fill_holes()
        mesh.fix_normals()
    return mesh


def mesh_bounds(mesh) -> tuple[float, float, float, float, float, float]:
    """(x_min, y_min, z_min, x_max, y_max, z_max) of the mesh."""
    if isinstance(mesh, np.ndarray):
        mins = mesh.reshape(-1, 3).min(axis=0)
        maxs = mesh.reshape(-1, 3).max(axis=0)
        return (*mins, *maxs)
    return (*mesh.bounds[0], *mesh.bounds[1])


def slice_mesh(
    mesh,
    layer_height: float,
    z_offset: float = 0.0,
) -> list[tuple[float, Polygon]]:
    """Slice a mesh at regular z intervals.

    Returns [(z_print, polygon), ...] for each non-empty slice.
    z_print = z_offset + layer_index * layer_height.

    `mesh` can be a Trimesh object (preferred) or an (N,3,3) triangle
    array for backwards compatibility.
    """
    import trimesh

    if isinstance(mesh, np.ndarray):
        mesh = trimesh.Trimesh(
            vertices=mesh.reshape(-1, 3),
            faces=np.arange(mesh.reshape(-1, 3).shape[0]).reshape(-1, 3),
        )

    z_min, z_max = float(mesh.bounds[0][2]), float(mesh.bounds[1][2])
    z_planes = np.arange(z_min + layer_height / 2.0, z_max, layer_height)

    if len(z_planes) == 0:
        return []

    slices: list[tuple[float, Polygon]] = []
    layer_idx = 0

    for z in z_planes:
        try:
            section = mesh.section(
                plane_origin=[0, 0, z],
                plane_normal=[0, 0, 1],
            )
        except Exception:
            continue

        if section is None:
            continue

        try:
            path_2d, _ = section.to_planar()
        except Exception:
            continue

        poly = _path2d_to_polygon(path_2d)
        if poly is not None and not poly.is_empty and poly.area > 0.01:
            z_print = z_offset + layer_idx * layer_height
            slices.append((z_print, poly))
            layer_idx += 1

    return slices


def _path2d_to_polygon(path_2d) -> Polygon | None:
    """Convert a trimesh.path.Path2D to the largest Shapely Polygon."""
    try:
        polys = path_2d.polygons_full
    except Exception:
        return None

    if not polys:
        return None

    best = max(polys, key=lambda p: p.area)
    if not best.is_valid:
        best = best.buffer(0)
    return best if not best.is_empty else None
