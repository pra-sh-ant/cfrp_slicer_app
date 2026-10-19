"""Imported meshes: slice, fill each slice (with pattern fallbacks), join
islands, report mesh problems."""

from __future__ import annotations

import math
from collections import Counter

import numpy as np
import shapely
from shapely.geometry import LineString, Point
from shapely.ops import nearest_points, unary_union

from .. import geometry as geo
from ..config import ARCHIMEDEAN, CORNER_STEP, DIAGONAL_45, FERMAT, ProcessConfig
from ..geometry._common import enforce_min_radius_clear, hole_keepouts
from ..geometry.laminate import ring_from, wall_seam
from ..geometry.mesh_slicer import islands as mesh_islands
from ..geometry.mesh_slicer import load_stl, slice_mesh_regions
from .common import (
    KinematicsParams,
    _end_direction,
    _mark_anchor,
    _ramp_min_run,
    add_lead_in_anchor,
    append_layer,
    plan_layer,
)
from .planning import (
    _ZIGZAGS,
    _fill_track_count,
    _points_within,
    plan_layers,
    skin_summary,
    stl_skins,
)


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


def _island_path(island, config: ProcessConfig, angle, layer_idx, cursor, fallbacks=None):
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


def _multi_island_layer(islands, config: ProcessConfig, angle, layer_idx, last_pt, fallbacks=None):
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
        sub = _island_path(island, config, angle, layer_idx, cursor, fallbacks)
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


def _stl_layer_path(islands, config: ProcessConfig, angle, layer_idx, last_pt, last_dir=None,
                    fallbacks=None, lead_in=0.0):
    """2D path for one sliced layer. A lone island goes through the normal
    flat-laminate generator. Where the chosen pattern cannot work on this
    slice it falls back instead of dropping the layer (each counted in
    `fallbacks`): a zigzag on walls too thin for its U-turns, a Fermat
    spiral with no room for its two tracks, or Corner-Step with no ring
    left, all become Archimedean rings; an Archimedean or Corner-Step track
    count that does not fit becomes as many rings as fit."""
    if fallbacks is None:
        fallbacks = Counter()
    if len(islands) > 1:
        return _multi_island_layer(islands, config, angle, layer_idx, last_pt, fallbacks)
    poly = islands[0]
    gap, r_min = config.track_gap_mm, config.min_radius_mm
    pattern = config.infill_type
    if pattern in _ZIGZAGS and not geo.zigzag_fits(poly, gap, r_min):
        pattern = ARCHIMEDEAN
        fallbacks['zigzag'] += 1
    tracks = (_fill_track_count(poly, config, pattern) if config.fill_solid
              else config.tracks_per_layer)

    def attempt(kind, count):
        return geo.laminate_layer(
            poly, gap, r_min, kind, count, config.geometry.include_boundary,
            angle, layer_idx=layer_idx, last_pt=last_pt, last_dir=last_dir,
            lead_in=lead_in if kind in (ARCHIMEDEAN, CORNER_STEP) else 0.0,
        )

    if pattern == ARCHIMEDEAN:
        try:
            return attempt(ARCHIMEDEAN, tracks)
        except ValueError:
            return attempt(ARCHIMEDEAN, None)
    if pattern == FERMAT:
        try:
            path = attempt(FERMAT, tracks)
        except ValueError:
            path = []
        if len(path) > 2:
            return path
        fallbacks['fermat'] += 1
        return attempt(ARCHIMEDEAN, None)
    if pattern == CORNER_STEP:
        for count in range(int(tracks or 0), 0, -1):
            try:
                path = attempt(CORNER_STEP, count)
            except ValueError:
                continue
            if path:
                return path
        fallbacks['corner'] += 1
        return attempt(ARCHIMEDEAN, None)
    return attempt(pattern, tracks)


_FALLBACK_NOTES = {
    'zigzag': "{n} slice(s) or island(s) are too narrow for the zigzag's U-turns (walls under "
              "{w:.1f} mm) and were filled with concentric rings instead.",
    'fermat': "{n} slice(s) have no room for the Fermat spiral's two tracks and were filled "
              "with Archimedean rings instead.",
    'corner': "{n} slice(s) fit no Corner-Step ring and were filled with Archimedean rings instead.",
    'lap': "{n} island(s) too thin for a spiral got one lap of their wall.",
}


