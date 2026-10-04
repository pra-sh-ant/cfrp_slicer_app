"""Concentric (Archimedean) spiral infill (spec §5.4)."""
from __future__ import annotations

import math

from ._common import (
    check_gap_radius,
    dedupe_path,
    hole_keepouts,
    inset_components,
    largest_polygon,
    resample_closed_ring,
)

_CONNECTOR_STEP_MM = 0.75   # max segment length inside ring-to-ring connectors


def _left_most(ring_poly):
    """Return the (x, y) point with minimum X (then minimum Y) on a ring."""
    coords = list(ring_poly.exterior.coords)[:-1]
    return min(coords, key=lambda p: (p[0], p[1]))


def archimedean_path(base_poly, track_gap, min_radius, track_count=None, start_from_inside=False,
                     seam_hint=None):
    """Concentric ring infill with left-side seam column.

    Each ring is traversed from its left-most point (min X, then min Y) almost
    all the way around, ending one sample step before the seam.  A short
    straight connector then steps inward to the left-most seam of the next
    ring.  Because every ring's seam is on the left edge, all connectors form a
    single vertical column on the left side of the part — matching the anchor
    direction (−X, approaching from the left).

    Seam selection:
      • For outside-in (default): left-most point of the outermost ring.
      • For inside-out: left-most point of the innermost ring, or `seam_hint`
        when supplied (aligns with the previous outside-in layer's seam).

    Returns (path, base_poly).  Raises ValueError if `track_count` rings were
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
    samples = max(120, int(max_length / _CONNECTOR_STEP_MM))

    # ----------------------------------------------------------------
    # Seam anchor: the Y-column reference point.
    # For outside-in → bottom-most point of outermost ring.
    # For inside-out → seam_hint (previous layer's end) or bottom-most of innermost.
    # ----------------------------------------------------------------
    if seam_hint is not None:
        initial_seam = seam_hint
    else:
        initial_seam = _left_most(ordered[0])

    path = []
    prev_seam = initial_seam

    for ring_poly in ordered:
        # Phase this ring to start nearest to prev_seam (the current Y-column point).
        ring_pts = resample_closed_ring(ring_poly, samples, prev_seam)
        ring_seam = ring_pts[0]

        if path:
            # Short straight connector from the last traversal point of the previous ring
            # (one step before its left-most seam) to the left-most seam of this ring.
            # For convex shapes this step is nearly horizontal (+X, inward).
            seg_len = math.hypot(ring_seam[0] - prev_seam[0], ring_seam[1] - prev_seam[1])
            n_conn = max(2, int(math.ceil(seg_len / _CONNECTOR_STEP_MM)))
            for i in range(1, n_conn + 1):
                t = i / n_conn
                path.append((
                    prev_seam[0] + t * (ring_seam[0] - prev_seam[0]),
                    prev_seam[1] + t * (ring_seam[1] - prev_seam[1]),
                ))

        # Traverse the ring.  resample_closed_ring returns `samples` points covering
        # one full revolution starting at the left-most seam, ending one step before
        # it.  prev_seam is updated to ring_pts[-1] so the next connector starts from
        # the natural end of this traversal — no jump back to the seam point.
        path.extend(ring_pts)
        prev_seam = ring_pts[-1]  # last traversal point, one step before the seam

    return dedupe_path(path), base_poly
