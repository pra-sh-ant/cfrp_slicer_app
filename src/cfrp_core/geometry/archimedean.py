"""Concentric (Archimedean) spiral infill (spec §5.4)."""
from __future__ import annotations

import math

import numpy as np

from ._common import (
    blend_chain,
    check_gap_radius,
    dedupe_path,
    hole_keepouts,
    inset_components,
    largest_polygon,
    notched_rings,
    resample_closed_ring,
    resample_polar_ring,
    ring_tail,
)

MAX_TWIST_PER_RING = 60.0 / 360.0     # laps of extra run along one ring (seam turn)


def archimedean_path(base_poly, track_gap, min_radius, track_count=None, start_from_inside=False,
                     seam_hint=None, polar_center=None, outer_start=None, lead_in=0.0, seam_turn=0.0):
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
    `seam_turn` (laps): end the blended spiral that much further round than
    it would (see _twisted_chain; at most MAX_TWIST_PER_RING per ring). As
    each layer starts where the last ended, consecutive layers' seams are
    staggered.

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

    if holes and (target_count is None or len(ring_polys) < target_count):
        # A hole stopped the rings early (inset_components ends the spiral at
        # the first ring that would come near one). Rings that bend round
        # the holes cover the part instead; they differ in shape ring to
        # ring, so they are joined by lane changes on one seam rather than
        # blended. Annuli, whose bore is reached by every ring at once, gain
        # nothing and keep the blended spiral.
        from .corner_step import RING_PTS, resample_ring_cw, stitch_rings
        notched = notched_rings(base_poly, track_gap, min_radius, max_rings=target_count)
        # Also when only the notched rings reach the requested count
        # (max_track_count promises that many, so refusing was a mismatch).
        if len(notched) > len(ring_polys) + 1 or (
                target_count is not None and len(ring_polys) < target_count <= len(notched)):
            if target_count is not None and len(notched) < target_count:
                raise ValueError(
                    f"Requested {target_count} tracks, but only {len(notched)} fit with "
                    f"a {track_gap:.2f} mm gap and {min_radius:.2f} mm bend radius."
                )
            rings = [resample_ring_cw(list(p.exterior.coords), max(RING_PTS, int(math.ceil(p.exterior.length / 0.5))))
                     for p in notched]
            hint = seam_hint if start_from_inside else outer_start
            return dedupe_path(stitch_rings(rings, track_gap, min_radius, outward=start_from_inside,
                                             seam_hint=hint, lead_in=lead_in if start_from_inside else 0.0,
                                             keepouts=holes)), base_poly

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
    # Seam turn: samples to follow each inner ring (rings 1 .. n-2) before
    # blending on.
    arcs = len(ordered) - 2
    step = (int(round(min(abs(seam_turn) / arcs, MAX_TWIST_PER_RING) * samples))
            if seam_turn and arcs > 0 else 0)

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
        if step:
            continuous_path = first_lap + _twisted_chain(ordered, resample, samples, ring0_pts, step)
        else:
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
        # Lead-in (first layer only): the innermost ring just before the
        # start; the first lap blends away from it, so nothing crosses it.
        lead = ring_tail(ring0_pts, 0, lead_in) if lead_in > 0 else []
        if step:
            continuous_path = lead + _twisted_chain(ordered, resample, samples, ring0_pts, step)
        else:
            continuous_path = lead + blend_chain(resampled, samples) + inner_close

    path = dedupe_path(continuous_path)

    # Close the loop when end lands near start (tight shapes, 1-2 tracks).
    if len(path) >= 2:
        d = math.hypot(path[-1][0] - path[0][0], path[-1][1] - path[0][1])
        if d < track_gap:
            path = path + [path[0]]

    return path, base_poly


def _twisted_chain(ordered, resample, samples, first, step):
    """Like blend_chain + the closing lap, but after arriving on each ring
    but the last the path follows that ring `step` samples further before
    blending on, so every ring pair starts further round and the spiral ends
    (step x rings) further round. Each blend stays phase-aligned, as in the
    plain spiral (shifting the next ring's start instead made the blend cut
    corners: crossings on rectangles and hexagons); the extra run lies on
    its ring, a gap from the revolutions either side."""
    u = np.linspace(0.0, 1.0, samples, endpoint=False).reshape(-1, 1)
    path = []
    a = np.asarray(first, dtype=np.float64)
    for k, poly in enumerate(ordered[1:], start=1):
        b = np.asarray(resample(poly, samples, (float(a[0][0]), float(a[0][1]))), dtype=np.float64)
        path += [(float(x), float(y)) for x, y in a[:samples] * (1.0 - u) + b[:samples] * u]
        if k < len(ordered) - 1:
            path += [(float(x), float(y)) for x, y in b[:step]]
            a = np.asarray(resample(poly, samples, (float(b[step][0]), float(b[step][1]))), dtype=np.float64)
        else:
            path += [(float(x), float(y)) for x, y in b] + [(float(b[0][0]), float(b[0][1]))]
    return path