def _flag_bridges(layer_trajectory, region, tol, skip=0):
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


def _mesh_report_notes(report, slices, layer_height):
    """Plain-language warnings / info from slice_mesh_regions' report."""
    warnings, info = [], []
    if report.get('stitched'):
        warnings.append(
            f"The mesh has holes: {report['stitched']} gap(s) in slice outlines were bridged "
            f"(Stitch Gap). Check or repair the model."
        )
    if report.get('open'):
        warnings.append(
            f"The mesh has holes too large to bridge: {report['open']} open outline(s) were "
            f"dropped, so parts of some slices are missing. Repair the model."
        )
    if slices:
        top = max(z for z, _ in slices)
        inside = [z for z in report.get('empty_planes', []) if z < top + layer_height]
        if inside:
            warnings.append(
                f"{len(inside)} slice(s) inside the model came out empty; the part has gaps "
                f"there (broken mesh, or separate bodies stacked with a gap)."
            )
    if report.get('bodies', 1) > 1:
        info.append(f"The mesh has {report['bodies']} separate bodies; they were merged where they overlap.")
    if report.get('subtractive'):
        info.append(f"{report['subtractive']} inward-facing body(ies) were treated as cavities.")
    return warnings, info


def build_stl_laminate(config: ProcessConfig):
    """STL-imported mesh: slice at layer_height intervals, generate infill
    per slice.

    `stl_islands="largest"` keeps one island per slice; `"all"` fills every
    island in one stroke, connector points flagged `is_bridge`.

    Returns (trajectory, target_shapes, max_z, warnings, info): warnings are
    problems found in the mesh or the fill (broken mesh, walls thinner than
    one track), info are neutral notes (several bodies merged)."""
    g = config.geometry
    mesh = load_stl(g.stl_path)
    tris = np.asarray(mesh.triangles, dtype=np.float64) * float(g.stl_scale)
    size = tris.reshape(-1, 3).max(axis=0) - tris.reshape(-1, 3).min(axis=0)
    gap, r_min = config.track_gap_mm, config.min_radius_mm
    if size[:2].max() < max(5.0, 2.0 * r_min + gap):
        raise ValueError(
            f"The model is only {size[0]:.3g} x {size[1]:.3g} x {size[2]:.3g} mm. If it was saved in "
            f"inches set STL Scale to 25.4, in cm to 10, in metres to 1000."
        )

    layer_height = max(float(config.layer_height_mm), 0.05)
    report: dict = {}
    slices = slice_mesh_regions(
        tris, layer_height, z_offset=float(config.z_offset_mm),
        closing_radius=float(g.stl_closing_radius_mm), stitch_mm=float(g.stl_stitch_mm),
        report=report,
    )

    if not slices:
        raise ValueError(
            f"STL mesh produced no valid slices (model {size[2]:.3g} mm tall, layer height "
            f"{layer_height:g} mm). Check the file, its scale (STL Scale) and the layer height."
        )
    warnings, info = _mesh_report_notes(report, slices, layer_height)
    if not mesh.is_watertight:
        warnings.insert(0,
            "The mesh is not watertight (it has open edges or missing faces), so its slices may "
            "not match the intended part, e.g. a missing wall reads as solid. Repair the model "
            "if the preview looks wrong."
        )

    trajectory: list[dict] = []
    target_shapes: list[tuple] = []
    max_z = float(config.z_offset_mm)
    last_pt = last_dir = None
    layup_sequence = list(config.layup_deg) or [0.0]
    kin = KinematicsParams.from_config(config)
    seam_trim = max(0.0, float(config.seam_trim_mm))
    gap, r_min = config.track_gap_mm, config.min_radius_mm
    # An island too small for one track turn cannot hold fibre.
    min_island_area = math.pi * (r_min + gap / 2.0) ** 2 * 0.25
    multi = g.stl_islands == "all"
    min_run = _ramp_min_run(layer_height, config.transition_angle_deg)
    thin_slices = 0
    fallbacks = Counter()

    found_all = [mesh_islands(region, min_island_area) for _z, region in slices]
    printed = [(found if multi else found[:1]) for found in found_all]
    bottom, top = int(config.solid_bottom_layers), int(config.solid_top_layers)
    # Skins where a slice's top or bottom is exposed (and the first / last
    # slices), judged on the islands that are printed.
    skins = (stl_skins([unary_union(isl) if isl else None for isl in printed], bottom, top)
             if bottom or top else [False] * len(slices))
    plans, first = [], None
    variants = {}

    def layer_config(plan):
        """config with this layer's pattern and fill (cached per pair)."""
        key = (plan.pattern, plan.solid)
        if key == (config.infill_type, bool(config.fill_solid)):
            return config
        if key not in variants:
            variants[key] = config.model_copy(update=dict(infill_type=plan.pattern, fill_solid=plan.solid))
        return variants[key]

    for i, (z_print, _region) in enumerate(slices):
        layer_z = z_print
        current_angle = layup_sequence[i % len(layup_sequence)]
        max_z = layer_z

        found = found_all[i]
        if not found:
            continue
        # A wall narrower than one track: eroding half a track leaves ~nothing.
        if any(p.buffer(-0.5 * gap).area < 0.05 * p.area for p in found):
            thin_slices += 1
        islands = printed[i]
        printable = unary_union([geo.printable_boundary(p, gap, r_min) for p in islands])
        target_shapes.append((layer_z, printable, i + 1))

        # Plan from the first printed slice (it is layer 0 of the plan: first
        # layer pattern / solid, its direction, the ring lead-in).
        if not trajectory:
            first = i
            plans = plan_layers(config, len(slices) - i, skins[i:], offset=i)
        plan = plans[i - first]
        try:
            lead = plan.lead_in if len(islands) == 1 else 0.0
            raw_path = _stl_layer_path(islands, layer_config(plan), current_angle, plan.index, last_pt, last_dir,
                                       fallbacks, lead_in=lead)
        except ValueError:
            continue
        if not raw_path:
            continue

        anchor_n = 0
        if not trajectory and lead:
            anchor_n = _points_within(raw_path, float(config.anchor_length_mm))
        elif not trajectory:
            n_before = len(raw_path)
            raw_path = add_lead_in_anchor(raw_path, float(config.anchor_length_mm))
            anchor_n = len(raw_path) - n_before

        raw_path = enforce_min_radius_clear(
            raw_path, r_min, [h for p in islands for h in hole_keepouts(p)], gap * 0.25)
        layer_trajectory = plan_layer(raw_path, layer_z, i + 1, kin)
        _mark_anchor(layer_trajectory, anchor_n)
        if len(islands) > 1:
            _flag_bridges(layer_trajectory, unary_union(islands), gap, skip=anchor_n + 1)
        append_layer(trajectory, layer_trajectory, i + 1, seam_trim, kin.v_min, min_run)

        if layer_trajectory:
            last_pt = (layer_trajectory[-1]['x'], layer_trajectory[-1]['y'])
            last_dir = _end_direction(layer_trajectory)

    if not trajectory:
        if thin_slices:
            raise ValueError(
                f"The model's walls are thinner than one {gap:g} mm track on {thin_slices} "
                f"slice(s), so no fibre fits. Thicken the walls, reduce Track Gap, or check "
                f"the model's scale (STL Scale)."
            )
        hint = (" The Fermat spiral needs room for two tracks (in and back out); Archimedean "
                "or Corner-Step fill single-track walls." if config.infill_type == FERMAT else "")
        raise ValueError(
            "No STL slice could be filled with these settings. Reduce Track Gap, "
            "Min Bend Radius or Tracks Per Layer, or check the model's scale (mm)." + hint
        )
    if thin_slices:
        warnings.append(
            f"{thin_slices} slice(s) have walls thinner than one {gap:g} mm track: the tow there is "
            f"wider than the wall (or the wall is skipped). Thicken the walls or reduce Track Gap."
        )
    for kind, n in sorted(fallbacks.items()):
        info.append(_FALLBACK_NOTES[kind].format(n=n, w=2.0 * max(r_min, gap) + gap))
    if bottom or top:
        summary = skin_summary(plans, offset=first or 0)
        if summary:
            info.append(summary)
    info.extend(dict.fromkeys(p.note for p in plans if p.note))
    return trajectory, target_shapes, max_z, warnings, info
