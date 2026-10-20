"""Flat multi-layer laminates (built-in outlines and Custom Outline)."""

from __future__ import annotations

import math

from shapely.geometry import LineString
from shapely.ops import unary_union

from .. import geometry as geo
from ..config import AUTO, ProcessConfig
from ..geometry._common import count_crossings, enforce_min_radius_clear, hole_keepouts
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
from .planning import _SPIRAL_INFILLS, ZigzagShifts, _fill_track_count, _points_within, plan_layers
from .recipe import RECIPE_CORES, Recipe, hole_notes, recipe_wanted


def part_pieces(base_poly, gap, r_min):
    """The pieces a part falls into where holes (or a narrow waist) leave
    less than one track between them: the part opened by half a track,
    each piece cut back to the part. One piece = not split. Pieces too small
    for one track turn are left out."""
    if base_poly.is_empty:
        return []
    opened = base_poly.buffer(-gap / 2.0, join_style=1).buffer(gap / 2.0, join_style=1)
    min_area = math.pi * (r_min + gap / 2.0) ** 2 * 0.25
    pieces = []
    for g in getattr(opened, 'geoms', [opened]):
        if g.geom_type == 'Polygon' and g.area >= min_area:
            piece = geo.largest_polygon(g.intersection(base_poly))
            if not piece.is_empty and piece.geom_type == 'Polygon':
                pieces.append(piece)
    return sorted(pieces, key=lambda p: -p.area)


