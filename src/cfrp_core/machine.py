"""Machine profile (print volume) and fitting a path onto the bed."""
from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class MachineProfile(BaseModel):
    """Physical print volume, mm. Origin at the homed corner (0, 0, 0)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = "default"
    bed_x_mm: float = Field(180.0, gt=0)
    bed_y_mm: float = Field(220.0, gt=0)
    z_max_mm: float = Field(200.0, gt=0)
    # Safety margin kept clear on every side when auto-positioning a path.
    margin_mm: float = Field(1.0, ge=0)


def load_machine_profile(path: str | Path | None = None) -> MachineProfile:
    """Load a profile from a JSON file; None loads the bundled default."""
    if path is None:
        text = resources.files("cfrp_core").joinpath("data/machine_profiles/default.json").read_text(encoding="utf-8")
    else:
        text = Path(path).read_text(encoding="utf-8")
    return MachineProfile.model_validate(json.loads(text))


DEFAULT_MACHINE = load_machine_profile()


def fit_trajectory_to_bed(trajectory, machine: MachineProfile = DEFAULT_MACHINE):
    """Translate a complete path into the machine envelope without changing
    shape. Returns (shifted_trajectory, shift_x, shift_y). Raises ValueError
    if the footprint can't fit inside the bed minus the margin."""
    if not trajectory:
        return [], 0.0, 0.0

    margin = machine.margin_mm
    min_x = min(pt["x"] for pt in trajectory)
    max_x = max(pt["x"] for pt in trajectory)
    min_y = min(pt["y"] for pt in trajectory)
    max_y = max(pt["y"] for pt in trajectory)
    usable_x = machine.bed_x_mm - 2.0 * margin
    usable_y = machine.bed_y_mm - 2.0 * margin
    if max_x - min_x > usable_x or max_y - min_y > usable_y:
        raise ValueError(
            f"Continuous path footprint is {max_x - min_x:.2f} x {max_y - min_y:.2f} mm, "
            f"but the safe bed area is only {usable_x:.2f} x {usable_y:.2f} mm. "
            "Reduce the part size or lead-in anchor length."
        )

    shift_x = margin - min_x if min_x < margin else (machine.bed_x_mm - margin - max_x if max_x > machine.bed_x_mm - margin else 0.0)
    shift_y = margin - min_y if min_y < margin else (machine.bed_y_mm - margin - max_y if max_y > machine.bed_y_mm - margin else 0.0)
    shifted = []
    for point in trajectory:
        moved = dict(point)
        moved["x"] += shift_x
        moved["y"] += shift_y
        shifted.append(moved)
    return shifted, shift_x, shift_y


def check_bounds(trajectory, machine: MachineProfile = DEFAULT_MACHINE) -> list[str]:
    """Human-readable violation messages for any point outside the print
    volume. Empty list = OK."""
    if not trajectory:
        return []

    min_x = min_y = min_z = float("inf")
    max_x = max_y = max_z = float("-inf")
    for pt in trajectory:
        min_x, max_x = min(min_x, pt["x"]), max(max_x, pt["x"])
        min_y, max_y = min(min_y, pt["y"]), max(max_y, pt["y"])
        min_z, max_z = min(min_z, pt["z"]), max(max_z, pt["z"])

    violations = []
    if min_x < 0 or max_x > machine.bed_x_mm:
        violations.append(
            f"Toolpath X range [{min_x:.2f}, {max_x:.2f}] mm exceeds the bed's "
            f"0-{machine.bed_x_mm:.0f} mm X travel."
        )
    if min_y < 0 or max_y > machine.bed_y_mm:
        violations.append(
            f"Toolpath Y range [{min_y:.2f}, {max_y:.2f}] mm exceeds the bed's "
            f"0-{machine.bed_y_mm:.0f} mm Y travel."
        )
    if min_z < 0 or max_z > machine.z_max_mm:
        violations.append(
            f"Toolpath Z range [{min_z:.2f}, {max_z:.2f}] mm exceeds the "
            f"0-{machine.z_max_mm:.0f} mm Z travel."
        )
    return violations
