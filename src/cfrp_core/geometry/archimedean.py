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

    The first and last ring are each also traced as one full closed lap, so
    the outermost wall is reached in both spiral directions and alternating
    layers stack into a straight wall.

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

    # Shared sample count per revolution: ~one point per 0.75 mm on the
    # longest ring, at least 120.
    max_length = max(poly.exterior.length for poly in ordered)
    samples = max(120, int(max_length / 0.75))

    resampled = []
    prev_start = None
    for poly in ordered:
        pts = resample_closed_ring(poly, samples, prev_start)
        resampled.append(pts)
        prev_start = pts[0]

    # first_lap traces the outermost ring as an explicit closed loop so the
    # shape boundary is always fully printed (important for shape integrity).
    # blend_chain then starts from ring1 (NOT ring0): ring0 is already done by
    # first_lap, so starting from ring0 again would double-deposit it.
    # The step from first_lap[-1] (ring0[0]) to ring1[0] is a short radial
    # move of one track_gap — the natural seam between perimeter and infill.
    first_lap = resampled[0] + [resampled[0][0]]
    inner_close = resampled[-1][1:] + [resampled[-1][0]]

    if len(resampled) == 1:
        continuous_path = first_lap
    else:
        continuous_path = first_lap + blend_chain(resampled[1:], samples) + inner_close

    path = dedupe_path(continuous_path)

    # Close the loop when end lands near start (tight shapes, 1-2 tracks).
    if len(path) >= 2:
        d = math.hypot(path[-1][0] - path[0][0], path[-1][1] - path[0][1])
        if d < track_gap:
            path = path + [path[0]]

    return path, base_poly
