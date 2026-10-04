"""Toolpath geometry: outlines, fillets, and the infill path generators.
All lengths in mm; paths are lists of (x, y) tuples."""
from ._common import (
    blend_chain,
    check_gap_radius,
    dedupe_path,
    fillet,
    hole_keepouts,
    inset_components,
    largest_polygon,
    resample_closed_ring,
)
from .archimedean import archimedean_path
from .fermat import fermat_spiral_path
from .laminate import laminate_layer
from .outlines import base_polygon, printable_boundary, serpentine_print_extent
from .revolve import revolve_cross_section, revolve_radius_profile
from .serpentine import oriented_serpentine, serpentine_infill

__all__ = [
    "archimedean_path", "base_polygon", "blend_chain", "check_gap_radius", "dedupe_path",
    "fermat_spiral_path", "fillet", "hole_keepouts", "inset_components", "laminate_layer",
    "largest_polygon", "oriented_serpentine", "printable_boundary", "resample_closed_ring",
    "revolve_cross_section", "revolve_radius_profile", "serpentine_infill",
    "serpentine_print_extent",
]
