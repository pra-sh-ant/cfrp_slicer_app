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
    resample_polar_ring,
)


def archimedean_path(base_poly, track_gap, min_radius, track_count=None, start_from_inside=False,
                     seam_hint=None, polar_center=None, outer_start=None):
    """One smooth, continuously-blended spiral inside an arbitrary polygon.

    Rings sit at inset d_k = gap/2 + k·gap from the outer shell. Instead of
    splicing ring-to-ring by jumping to the nearest point, each ring is
    resampled at matched arc-length parameters and consecutive rings are
    linearly blended across one full revolution: a constant pitch of `gap`
    with no direction reversals.

    For outside-in (default) the path opens with the full outer ring as a
    closed perimeter lap, then exits at 45° through a short (_PERIMETER_ENTRY_MM)
    diagonal segment that bridges to the second ring.  The spiral then
    continues inward through the remaining rings.  This gives:
        perimeter coverage + smooth diagonal entry + seamless inward spiral.

    For inside-out the last ring is the outermost; inner_close closes it at
    full radius so alternating layers maintain symmetric outer-wall coverage.

    `outer_start`: outside-in only, start the outer ring nearest this point
    (e.g. where a boundary wall loop hands over).
    `polar_center`: pair ring points by angle about this point instead of by
    arc length (rings must be star-shaped about it; see resample_polar_ring).

    Returns (path, base_poly). Raises ValueError if `track_count` rings were
    requested but fewer fit.
    """
    check_gap_radius(track_gap, min_radius)
    if base_poly.is_empty:
        return [], base_poly
    base_poly = largest_polygon(base_poly)
    if polar_center is None:
        resample = resample_closed_ring
    else:
        def resample(poly, n, phase_start):
            return resample_polar_ring(poly, n, polar_center, phase_start)
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

    # Shared sample count: ~one point per 0.75 mm on the longest ring, ≥120.
    max_length = max(poly.exterior.length for poly in ordered)
    samples = max(120, int(max_length / 0.75))

    if len(ordered) == 1:
        ring = resample(ordered[0], samples, None)
        path = dedupe_path(ring + [ring[0]])
        return path, base_poly

    # --- Resample ring0 first (no phase anchor) ---
    ring0_pts = resample(ordered[0], samples, None if start_from_inside else outer_start)

    if not start_from_inside:
        # ----------------------------------------------------------------
        # Outside-in: full outer ring + smooth blended spiral inward
        # ----------------------------------------------------------------
        # Phase all rings to start near ring0's seam.
        resampled = [ring0_pts]
        prev_start = ring0_pts[0]
        for poly in ordered[1:]:
            pts = resample(poly, samples, prev_start)
            resampled.append(pts)
            prev_start = pts[0]

        # Full outer ring lap for perimeter coverage, then blend_chain
        # handles ALL ring-to-ring transitions (including ring0→ring1)
        # with the same smooth, curvature-continuous linear blending.
        first_lap = ring0_pts + [ring0_pts[0]]
        inner_close = resampled[-1][1:] + [resampled[-1][0]]
        continuous_path = first_lap + blend_chain(resampled, samples) + inner_close

    else:
        # ----------------------------------------------------------------
        # Inside-out: pure blend inner→outer + outer ring close
        # ----------------------------------------------------------------
        # Ring0 is innermost here.  If the caller supplies seam_hint (the
        # previous layer's end-point on this same ring), anchor ring0 to start
        # near it so the layer-to-layer seam trim cuts go in opposite directions
        # on the innermost ring, giving a clean ramp with good XY separation.
        if seam_hint is not None:
            ring0_pts = resample(ordered[0], samples, seam_hint)
        resampled = [ring0_pts]
        prev_start = ring0_pts[0]
        for poly in ordered[1:]:
            pts = resample(poly, samples, prev_start)
            resampled.append(pts)
            prev_start = pts[0]

        inner_close = resampled[-1][1:] + [resampled[-1][0]]
        continuous_path = blend_chain(resampled, samples) + inner_close

    path = dedupe_path(continuous_path)

    # Close the loop when end lands near start (tight shapes, 1-2 tracks).
    if len(path) >= 2:
        d = math.hypot(path[-1][0] - path[0][0], path[-1][1] - path[0][1])
        if d < track_gap:
            path = path + [path[0]]

    return path, base_poly
