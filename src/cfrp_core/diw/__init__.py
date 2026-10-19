"""DIW (direct ink writing) slicing for structural electronics: conductive
traces, pads and dielectric printed on a continuous-fibre laminate.

    from cfrp_core.diw import BoardDesign, DiwConfig, slice_diw, example_board
"""
from .config import DiwConfig, DiwLayerSpec, build_diw_config
from .design import (
    FOOTPRINTS,
    BoardDesign,
    Hole,
    Pad,
    Trace,
    Zone,
    board_polygon,
    example_board,
    footprint,
    hole_outlines,
    interdigitated,
    load_design,
    meander,
    save_design,
    spiral_coil,
)
from .slicer import DiwResult, default_stack, layer_stack, slice_diw
from .validation import drc, trace_resistance

__all__ = [
    "FOOTPRINTS", "BoardDesign", "DiwConfig", "DiwLayerSpec", "DiwResult", "Hole", "Pad", "Trace", "Zone",
    "board_polygon", "build_diw_config", "default_stack", "drc", "example_board", "footprint", "hole_outlines",
    "interdigitated", "layer_stack", "load_design", "meander", "save_design", "slice_diw", "spiral_coil",
    "trace_resistance",
]
