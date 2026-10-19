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

PRINT_MODES = ("structural", "fibre", "electronics", "embedded")


@dataclass
class BoardResult:
    fibre: SliceResult | None     # None when printing the electronics only
    diw: DiwResult | None         # None when printing the fibre only
    gcode: str                    # one program for everything printed
    offset: tuple                 # board origin on the bed (mm)
    mode: str = "structural"
    fibre_upper: SliceResult | None = None   # embedded: the laminate laid over the electronics
    notes: tuple = ()


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
    if mode == "embedded":
        return _embedded(design, process, diw, machine)
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


def _resume_gcode(fibre_gcode: str, safe_z: float) -> str:
    """A fibre program resumed mid-print: no homing (the part is on the bed),
    a safe lift above the part instead of the fixed one, and a pause to
    re-thread the tow."""
    out = ["; --- RESUME FIBRE (upper laminate) ---",
           "M0 Mount the fibre head and re-thread the tow, then resume ; PAUSE"]
    for ln in fibre_gcode.splitlines():
        if ln.startswith("G28"):
            continue
        if "Safe Z lift" in ln:
            ln = f"G0 F1200 Z{safe_z:.3f} ; Safe Z lift (above the part)"
        out.append(ln)
    return "\n".join(out) + "\n"


def _embedded(design, process, diw, machine):
    """Lower laminate -> electronics (insulated below and above) -> upper
    laminate as a second fibre stroke, with windows over the pads."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    from .diw.design import hole_shape, pad_shape
    from .diw.gcode import compile_diw_gcode
    n = int(process.layers)
    if n < 2:
        raise ValueError("Embedded electronics need at least 2 fibre layers (some below, some above). "
                         "Raise Total Layers.")
    k = int(diw.embed_after_layer) or n // 2
    k = min(max(1, k), n - 1)
    notes = [f"Electronics embedded after fibre layer {k} of {n}; the fibre is cut there and a second "
             f"stroke lays layers {k + 1}-{n} over them."]
    if not diw.encapsulate:
        diw = diw.model_copy(update=dict(encapsulate=True))
        notes.append("Covering (encapsulation) turned on: the upper carbon layers would short bare ink.")
    base = board_process(design, process)
    lower = slice_part(base.model_copy(update=dict(layers=k)), machine)
    g = lower.config.geometry
    offset = (g.start_x_mm + lower.bed_shift[0], g.start_y_mm + lower.bed_shift[1])
    top1 = max(p['z'] for p in lower.trajectory)
    result = slice_diw(design, diw, z_base=top1, offset=offset, layer_offset=k)
    _check_bed(result, machine)
    ink_top = max((p['z'] for p in result.trajectory if p['extruding']), default=top1)
    # Upper laminate: the board with a window over every pad (and the holes).
    board = Polygon(design.outline).buffer(0)
    cut = unary_union([hole_shape(h) for h in design.holes] + [pad_shape(p).buffer(1.0) for p in design.pads])
    cut = cut.intersection(board.buffer(-0.5))
    holes = [list(c.exterior.coords)[:-1] for c in getattr(cut, 'geoms', [cut])
             if c.geom_type == 'Polygon' and not c.is_empty]
    layup = tuple(process.layup_deg)
    rot = layup[k % len(layup):] + layup[:k % len(layup)]
    upper_geom = base.geometry.model_copy(update=dict(
        custom_holes=tuple(tuple(map(tuple, h)) for h in holes),
        start_x_mm=offset[0], start_y_mm=offset[1]))
    upper_cfg = base.model_copy(update=dict(
        geometry=upper_geom, layers=n - k, layup_deg=rot,
        z_offset_mm=ink_top + float(process.layer_height_mm), anchor_length_mm=0.0, first_layer_solid=False,
        fill_solid=True))
    upper = slice_part(upper_cfg, machine)
    if not process.fill_solid:
        notes.append("The upper laminate is filled solid (as many tracks as fit) so it covers the "
                     "electronics; only the pads stay open.")
    if upper.bed_shift != (0.0, 0.0):
        notes.append("Warning: the upper laminate had to be moved to fit the bed; it may not line up.")
    n_diw = len(result.layers)
    for p in upper.trajectory:
        p['layer'] = p.get('layer', 1) + k + n_diw
    diw_g = compile_diw_gcode(result.trajectory, diw, design.name, standalone=False)
    gcode = combine_gcode(lower.gcode, diw_g)
    gcode = "\n".join(ln for ln in gcode.splitlines()
                      if not ln.startswith("M84") and not ln.startswith("G0 X0 Y0")) + "\n"
    gcode += "\n" + _resume_gcode(upper.gcode, ink_top + 15.0)
    return BoardResult(lower, result, gcode, offset, "embedded", fibre_upper=upper, notes=tuple(notes))
