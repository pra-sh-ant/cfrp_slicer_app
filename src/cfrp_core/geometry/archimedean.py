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

_PERIMETER_ENTRY_MM = 3.0   # length of the 45° perimeter-to-infill bridge


def archimedean_path(base_poly, track_gap, min_radius, track_count=None, start_from_inside=False,
                     seam_hint=None):
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

    # --- Resample ring0 first (no phase anchor) ---
    ring0_pts = resample_closed_ring(ordered[0], samples, None)

    if not start_from_inside:
        # ----------------------------------------------------------------
        # Outside-in: full outer ring + 45° diagonal entry to ring1
        # ----------------------------------------------------------------
        # Compute a 45° direction at ring0's seam: bisector between the ring
        # tangent (forward travel direction) and the inward normal (toward
        # the centroid).  Phase ring1 to start at the point nearest to the
        # projected 45° endpoint so the diagonal lands exactly on ring1.
        p0 = ring0_pts[0]
        tang = (ring0_pts[1][0] - p0[0], ring0_pts[1][1] - p0[1])
        tlen = math.hypot(tang[0], tang[1])
        tang = (tang[0] / tlen, tang[1] / tlen) if tlen > 1e-9 else (1.0, 0.0)

        cx, cy = base_poly.centroid.x, base_poly.centroid.y
        inv = (cx - p0[0], cy - p0[1])
        ilen = math.hypot(inv[0], inv[1])
        inv = (inv[0] / ilen, inv[1] / ilen) if ilen > 1e-9 else (-tang[1], tang[0])

        diag = (tang[0] + inv[0], tang[1] + inv[1])
        dlen = math.hypot(diag[0], diag[1])
        diag = (diag[0] / dlen, diag[1] / dlen) if dlen > 1e-9 else inv

        entry_target = (p0[0] + _PERIMETER_ENTRY_MM * diag[0],
                        p0[1] + _PERIMETER_ENTRY_MM * diag[1])

        # Resample ring1 with entry_target as phase anchor, subsequent rings
        # chain off ring1's start as usual.
        resampled = [ring0_pts]
        prev_start = entry_target
        for poly in ordered[1:]:
            pts = resample_closed_ring(poly, samples, prev_start)
            resampled.append(pts)
            prev_start = pts[0]

        # Outer ring fully closed (first_lap)
        first_lap = ring0_pts + [ring0_pts[0]]

        # 45° entry segment: straight interpolation from ring0[0] to ring1[0].
        # ring1[0] is the point on ring1 nearest the projected 45° endpoint,
        # so this segment naturally runs at ~45° to the ring tangent.
        ring1_start = resampled[1][0]
        n_entry = max(4, int(_PERIMETER_ENTRY_MM / 0.75))
        entry_seg = [
            (p0[0] + (i / n_entry) * (ring1_start[0] - p0[0]),
             p0[1] + (i / n_entry) * (ring1_start[1] - p0[1]))
            for i in range(1, n_entry + 1)
        ]

        # blend_chain starts at resampled[1][0] = ring1_start = entry_seg[-1];
        # dedupe_path removes the duplicate at the junction.
        inner_close = resampled[-1][1:] + [resampled[-1][0]]
        if len(resampled) == 2:
            continuous_path = first_lap + entry_seg + inner_close
        else:
            continuous_path = first_lap + entry_seg + blend_chain(resampled[1:], samples) + inner_close

    else:
        # ----------------------------------------------------------------
        # Inside-out: pure blend inner→outer + outer ring close
        # ----------------------------------------------------------------
        # Ring0 is innermost here.  If the caller supplies seam_hint (the
        # previous layer's end-point on this same ring), anchor ring0 to start
        # near it so the layer-to-layer seam trim cuts go in opposite directions
        # on the innermost ring, giving a clean ramp with good XY separation.
        if seam_hint is not None:
            ring0_pts = resample_closed_ring(ordered[0], samples, seam_hint)
        resampled = [ring0_pts]
        prev_start = ring0_pts[0]
        for poly in ordered[1:]:
            pts = resample_closed_ring(poly, samples, prev_start)
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
