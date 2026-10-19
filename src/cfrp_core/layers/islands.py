"""One stroke over several separate pieces ("islands") of a layer: each is
filled from a port on its perimeter (so the connector to the next never
crosses track already laid) and the pieces are joined by straight
connectors -- fibre bridges, flagged `is_bridge`. Used by STL slices with
several islands and by flat parts that holes split into pieces."""

from __future__ import annotations

import math

import numpy as np
import shapely
from shapely.geometry import LineString, Point
from shapely.ops import nearest_points

from .. import geometry as geo
from ..config import DIAGONAL_45, ProcessConfig
from ..geometry.laminate import ring_from, wall_seam
from .planning import _ZIGZAGS


def _ring_loop(island, gap, r_min, cursor):
    """One closed lap of an island's wall centreline, from the point nearest
    `cursor` back to it: the fill for an island too thin for a Fermat
    spiral's two tracks (it enters and leaves at the same port)."""
    wall = geo.printable_boundary(island, gap, r_min)
    if wall.is_empty:
        return []
    wall = geo.largest_polygon(wall)
    if wall.geom_type != 'Polygon':
        return []
    return ring_from(wall, wall_seam(wall, cursor))


def island_path(island, config: ProcessConfig, angle, layer_idx, cursor, fallbacks=None):
    """Path for one island of a multi-island slice that enters AND leaves on
    the island's perimeter, so the connector to the next island never
    crosses track already laid. Spiral infills use the Fermat double spiral
    (in and back out); line infills use the serpentine, run from whichever
    end is nearer the cursor. An island too narrow for the zigzag's U-turns
    gets the Fermat spiral, one too thin for that one lap of its wall (both
    counted in `fallbacks`)."""
    gap, r_min = config.track_gap_mm, config.min_radius_mm
    zigzag = config.infill_type in _ZIGZAGS and geo.zigzag_fits(island, gap, r_min)
    if not zigzag:
        tracks = None if config.fill_solid else config.tracks_per_layer
        path = geo.fermat_spiral_path(island, gap, r_min, track_count=tracks, entry_hint=cursor)
        if not path:
            path = _ring_loop(island, gap, r_min, cursor)
            if path and fallbacks is not None:
                fallbacks['lap'] += 1
        elif config.infill_type in _ZIGZAGS and fallbacks is not None:
            fallbacks['zigzag'] += 1
        return path
    if config.infill_type == DIAGONAL_45:
        angle = 45.0 if layer_idx % 2 == 0 else -45.0
    path = geo.oriented_serpentine(island, angle, gap, r_min)
    if cursor is not None and len(path) > 1:
        d0 = math.hypot(path[0][0] - cursor[0], path[0][1] - cursor[1])
        d1 = math.hypot(path[-1][0] - cursor[0], path[-1][1] - cursor[1])
        if d1 < d0:
            path = path[::-1]
    return path


_CONNECTOR_STEP_MM = 0.75


def _nearest_island(polys, point):
    pt = Point(point)
    return min(polys, key=lambda p: p.exterior.distance(pt))


def _boundary_point_toward(island, other):
    """Point on island's outline nearest the other geometry."""
    return nearest_points(island.exterior, other)[0].coords[0]


def _exit_route(island, end_pt, target, gap):
    """If the straight connector end_pt -> target would cut across the
    island's own laid track, retrace the outermost track ring to the point
    facing the target first (the route Fermat uses between its children)."""
    core = island.buffer(-gap, join_style=1)
    if core.is_empty or not LineString([end_pt, target]).intersects(core):
        return []
    ring = geo.largest_polygon(island.buffer(-gap / 2.0, join_style=1))
    if ring.is_empty or ring.geom_type != 'Polygon':
        return []
    exit_pt = nearest_points(ring.exterior, Point(target))[0].coords[0]
    return geo.arc_along_ring(ring, end_pt, exit_pt)[1:]


def _straight(p0, p1, step=_CONNECTOR_STEP_MM):
    """Points after p0 up to and including p1, ~step apart."""
    n = max(1, int(math.ceil(math.hypot(p1[0] - p0[0], p1[1] - p0[1]) / step)))
    return [(p0[0] + (p1[0] - p0[0]) * k / n, p0[1] + (p1[1] - p0[1]) * k / n) for k in range(1, n + 1)]


def multi_island_layer(islands, config: ProcessConfig, angle, layer_idx, last_pt, fallbacks=None):
    """One stroke over every island: nearest island first, each filled from
    a port on its perimeter, joined by straight connectors (fibre bridges)
    densified to the usual point spacing so bridges can be flagged."""
    gap = config.track_gap_mm
    remaining = list(islands)
    if last_pt is not None:
        island = _nearest_island(remaining, last_pt)
        cursor = last_pt
    else:
        # First layer: start on the largest island, at the side facing the
        # island visited next, so the first connector is short.
        island = max(remaining, key=lambda p: p.area)
        others = [p for p in remaining if p is not island]
        nxt = min(others, key=lambda p: p.distance(island))
        cursor = _boundary_point_toward(island, nxt)
    path = [last_pt] if last_pt is not None else []
    while True:
        remaining.remove(island)
        sub = island_path(island, config, angle, layer_idx, cursor, fallbacks)
        if sub:
            if path:
                path.extend(_straight(path[-1], sub[0]))
            path.extend(sub)
            cursor = sub[-1]
        if not remaining:
            break
        nxt = _nearest_island(remaining, cursor)
        if sub:
            target = nearest_points(nxt.exterior, Point(cursor))[0].coords[0]
            route = _exit_route(island, cursor, target, gap)
            path.extend(route)
            if route:
                cursor = route[-1]
        island = nxt
    return geo.dedupe_path(path)


def flag_bridges(layer_trajectory, region, tol, skip=0):
    """Mark points whose incoming segment's midpoint lies outside the slice
    (inflated by `tol`): fibre spanning empty space between islands."""
    if len(layer_trajectory) < 2 or region.is_empty:
        return
    zone = region.buffer(tol)
    xy = np.array([(p['x'], p['y']) for p in layer_trajectory], dtype=np.float64)
    mid = 0.5 * (xy[1:] + xy[:-1])
    inside = shapely.contains_xy(zone, mid[:, 0], mid[:, 1])
    for k in np.nonzero(~inside)[0]:
        if k + 1 >= skip:
            layer_trajectory[k + 1]['is_bridge'] = True
