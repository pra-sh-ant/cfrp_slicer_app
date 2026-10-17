"""Pre-slice automatic adjustments (spec §4).

Hard errors are raised by `ProcessConfig` validation itself. This module
applies the *coupled* adjustments that are allowed to proceed with an info
note instead of failing.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .config import ARCHIMEDEAN, DIAGONAL_45, FERMAT, ORIENTED_SERPENTINE, SPECIALTY_SHAPES, ProcessConfig


@dataclass(frozen=True)
class ResolvedConfig:
    config: ProcessConfig
    notes: tuple[str, ...]


def resolve_process_constraints(config: ProcessConfig) -> ResolvedConfig:
    """Return the adjusted config plus one plain-language note per adjustment."""
    notes: list[str] = []
    min_radius = float(config.min_radius_mm)
    track_gap = float(config.track_gap_mm)
    k = config.kinematics
    max_speed = float(k.v_max_mm_s)
    shape = config.geometry.shape

    if shape not in SPECIALTY_SHAPES:
        narrow_dimension = min(float(config.geometry.width_mm), float(config.geometry.height_mm))
        cornering_window = narrow_dimension - (2.0 * min_radius) - track_gap
        if min_radius > track_gap / 2.0:
            notes.append(
                f"Radius clearance uses {2.0 * min_radius:.2f} mm across the "
                f"{narrow_dimension:.2f} mm narrow dimension; about "
                f"{cornering_window:.2f} mm remains for concentric tracks."
            )

    kin_update = {}
    minimum_speed = float(k.v_min_mm_s)
    if minimum_speed > max_speed:
        kin_update["v_min_mm_s"] = max_speed
        notes.append(f"Min Bend Speed limited to Print Speed (Max), {max_speed:.2f} mm/s.")

    pump_floor = float(k.pump_stall_floor_mm_s)
    if pump_floor > max_speed:
        kin_update["pump_stall_floor_mm_s"] = max_speed
        notes.append(f"Pump-Stall Floor limited to Print Speed (Max), {max_speed:.2f} mm/s-equiv.")

    update: dict = {"track_gap_mm": track_gap}

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

    # A Fermat spiral must U-turn between its two arms inside the innermost
    # region; in a revolve shell's narrow wall band that U-turn is tighter than
    # min bend radius (e.g. ~0.2 mm vs 3 mm). Archimedean never reverses.
    wall_band = int(update.get("tracks_per_layer", config.tracks_per_layer)) * track_gap
    if (config.geometry.is_revolve and config.infill_type == FERMAT
            and min_radius > 0 and wall_band < 2.0 * min_radius + track_gap):
        update["infill_type"] = ARCHIMEDEAN
        notes.append(
            f"Fermat spiral needs a U-turn that the {wall_band:.1f} mm wall band cannot fit at "
            f"{min_radius:.1f} mm min bend radius; using Archimedean (no reversals) instead."
        )
    # Line patterns turn 180 deg between adjacent lines one gap apart; the
    # widest such turn is a semicircle of radius gap/2.
    if (config.infill_type in (ORIENTED_SERPENTINE, DIAGONAL_45) and min_radius > 0
            and track_gap < 2.0 * min_radius):
        notes.append(
            f"{config.infill_type}: U-turns between lines {track_gap:.2f} mm apart are at most "
            f"{track_gap / 2.0:.2f} mm radius, below the {min_radius:.1f} mm min bend radius, so they "
            f"are speed-limited (curvature warning). A spiral pattern, or Track Gap >= "
            f"{2.0 * min_radius:.1f} mm, avoids this."
        )
    if kin_update:
        update["kinematics"] = k.model_copy(update=kin_update)
    return ResolvedConfig(config.model_copy(update=update), tuple(notes))
