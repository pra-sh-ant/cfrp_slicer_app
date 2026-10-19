"""Toolpath geometry: outlines, fillets, and the infill path generators.
All lengths in mm; paths are lists of (x, y) tuples."""
from ._common import (
    arc_along_ring,
    audit_min_radius,
    blend_chain,
    check_gap_radius,
    dedupe_path,
    enforce_min_radius,
    fillet,
    hole_keepouts,
    inset_components,
    largest_polygon,
    resample_closed_ring,
)
from .archimedean import archimedean_path
from .corner_step import corner_step_path, is_rectangle, stitch_rings
from .fermat import fermat_spiral_path
from .laminate import laminate_layer, max_track_count, ring_from, wall_seam
from .mesh_slicer import load_stl, mesh_bounds, slice_mesh
from .outlines import base_polygon, printable_boundary
from .revolve import revolve_cross_section, revolve_radius_profile
from .serpentine import oriented_serpentine, zigzag_fits

__all__ = [
    "arc_along_ring", "archimedean_path", "audit_min_radius", "base_polygon", "blend_chain", "check_gap_radius",
    "corner_step_path", "dedupe_path", "enforce_min_radius", "fermat_spiral_path", "fillet",
    "hole_keepouts", "inset_components", "is_rectangle", "laminate_layer", "largest_polygon", "load_stl", "max_track_count",
    "mesh_bounds", "oriented_serpentine", "printable_boundary", "resample_closed_ring",
    "revolve_cross_section", "revolve_radius_profile", "ring_from", "slice_mesh", "stitch_rings",
    "wall_seam", "zigzag_fits",
]
