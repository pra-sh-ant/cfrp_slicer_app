"""Pre-slice automatic adjustments (spec §4).

Hard errors are raised by `ProcessConfig` validation itself. This module
applies the *coupled* adjustments that are allowed to proceed with an info
note instead of failing.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .config import (
    ARCHIMEDEAN,
    CORNER_STEP,
    DIAGONAL_45,
    FERMAT,
    ORIENTED_SERPENTINE,
    SPECIALTY_SHAPES,
    ProcessConfig,
)
from .geometry import base_polygon
from .geometry.laminate import max_track_count


@dataclass(frozen=True)
class ResolvedConfig:
    config: ProcessConfig
    notes: tuple[str, ...]


def cure_speed(config: ProcessConfig) -> float | None:
    """The fastest the tow may move and still get the resin's cure dose
    under the head's UV LED (mm/s), or None while any of the three is unset.

    A point of tow is lit for spot / v seconds, so its dose is
    irradiance x spot / v: v <= irradiance [mW/cm²] x spot [mm] / dose
    [mJ/cm²] (mW/mJ = 1/s). Assumes even light over the spot and a dose that
    adds up linearly (no oxygen inhibition, no dark cure)."""
    dose = float(config.material.cure_dose_mj_cm2)
    irr = float(config.gcode.uv_irradiance_mw_cm2)
    spot = float(config.gcode.uv_spot_mm)
    if dose <= 0 or irr <= 0 or spot <= 0:
        return None
    return irr * spot / dose


def resolve_process_constraints(config: ProcessConfig) -> ResolvedConfig:
    """Return the adjusted config plus one plain-language note per adjustment."""
    notes: list[str] = []
    min_radius = float(config.min_radius_mm)
    track_gap = float(config.track_gap_mm)
    k = config.kinematics
    max_speed = float(k.v_max_mm_s)
    shape = config.geometry.shape

    if shape not in SPECIALTY_SHAPES and shape not in ("STL Import", "Custom Outline"):
        narrow_dimension = min(float(config.geometry.width_mm), float(config.geometry.height_mm))
        cornering_window = narrow_dimension - (2.0 * min_radius) - track_gap
        if min_radius > track_gap / 2.0:
            notes.append(
                f"Radius clearance uses {2.0 * min_radius:.2f} mm across the "
                f"{narrow_dimension:.2f} mm narrow dimension; about "
                f"{cornering_window:.2f} mm remains for concentric tracks."
            )

    kin_update = {}
    # UV cure cap (Phase 45): no speed above what still cures the resin.
    cure = cure_speed(config)
    if cure is not None:
        for field, label in (("v_max_mm_s", "Print Speed (Max)"), ("v_overhang_mm_s", "Overhang Speed"),
                             ("v_z_mm_s", "Z Pull Speed")):
            if float(getattr(k, field)) > cure:
                kin_update[field] = cure
                notes.append(f"{label} limited to {cure:.2f} mm/s by the UV cure: "
                             f"{float(config.gcode.uv_irradiance_mw_cm2):g} mW/cm² over "
                             f"{float(config.gcode.uv_spot_mm):g} mm gives the "
                             f"{float(config.material.cure_dose_mj_cm2):g} mJ/cm² the resin needs "
                             f"up to that speed.")
        max_speed = min(max_speed, cure)
    elif any(float(v) > 0 for v in (config.material.cure_dose_mj_cm2, config.gcode.uv_irradiance_mw_cm2,
                                    config.gcode.uv_spot_mm)):
        notes.append("UV cure speed cap off: set Cure Dose, UV Irradiance and UV Spot Length (all three) "
                     "to use it.")
    minimum_speed = float(k.v_min_mm_s)
    if minimum_speed > max_speed:
        kin_update["v_min_mm_s"] = max_speed
        notes.append(f"Min Bend Speed limited to Print Speed (Max), {max_speed:.2f} mm/s.")

    pump_floor = float(k.pump_stall_floor_mm_s)
    if pump_floor > max_speed:
        kin_update["pump_stall_floor_mm_s"] = max_speed
        notes.append(f"Pump-Stall Floor limited to Print Speed (Max), {max_speed:.2f} mm/s-equiv.")

    update: dict = {"track_gap_mm": track_gap}

    # Flat parts: Archimedean rings / the Corner-Step rectangle need an exact
    # track count; cap it to what fits instead of failing every layer.
    # (With Fill Solid the layer builder lays as many as fit itself.)
    if (shape not in SPECIALTY_SHAPES and shape != "STL Import" and not config.fill_solid
            and config.infill_type in (ARCHIMEDEAN, CORNER_STEP)):
        base = base_polygon(config.geometry, track_gap, min_radius)
        fit = max_track_count(base, track_gap, min_radius, config.infill_type,
                              config.geometry.include_boundary)
        if fit is not None and int(config.tracks_per_layer) > fit:
            if fit < 1:
                raise ValueError(
                    f"No {config.infill_type} track fits this outline at {track_gap:.2f} mm gap and "
                    f"{min_radius:.1f} mm min bend radius. Enlarge the part or reduce Min Bend Radius."
                )
            update["tracks_per_layer"] = fit
            notes.append(
                f"Tracks Per Layer reduced from {int(config.tracks_per_layer)} to {fit}: that is "
                f"how many {config.infill_type} tracks fit at {track_gap:.2f} mm gap with "
                f"{min_radius:.1f} mm min bend radius."
            )

    # A round revolve layer is sliced only while its outer radius leaves room
    # for every ring at >= min bend radius: R >= r_min + (tracks + 0.5) * gap
    # (layers.build_revolve_shell). If even the base radius fails that, every
    # layer would be skipped, so cap the track count instead.
    shape_is_round_revolve = config.geometry.is_revolve and shape != "Hollow Square Tube (Z)"
    if shape_is_round_revolve:
        base_r = max(float(config.geometry.base_radius_mm), 1.0)
        fit = math.floor((base_r - min_radius) / track_gap - 0.5 + 1e-9)
        if fit < 1:
            raise ValueError(
                f"Base radius {base_r:.1f} mm is too small for one {track_gap:.2f} mm track at "
                f"{min_radius:.1f} mm min bend radius (needs at least "
                f"{min_radius + 1.5 * track_gap:.1f} mm). Enlarge the part or reduce Min Bend Radius."
            )
        if int(config.tracks_per_layer) > fit:
            update["tracks_per_layer"] = fit
            notes.append(
                f"Tracks Per Layer reduced from {int(config.tracks_per_layer)} to {fit}: a "
                f"{base_r:.1f} mm radius shell fits {fit} rings at {track_gap:.2f} mm gap with "
                f"{min_radius:.1f} mm min bend radius."
            )

    # A Fermat spiral reverses once per region, between its two arms, which
    # are adjacent tracks one gap apart: that U-turn is at most gap/2 radius
    # however wide the region. A revolve shell does it on every layer;
    # Archimedean never reverses, so use it there whenever gap < 2·r_min.
    wall_band = int(update.get("tracks_per_layer", config.tracks_per_layer)) * track_gap
    if config.infill_type == FERMAT and min_radius > 0 and track_gap < 2.0 * min_radius:
        if config.geometry.is_revolve:
            update["infill_type"] = ARCHIMEDEAN
            notes.append(
                f"Fermat spiral reverses between adjacent tracks ({track_gap / 2.0:.2f} mm radius, "
                f"below the {min_radius:.1f} mm min bend radius) on every layer of a "
                f"{wall_band:.1f} mm shell wall; using Archimedean (no reversals) instead."
            )
        elif config.strict_bend_radius:
            notes.append(
                f"Fermat spiral (Strict Bend Radius): the centre turn between adjacent tracks "
                f"({track_gap / 2.0:.2f} mm radius) becomes a loop at the {min_radius:.1f} mm min bend "
                f"radius into the unfilled middle, or the spiral turns a ring earlier where there is no "
                f"room."
            )
        else:
            notes.append(
                f"Fermat spiral: its centre U-turn joins adjacent tracks, at most "
                f"{track_gap / 2.0:.2f} mm radius (below the {min_radius:.1f} mm min bend radius), so "
                f"those points are speed-limited (curvature warning). Archimedean never reverses."
            )
    # Line patterns turn 180 deg between adjacent lines one gap apart; the
    # widest such turn is a semicircle of radius gap/2.
    zigzag_layers = config.infill_type in (ORIENTED_SERPENTINE, DIAGONAL_45)
    keep_turns = config.zigzag_respect_radius or config.strict_bend_radius
    if zigzag_layers and min_radius > 0 and track_gap < 2.0 * min_radius and keep_turns:
        step = max(1, math.ceil(2.0 * min_radius / track_gap - 1e-9))
        notes.append(
            f"{config.infill_type}: turns kept at the {min_radius:.1f} mm min bend radius: lines "
            f"{step * track_gap:.2f} mm apart (every {step}th line), each layer at the same angle shifted "
            f"{track_gap:.2f} mm, so {step} such layers fill the area once."
        )
    elif zigzag_layers and min_radius > 0 and track_gap < 2.0 * min_radius:
        notes.append(
            f"{config.infill_type}: U-turns between lines {track_gap:.2f} mm apart are at most "
            f"{track_gap / 2.0:.2f} mm radius, below the {min_radius:.1f} mm min bend radius, so they "
            f"are speed-limited (curvature warning). A spiral pattern, or Track Gap >= "
            f"{2.0 * min_radius:.1f} mm, avoids this."
        )
    if kin_update:
        update["kinematics"] = config.kinematics.model_copy(update=kin_update)
    return ResolvedConfig(config.model_copy(update=update), tuple(notes))
