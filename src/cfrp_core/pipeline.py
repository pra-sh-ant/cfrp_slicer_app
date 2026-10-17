"""End-to-end slice: resolve constraints -> build layers -> fit to bed ->
G-code -> validation (spec §2)."""
from __future__ import annotations

from dataclasses import dataclass

import shapely
from shapely.affinity import translate

from .config import ProcessConfig
from .constraints import resolve_process_constraints
from .coverage import coverage_summary, layer_coverage
from .gcode import compile_gcode
from .layers import build_flat_laminate, build_revolve_shell, build_stl_laminate
from .machine import DEFAULT_MACHINE, MachineProfile, fit_trajectory_to_bed
from .specialty import specialty_shape
from .validation import validate_trajectory


def _compute_stats(trajectory: list[dict], config: ProcessConfig, target_shapes=()) -> dict:
    """Print time (s), fiber length (mm), fiber mass (g) from trajectory,
    plus area-weighted fibre coverage / overlap when layer outlines exist."""
    print_time = 0.0
    fiber_length = 0.0
    for pt in trajectory:
        if pt.get("is_transition"):
            continue
        dist = float(pt.get("dist", 0.0))
        v = float(pt.get("v", 1.0))
        fiber_length += dist
        if v > 0:
            print_time += dist / v
    fiber_mass = fiber_length * float(config.material.fiber_weight_g_per_mm)
    stats = {
        "print_time_s": print_time,
        "fiber_length_mm": fiber_length,
        "fiber_mass_g": fiber_mass,
    }
    if target_shapes:
        # keys: coverage, overlap, worst_layer, worst_coverage (fractions 0..1).
        # Informational only: a geometry failure here must not fail the slice.
        try:
            stats.update(coverage_summary(layer_coverage(trajectory, target_shapes, config.track_gap_mm)))
        except shapely.errors.GEOSException:
            pass
    return stats


@dataclass
class SliceResult:
    config: ProcessConfig                 # the resolved config actually sliced
    trajectory: list[dict]
    target_shapes: list[tuple]            # (z, shapely polygon, layer)
    gcode: str
    validation: dict                      # {"status", "messages"}
    max_z: float
    constraint_notes: tuple[str, ...]     # automatic adjustments (spec §4)
    info: tuple[str, ...]                 # other notes (e.g. bed auto-positioning)
    stats: dict = None                    # print_time_s, fiber_length_mm, fiber_mass_g


def slice_part(config: ProcessConfig, machine: MachineProfile = DEFAULT_MACHINE) -> SliceResult:
    """Slice one part. Raises ValueError (plain language) for input that
    can't be sliced, e.g. a footprint larger than the bed."""
    resolved = resolve_process_constraints(config)
    cfg = resolved.config
    g = cfg.geometry

    if g.shape == "STL Import":
        trajectory, target_shapes, max_z = build_stl_laminate(cfg)
    elif g.is_revolve:
        trajectory, target_shapes, max_z = build_revolve_shell(cfg)
    elif g.is_specialty:
        trajectory, target_shapes, max_z = specialty_shape(cfg), [], None
    else:
        trajectory, target_shapes, max_z = build_flat_laminate(cfg)

    info = []
    trajectory, shift_x, shift_y = fit_trajectory_to_bed(trajectory, machine)
    if shift_x or shift_y:
        info.append(f"Path auto-positioned for the homed machine envelope: X {shift_x:+.2f} mm, Y {shift_y:+.2f} mm.")
        target_shapes = [
            (z, translate(shape, xoff=shift_x, yoff=shift_y), layer)
            for z, shape, layer in target_shapes
        ]
    gcode = compile_gcode(trajectory, cfg)
    validation = validate_trajectory(trajectory, cfg, machine)

    if max_z is None:  # specialty: the path's own top
        max_z = max((pt['z'] for pt in trajectory), default=float(cfg.z_offset_mm))

    return SliceResult(
        config=cfg, trajectory=trajectory, target_shapes=target_shapes, gcode=gcode,
        validation=validation, max_z=max_z, constraint_notes=resolved.notes, info=tuple(info),
        stats=_compute_stats(trajectory, cfg, target_shapes),
    )
