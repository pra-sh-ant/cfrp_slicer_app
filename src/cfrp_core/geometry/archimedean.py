"""Concentric (Archimedean) spiral infill (spec §5.4)."""
from __future__ import annotations

import math

from ._common import (
    blend_chain,
    check_gap_radius,
    dedupe_path,
    hole_keepouts,
    inset_components,
    largest_polygon,
    resample_closed_ring,
)


def archimedean_path(base_poly, track_gap, min_radius, track_count=None, start_from_inside=False):
    """One smooth, continuously-blended spiral inside an arbitrary polygon.

    Rings sit at inset d_k = gap/2 + k·gap from the outer shell. Instead of
    splicing ring-to-ring by jumping to the nearest point, each ring is
    resampled at matched arc-length parameters and consecutive rings are
    linearly blended across one full revolution: a constant pitch of `gap`
    with no direction reversals.

    Returns (path, base_poly). Raises ValueError if `track_count` rings were
    requested but fewer fit.
    """
    check_gap_radius(track_gap, min_radius)
    if base_poly.is_empty:
        return [], base_poly
    base_poly = largest_polygon(base_poly)
    holes = hole_keepouts(base_poly)

    ring_polys, current_inset = [], track_gap / 2.0
    target_count = int(track_count) if track_count is not None else None
    while target_count is None or len(ring_polys) < target_count:
        comps = inset_components(base_poly, current_inset, min_radius, holes, track_gap / 2.0)
        if comps is None:
            break
        ring_polys.append(max(comps, key=lambda g: g.area))
        current_inset += track_gap

    if not ring_polys:
        return [], base_poly

    if target_count is not None and len(ring_polys) < target_count:
        raise ValueError(
            f"Requested {target_count} tracks, but only {len(ring_polys)} fit with "
            f"a {track_gap:.2f} mm gap and {min_radius:.2f} mm bend radius."
        )

    ordered = list(reversed(ring_polys)) if start_from_inside else ring_polys

    if len(ordered) == 1:
        return list(ordered[0].exterior.coords), base_poly

    # Shared sample count: ~one point per 0.75 mm on the longest ring, ≥120.
    max_length = max(poly.exterior.length for poly in ordered)
    samples = max(120, int(max_length / 0.75))

    resampled = []
    prev_start = None
    for poly in ordered:
        pts = resample_closed_ring(poly, samples, prev_start)
        resampled.append(pts)
        prev_start = pts[0]

    # Pure-blend spiral: blend_chain transitions smoothly from ring0 (outermost
    # for outside-in, innermost for inside-out) continuously through all rings
    # with no abrupt radial seam step and no double-trace of the outer wall.
    # The last ring is explicitly closed so its seam is clean.  For inside-out
    # the outermost ring IS the last ring, so inner_close fully traces it at its
    # exact radius — giving symmetric wall coverage in both layer directions.
    inner_close = resampled[-1][1:] + [resampled[-1][0]]
    continuous_path = blend_chain(resampled, samples) + inner_close

    path = dedupe_path(continuous_path)

    # Close the loop when end lands near start (tight shapes, 1-2 tracks).
    if len(path) >= 2:
        d = math.hypot(path[-1][0] - path[0][0], path[-1][1] - path[0][1])
        if d < track_gap:
            path = path + [path[0]]

    return path, base_poly
