"""Imported meshes: slice, fill each slice (with pattern fallbacks), join
islands, report mesh problems."""

from __future__ import annotations

import math
from collections import Counter

import numpy as np
from shapely.ops import unary_union

from .. import geometry as geo
from ..config import ARCHIMEDEAN, AUTO, CORNER_STEP, FERMAT, ProcessConfig
from ..geometry._common import enforce_min_radius_clear, hole_keepouts
from ..geometry.mesh_slicer import islands as mesh_islands
from ..geometry.mesh_slicer import load_stl, open_edges, slice_mesh_regions
from . import auto
from .common import (
    KinematicsParams,
    StrictBends,
    _end_direction,
    _mark_anchor,
    _ramp_min_run,
    add_lead_in_anchor,
    append_layer,
    plan_layer,
)
from .islands import flag_bridges, multi_island_layer
from .planning import (
    _ZIGZAGS,
    ZigzagShifts,
    _fill_track_count,
    _points_within,
    plan_layers,
    skin_summary,
    stl_skins,
)


def _stl_layer_path(islands, config: ProcessConfig, angle, layer_idx, last_pt, last_dir=None,
                    fallbacks=None, lead_in=0.0, spacing=None, offset=0.0, strict=None):
    """_stl_layer_raw, repaired for Strict Bend Radius when `strict` (a
    StrictBends) is on."""
    path = _stl_layer_raw(islands, config, angle, layer_idx, last_pt, last_dir, fallbacks, lead_in, spacing, offset)
    if strict is None or not strict.on:
        return path
    return strict.repair(path, unary_union(islands), keep_head=1 if last_pt is not None else 0)


def _stl_layer_raw(islands, config: ProcessConfig, angle, layer_idx, last_pt, last_dir=None,
                   fallbacks=None, lead_in=0.0, spacing=None, offset=0.0):
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
        return multi_island_layer(islands, config, angle, layer_idx, last_pt, fallbacks, spacing, offset)
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
            line_spacing=spacing, line_offset=offset, fill_centre=config.fill_centre,
            strict=config.strict_bend_radius,
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

    Returns (trajectory, target_shapes, max_z, warnings, info, markers):
    warnings are problems found in the mesh or the fill (broken mesh, walls
    thinner than one track), info are neutral notes (several bodies merged),
    markers (x, y, z, kind) where the mesh is broken (open edges, bridged
    gaps, dropped outlines, empty slices), at print Z."""
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
    markers = list(report.get('markers', []))
    if not mesh.is_watertight:
        warnings.insert(0,
            "The mesh is not watertight (it has open edges or missing faces), so its slices may "
            "not match the intended part, e.g. a missing wall reads as solid. Repair the model "
            "if the preview looks wrong."
        )
        to_print = report.get('to_print_z', lambda zz: zz)
        markers += [(float(x), float(y), float(to_print(z)), 'open edge') for x, y, z in open_edges(tris)]
    if markers:
        info.append(f"{len(markers)} mesh problem spot(s) are marked in the 3D view (Problems).")

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
    shifts = ZigzagShifts(config)
    decided, chosen = {}, []                       # Auto: (parity, angle, outline) -> (pattern, angle)
    strict = StrictBends(config)
    prev_zone = None

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
        lead = plan.lead_in if len(islands) == 1 else 0.0
        try:
            if plan.pattern == AUTO:
                # Auto fills every slice; a slice like one already judged
                # (same outline, direction, angle) gets the same pattern.
                region = unary_union(islands)
                sig = (plan.index % 2, round(current_angle % 180.0, 3), len(islands),
                       round(region.area), tuple(round(b) for b in region.bounds),
                       sum(len(getattr(p, 'interiors', ())) for p in islands))
                holes = [h.buffer(-0.1) for p in islands for h in hole_keepouts(p)]

                def generate(pattern, angle, peek=False, raw=False, plan=plan, islands=islands, last_pt=last_pt,
                             last_dir=last_dir):
                    sp, off = shifts.next(pattern, plan.index, angle, peek=peek)
                    cfg = variants.setdefault((pattern, True), config.model_copy(
                        update=dict(infill_type=pattern, fill_solid=True)))
                    return _stl_layer_path(islands, cfg, angle, plan.index, last_pt, last_dir, Counter(),
                                           lead_in=0.0, spacing=sp, offset=off,
                                           strict=None if raw else strict)

                def repair(path, region=region, keep=1 if last_pt is not None else 0):
                    return strict.repair(path, region, keep_head=keep)
                reused = None
                if sig in decided:
                    pattern, angle, cov = decided[sig]
                    path = generate(pattern, angle, peek=True)
                    m = auto.measure(path, region, holes, gap, r_min, last_pt) if path else None
                    if m is not None and m[2] == 0 and m[0] >= cov - 0.03:
                        shifts.next(pattern, plan.index, angle)
                        raw_path, reused = path, auto.Choice(pattern, angle, *m[:3], 0.0)
                        chosen.append((i + 1, reused))
                if reused is None:
                    choice, raw_path = auto.choose(lambda p, a: generate(p, a, peek=True, raw=strict.on),
                                                   auto.candidates_for(region, current_angle), region, holes,
                                                   gap, r_min, last_pt, strict=strict.on,
                                                   repair=repair if strict.on else None)
                    if choice is None:
                        continue
                    shifts.next(choice.pattern, plan.index, choice.angle)
                    decided[sig] = (choice.pattern, choice.angle, choice.coverage)
                    chosen.append((i + 1, choice))
                lead = 0.0
            else:
                spacing, offset = shifts.next(plan.pattern, plan.index, current_angle)
                raw_path = _stl_layer_path(islands, layer_config(plan), current_angle, plan.index, last_pt,
                                           last_dir, fallbacks, lead_in=lead, spacing=spacing, offset=offset,
                                           strict=strict)
        except ValueError:
            continue
        if not raw_path:
            continue

        strict.record(i + 1, raw_path)
        anchor_n = 0
        if not trajectory and lead:
            anchor_n = _points_within(raw_path, float(config.anchor_length_mm))
        elif not trajectory:
            n_before = len(raw_path)
            raw_path = add_lead_in_anchor(raw_path, float(config.anchor_length_mm))
            anchor_n = len(raw_path) - n_before

        raw_path = enforce_min_radius_clear(
            raw_path, r_min, [h for p in islands for h in hole_keepouts(p)], gap * 0.25)
        raw_path = strict.repair(raw_path, unary_union(islands), keep_head=1 if last_pt is not None else 0)
        strict.record(i + 1, raw_path)
        layer_trajectory = plan_layer(raw_path, layer_z, i + 1, kin)
        _mark_anchor(layer_trajectory, anchor_n)
        if len(islands) > 1:
            flag_bridges(layer_trajectory, unary_union(islands), gap, skip=anchor_n + 1)
        zone = None
        if strict.on:
            here = printable.buffer(gap / 2.0 + 0.2)
            zone = here if prev_zone is None else here.union(prev_zone)
            prev_zone = here
        append_layer(trajectory, layer_trajectory, i + 1, seam_trim, kin.v_min, min_run,
                     strict=strict.climb(zone))

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
    if chosen:
        info.append(auto.summary(chosen))
    if strict.note():
        info.append(strict.note())
    return trajectory, target_shapes, max_z, warnings, info, markers
