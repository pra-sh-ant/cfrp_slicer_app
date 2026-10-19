"""Structural PCB = continuous-fibre laminate of the board outline + DIW
electronics on top, sliced together so both land on the same spot.

The fibre laminate is one continuous stroke as always (Custom Outline with
the board's holes); DIW starts after it on the laminate's top surface, in
the same bed placement (start_x/y plus any auto-positioning shift).
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import ProcessConfig
from .diw.config import DiwConfig
from .diw.design import BoardDesign, hole_outlines
from .diw.slicer import DiwResult, slice_diw
from .machine import DEFAULT_MACHINE, MachineProfile, check_bounds
from .pipeline import SliceResult, slice_part

PRINT_MODES = ("structural", "fibre", "electronics")


@dataclass
class BoardResult:
    fibre: SliceResult | None     # None when printing the electronics only
    diw: DiwResult | None         # None when printing the fibre only
    gcode: str                    # one program for everything printed
    offset: tuple                 # board origin on the bed (mm)
    mode: str = "structural"


def board_process(design: BoardDesign, process: ProcessConfig) -> ProcessConfig:
    """`process` with the board as its Custom Outline part."""
    geom = process.geometry.model_copy(update=dict(
        shape="Custom Outline", custom_outline=tuple(map(tuple, design.outline)),
        custom_holes=tuple(tuple(map(tuple, h)) for h in hole_outlines(design))))
    return process.model_copy(update=dict(geometry=geom))


def combine_gcode(fibre_gcode: str, diw_gcode: str) -> str:
    """Fibre program without its park/motor-off lines, then the DIW program."""
    keep = [ln for ln in fibre_gcode.splitlines()
            if not ln.startswith("M84") and not ln.startswith("G0 X0 Y0")]
    return "\n".join(keep) + "\n\n" + diw_gcode


def slice_structural_board(design: BoardDesign, process: ProcessConfig, diw: DiwConfig,
                           machine: MachineProfile = DEFAULT_MACHINE, mode: str = "structural") -> BoardResult:
    """Slice a structural PCB. mode: "structural" = fibre laminate + ink on
    top; "fibre" = the laminate only; "electronics" = the ink only, on an
    existing surface `diw.surface_z_mm` high, board origin at Start X/Y."""
    if mode not in PRINT_MODES:
        raise ValueError(f"Print mode must be one of {', '.join(PRINT_MODES)} (got {mode!r}).")
    from .diw.gcode import compile_diw_gcode
    g = process.geometry
    if mode == "electronics":
        offset = (g.start_x_mm, g.start_y_mm)
        result = slice_diw(design, diw, z_base=float(diw.surface_z_mm), offset=offset)
        _check_bed(result, machine)
        return BoardResult(None, result, result.gcode, offset, mode)
    fibre = slice_part(board_process(design, process), machine)
    g = fibre.config.geometry
    offset = (g.start_x_mm + fibre.bed_shift[0], g.start_y_mm + fibre.bed_shift[1])
    if mode == "fibre":
        return BoardResult(fibre, None, fibre.gcode, offset, mode)
    top = max((p['z'] for p in fibre.trajectory), default=float(process.z_offset_mm))
    layers = max((p.get('layer', 1) for p in fibre.trajectory), default=0)
    result = slice_diw(design, diw, z_base=top, offset=offset, layer_offset=layers)
    _check_bed(result, machine)
    diw_gcode = compile_diw_gcode(result.trajectory, diw, design.name, standalone=False)
    return BoardResult(fibre, result, combine_gcode(fibre.gcode, diw_gcode), offset, mode)


def _check_bed(result: DiwResult, machine: MachineProfile):
    out_of_bed = check_bounds(result.trajectory, machine)
    if out_of_bed:
        v = dict(result.validation)
        v["messages"] = [f"Out-of-bounds (DIW): {m}" for m in out_of_bed] + list(v["messages"])
        v["status"] = "Fail"
        result.validation = v