def build_flat_laminate(config: ProcessConfig):
    """Flat multi-layer laminate:  z_i = z_offset + i·layer_height,
    fibre angle_i = layup[i mod len(layup)]; each layer's pattern, fill and
    direction from plan_layers (solid skins, spiral/zigzag hand-overs).

    Returns (trajectory, target_shapes, max_z, info). A ValueError from a
    layer's geometry is re-raised naming the layer. `info`: notes (what
    Auto (Best Fit) chose)."""
    g = config.geometry
    trajectory = []
    target_shapes = []
    max_z = config.z_offset_mm
    last_pt = last_dir = None
    layup_sequence = list(config.layup_deg) or [0.0]
    kin = KinematicsParams.from_config(config)
    seam_trim = max(0.0, float(config.seam_trim_mm))
    gap, r_min = config.track_gap_mm, config.min_radius_mm
    layer_height = max(float(config.layer_height_mm), 0.05)
    min_run = _ramp_min_run(layer_height, config.transition_angle_deg)

    base_poly = geo.base_polygon(g, gap, r_min)
    printable = geo.printable_boundary(base_poly, gap, r_min)
    # Holes that split the part: with Print All Islands every piece is
    # filled in one stroke, joined by flagged bridges. Else spirals fill the
    # largest piece as the part (on the holed outline whole, a Fermat spiral
    # found no room at all and the rings crossed at the necks); zigzags keep
    # the whole outline, their connectors already reaching every piece.
    pieces = part_pieces(base_poly, gap, r_min)
    multi = len(pieces) > 1 and g.stl_islands == "all"
    # Seam stagger: each Archimedean layer twisted so it ends (and the next
    # starts) this much further round; one layer has no seams to stagger.
    turn = float(config.seam_stagger_deg) / 360.0 if config.layers > 1 else 0.0
    spiral_only = all(p.pattern in _SPIRAL_INFILLS for p in plan_layers(config, config.layers))
    holed = bool(getattr(base_poly, 'interiors', ()))   # a waist without holes: Fermat visits both lobes
    fill_poly = pieces[0] if len(pieces) > 1 and not multi and spiral_only and holed else base_poly
    variants = {}
    shifts = ZigzagShifts(config)
    holes = [h.buffer(-0.1) for h in hole_keepouts(base_poly)]
    decided, chosen = {}, []                       # Auto: (parity, angle, first) -> (pattern, angle)
    strict = StrictBends(config)
    fill_region = unary_union(pieces) if multi else fill_poly
    climb_zone = printable.buffer(gap / 2.0 + 0.2) if strict.on else None
    # Fill Pockets: where every layer so far left the part empty
    empty = base_poly if config.fill_pockets else None
    # Layer recipe: edge rings, a zigzag / Fermat core, hole wraps (one piece)
    recipe = (Recipe(config, base_poly) if recipe_wanted(config) and not multi
              and base_poly.geom_type == 'Polygon' else None)
    plans = plan_layers(config, config.layers)

    for i, plan in enumerate(plans):
        layer_z = config.z_offset_mm + (i * layer_height)
        current_angle = layup_sequence[i % len(layup_sequence)]
        max_z = layer_z

        keep = 1 if last_pt is not None else 0

        def repair(path, keep=keep):
            return strict.repair(path, fill_region, keep_head=keep)

        def generate(pattern, angle, solid, peek=False, raw=False, plan=plan, last_pt=last_pt,
                     last_dir=last_dir, num=i + 1):
            """The layer's path for this pattern (repaired for Strict Bend
            Radius unless `raw`)."""
            spacing, offset = shifts.next(pattern, plan.index, angle, peek=peek)
            if multi:
                key = (pattern, solid)
                if key not in variants:
                    variants[key] = config.model_copy(update=dict(infill_type=pattern, fill_solid=solid))
                path = multi_island_layer(pieces, variants[key], angle, plan.index, last_pt,
                                          spacing=spacing, offset=offset)
                return path if raw else repair(path)
            count = (_fill_track_count(fill_poly, config, pattern) if solid else config.tracks_per_layer)

            def plain():
                return geo.laminate_layer(
                    fill_poly, gap, r_min, pattern, count, g.include_boundary, angle, layer_idx=plan.index,
                    last_pt=last_pt, last_dir=last_dir, lead_in=plan.lead_in if pattern == plan.pattern else 0.0,
                    seam_turn=turn, line_spacing=spacing, line_offset=offset, fill_centre=config.fill_centre,
                    strict=strict.on,
                )
            if recipe is not None and recipe.active and pattern in RECIPE_CORES and plan.pattern != AUTO:
                def core(poly, at, heading, idx):
                    return geo.laminate_layer(
                        poly, gap, r_min, pattern, count, False, angle, layer_idx=idx, last_pt=at,
                        last_dir=heading, line_spacing=spacing, line_offset=offset, strict=strict.on)
                # wrap only when the next layer is a recipe layer too (it unwraps)
                wrap_ok = num < len(plans) and plans[num].pattern in RECIPE_CORES
                path = recipe.layer(num, plan.index, wrap_ok, last_pt, last_dir, core, plain)
                if raw:
                    return path
                fixed = repair(path)
                if strict.on and count_crossings(fixed) > count_crossings(path):
                    # a bend repair crossed the recipe's own fibre (a join laid
                    # along an edge ring): the layer without the recipe, if
                    # that crosses less once repaired
                    alt = repair(plain())
                    if count_crossings(alt) < count_crossings(fixed):
                        recipe.drop(num)
                        return alt
                return fixed
            path = plain()
            return path if raw else repair(path)

        try:
            if plan.pattern == AUTO:
                # Auto fills every layer: each candidate solid, the best laid.
                key = (plan.index % 2, round(current_angle % 180.0, 3), i == 0)
                reused = None
                pockets = empty is not None and empty.area > 0.01 * base_poly.area
                if key in decided and not pockets:
                    # the same choice for a layer like one judged -- unless it
                    # lays worse here (the start differs: a crossing, a hole)
                    pattern, angle, cov = decided[key]
                    path = generate(pattern, angle, True, peek=True)
                    m = auto.measure(path, base_poly, holes, gap, r_min, last_pt) if path else None
                    if m is not None and m[2] == 0 and m[0] >= cov - 0.03:
                        shifts.next(pattern, plan.index, angle)
                        raw_path, reused = path, auto.Choice(pattern, angle, *m[:3], 0.0)
                        chosen.append((i + 1, reused))
                if reused is None:
                    choice, raw_path = auto.choose(lambda p, a: generate(p, a, True, peek=True, raw=strict.on),
                                                   auto.candidates_for(fill_poly, current_angle),
                                                   base_poly, holes, gap, r_min, last_pt, strict=strict.on,
                                                   repair=repair if strict.on else None,
                                                   empty=empty if pockets else None)
                    if choice is None:
                        raise ValueError("no pattern fits this layer.")
                    shifts.next(choice.pattern, plan.index, choice.angle)
                    decided[key] = (choice.pattern, choice.angle, choice.coverage)
                    chosen.append((i + 1, choice))
            else:
                raw_path = generate(plan.pattern, current_angle, plan.solid)
            target_shapes.append((layer_z, printable, i + 1))
        except ValueError as exc:
            raise ValueError(f"Layer {i + 1}: {exc}") from exc

        strict.record(i + 1, raw_path)
        anchor_n = 0
        # A recipe layer starts on its edge ring: the ring's own first mm are
        # the anchor (a straight stub back from there lay across the ring's end).
        recipe_first = (recipe is not None and recipe.active and plan.pattern in RECIPE_CORES
                        and 1 not in recipe.plain_layers)
        if i == 0 and (plan.lead_in or recipe_first) and not multi:
            anchor_n = _points_within(raw_path, float(config.anchor_length_mm))
        elif i == 0:
            n_before = len(raw_path)
            raw_path = add_lead_in_anchor(raw_path, float(config.anchor_length_mm))
            anchor_n = len(raw_path) - n_before

        raw_path = enforce_min_radius_clear(raw_path, r_min, hole_keepouts(base_poly), gap * 0.25)
        raw_path = repair(raw_path)
        strict.record(i + 1, raw_path)
        layer_trajectory = plan_layer(raw_path, layer_z, i + 1, kin)
        _mark_anchor(layer_trajectory, anchor_n)
        if multi:
            flag_bridges(layer_trajectory, unary_union(pieces), gap, skip=anchor_n + 1)
        append_layer(trajectory, layer_trajectory, i + 1, seam_trim, kin.v_min, min_run,
                     strict=strict.climb(climb_zone))
        if empty is not None and len(layer_trajectory) > 1:
            laid = [(p['x'], p['y']) for p in layer_trajectory]
            empty = empty.difference(LineString(laid).buffer(gap / 2.0))

        if layer_trajectory:
            last_pt = (layer_trajectory[-1]['x'], layer_trajectory[-1]['y'])
            last_dir = _end_direction(layer_trajectory)

    info = [auto.summary(chosen)] if chosen else []
    if recipe is not None:
        info.extend(recipe.note(config))
    elif recipe_wanted(config):
        info.append("Layer recipe (Edge Rings, Wrap Holes): not used on a part in several pieces.")
    info.extend(hole_notes(config))
    if strict.note():
        info.append(strict.note())
    return trajectory, target_shapes, max_z, info
