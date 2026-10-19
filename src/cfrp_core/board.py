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


@dataclass
class BoardResult:
    fibre: SliceResult
    diw: DiwResult
    gcode: str                    # fibre then DIW, one file
    offset: tuple                 # board origin on the bed (mm)


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
                           machine: MachineProfile = DEFAULT_MACHINE) -> BoardResult:
    fibre = slice_part(board_process(design, process), machine)
    g = fibre.config.geometry
    offset = (g.start_x_mm + fibre.bed_shift[0], g.start_y_mm + fibre.bed_shift[1])
    top = max((p['z'] for p in fibre.trajectory), default=float(process.z_offset_mm))
    layers = max((p.get('layer', 1) for p in fibre.trajectory), default=0)
    result = slice_diw(design, diw, z_base=top, offset=offset, layer_offset=layers)
    out_of_bed = check_bounds(result.trajectory, machine)
    if out_of_bed:
        v = dict(result.validation)
        v["messages"] = [f"Out-of-bounds (DIW): {m}" for m in out_of_bed] + list(v["messages"])
        v["status"] = "Fail"
        result.validation = v
    from .diw.gcode import compile_diw_gcode
    diw_gcode = compile_diw_gcode(result.trajectory, diw, design.name, standalone=False)
    return BoardResult(fibre, result, combine_gcode(fibre.gcode, diw_gcode), offset)
