"""Physical machine/print-volume description shared by validation and the viewer."""
from dataclasses import dataclass


@dataclass(frozen=True)
class MachineConfig:
    bed_x: float = 180.0
    bed_y: float = 220.0
    z_max: float = 200.0


DEFAULT_MACHINE = MachineConfig()


def fit_trajectory_to_bed(trajectory, machine: MachineConfig = DEFAULT_MACHINE, margin=1.0):
    """Translate a complete path into the machine envelope without changing shape."""
    if not trajectory:
        return [], 0.0, 0.0

    min_x = min(pt["x"] for pt in trajectory)
    max_x = max(pt["x"] for pt in trajectory)
    min_y = min(pt["y"] for pt in trajectory)
    max_y = max(pt["y"] for pt in trajectory)
    usable_x = machine.bed_x - 2.0 * margin
    usable_y = machine.bed_y - 2.0 * margin
    if max_x - min_x > usable_x or max_y - min_y > usable_y:
        raise ValueError(
            f"Continuous path footprint is {max_x - min_x:.2f} x {max_y - min_y:.2f} mm, "
            f"but the safe bed area is only {usable_x:.2f} x {usable_y:.2f} mm. "
            "Reduce the part size or lead-in anchor length."
        )

    shift_x = margin - min_x if min_x < margin else (machine.bed_x - margin - max_x if max_x > machine.bed_x - margin else 0.0)
    shift_y = margin - min_y if min_y < margin else (machine.bed_y - margin - max_y if max_y > machine.bed_y - margin else 0.0)
    shifted = []
    for point in trajectory:
        moved = dict(point)
        moved["x"] += shift_x
        moved["y"] += shift_y
        shifted.append(moved)
    return shifted, shift_x, shift_y


def check_bounds(trajectory, machine: MachineConfig = DEFAULT_MACHINE):
    """Return a list of human-readable violation messages for any trajectory
    point that falls outside the machine's print volume. Empty list = OK."""
    if not trajectory:
        return []

    min_x = min_y = min_z = float("inf")
    max_x = max_y = max_z = float("-inf")
    for pt in trajectory:
        min_x, max_x = min(min_x, pt["x"]), max(max_x, pt["x"])
        min_y, max_y = min(min_y, pt["y"]), max(max_y, pt["y"])
        min_z, max_z = min(min_z, pt["z"]), max(max_z, pt["z"])

    violations = []
    if min_x < 0 or max_x > machine.bed_x:
        violations.append(
            f"Toolpath X range [{min_x:.2f}, {max_x:.2f}] mm exceeds the bed's "
            f"0-{machine.bed_x:.0f} mm X travel."
        )
    if min_y < 0 or max_y > machine.bed_y:
        violations.append(
            f"Toolpath Y range [{min_y:.2f}, {max_y:.2f}] mm exceeds the bed's "
            f"0-{machine.bed_y:.0f} mm Y travel."
        )
    if min_z < 0 or max_z > machine.z_max:
        violations.append(
            f"Toolpath Z range [{min_z:.2f}, {max_z:.2f}] mm exceeds the "
            f"0-{machine.z_max:.0f} mm Z travel."
        )
    return violations
