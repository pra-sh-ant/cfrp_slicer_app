"""Pre-slice automatic adjustments (spec §4).

Hard errors are raised by `ProcessConfig` validation itself. This module
applies the *coupled* adjustments that are allowed to proceed with an info
note instead of failing.
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import SPECIALTY_SHAPES, ProcessConfig


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

    if shape == "Serpentine Infill":
        required_gap = 2.0 * min_radius
        if track_gap < required_gap:
            notes.append(
                f"Track Gap raised from {track_gap:.2f} to {required_gap:.2f} mm: "
                "a serpentine U-turn needs at least 2R of vertical room."
            )
            track_gap = required_gap

    if shape not in (SPECIALTY_SHAPES | {"Serpentine Infill"}):
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
    if kin_update:
        update["kinematics"] = k.model_copy(update=kin_update)
    return ResolvedConfig(config.model_copy(update=update), tuple(notes))
