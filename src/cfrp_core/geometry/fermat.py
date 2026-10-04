"""Fermat "connected double spiral" infill (spec §5.5)."""
from __future__ import annotations

from shapely.geometry import Point

from ._common import (
    blend_chain,
    check_gap_radius,
    dedupe_path,
    hole_keepouts,
    inset_components,
    resample_closed_ring,
)

_ARC_STEP_MM = 0.75   # sample spacing when tracing along a ring boundary


def _arc_along_ring(ring_poly, p_from, p_to):
    """Points along the SHORTER arc of ring_poly.exterior connecting the
    projections of p_from and p_to onto that exterior.

    Used to bridge between child sub-regions without crossing the already-
    printed ring: instead of cutting across the ring in a straight line, the
    connector retraces the ring boundary (acceptable double-deposition).
    """
    ext = ring_poly.exterior
    length = ext.length
    if length < 1e-9:
        return [p_from, p_to]

    t_from = ext.project(Point(p_from))
    t_to = ext.project(Point(p_to))

    fwd = (t_to - t_from) % length
    bck = length - fwd

    if fwd <= bck:
        arc_len, direction = fwd, 1.0
    else:
        arc_len, direction = bck, -1.0

    n = max(2, int(arc_len / _ARC_STEP_MM))
    pts = []
    for i in range(n + 1):
        t = (t_from + direction * arc_len * i / n) % length
        pt = ext.interpolate(t)
        pts.append((pt.x, pt.y))
    return pts


def _reverse_winding(ring_pts):
    """Reverse a resampled ring's own point traversal while keeping its
    anchor (index 0) fixed. Reversing ring order alone keeps the return
    pass winding the same way as the entry pass, so their radius bands
    cross nearly every lap; reversing each ring's point order too flips the
    rotational sense, keeping the two arms a constant gap apart like the
    two threads of a screw."""
    return [ring_pts[0]] + list(reversed(ring_pts[1:]))


def fermat_spiral_path(base_poly, track_gap, min_radius, track_count=None,
                       start_from_inside=False, entry_hint=None):
    """One continuous "connected Fermat spiral" covering base_poly --
    including holes, and shapes that split into islands as they're offset.

    Each region is filled with two interleaved spirals grown in lockstep:
        chain A insets: gap/2 + 2k·gap      (inward pass)
        chain B insets: 1.5·gap + 2k·gap    (return pass, ring order AND
                                             per-ring winding reversed)
    Same pitch, constant half-pitch offset: at any angle each pass reaches
    its own unique radii, so the passes never cross. Splits become child
    regions, visited depth-first, nearest first.

    `track_count` is a soft global cap on tracks across the whole branch
    tree (2 per ring pair); unlike the Archimedean spiral this never raises.

    `entry_hint` steers where on the outer ring the spiral starts/ends.
    Pass the diametrically-opposite point of the previous layer's end to
    phase-shift the seam ~180°, giving the Z-ramp a long horizontal run.
    """
    check_gap_radius(track_gap, min_radius)
    if base_poly.is_empty or base_poly.geom_type != 'Polygon':
        return []

    holes = hole_keepouts(base_poly)
    remaining = [int(track_count)] if track_count is not None else [None]

    def _next_ring(poly, inset):
        """One offset+fillet step. Returns None (vanished), a list of
        >=2 Polygons (split), or a single-element list (clean ring)."""
        return inset_components(poly, inset, min_radius, holes, track_gap / 2.0)

    def build_chains(poly):
        """Grow chains A and B in lockstep. Stops -- for both together --
        at the first depth where either vanishes or splits; whichever did
        is where `children` comes from. Returns (a_rings, b_rings, children)."""
        a_rings, b_rings = [], []
        inset_a, inset_b = track_gap / 2.0, 1.5 * track_gap
        while True:
            if remaining[0] is not None and remaining[0] <= 1:
                return a_rings, b_rings, []
            comps_a = _next_ring(poly, inset_a)
            if comps_a is None or len(comps_a) > 1:
                return a_rings, b_rings, (comps_a or [])
            comps_b = _next_ring(poly, inset_b)
            if comps_b is None or len(comps_b) > 1:
                return a_rings, b_rings, (comps_b or [])
            a_rings.append(comps_a[0])
            b_rings.append(comps_b[0])
            if remaining[0] is not None:
                remaining[0] -= 2
            inset_a += 2.0 * track_gap
            inset_b += 2.0 * track_gap

    def visit_children(children, near_point, innermost_ring=None):
        """DFS the split-off branches nearest-first, each fully in and back
        out, chained end-to-start so the walk stays one continuous stroke.

        `innermost_ring` is the deepest printed A-ring of the parent region.
        When set, transitions between children are routed ALONG its exterior
        rather than in a straight line, preventing the connector from crossing
        the already-printed ring boundary in non-convex shapes.
        """
        if not children:
            return []
        remaining_children = list(children)
        cursor = near_point
        out = []
        while remaining_children:
            remaining_children.sort(key=lambda c: c.exterior.distance(Point(cursor)))
            child = remaining_children.pop(0)

            # Bridge along the innermost printed ring when moving between children
            # (not on the very first child — cursor is already at the ring interior).
            if out and innermost_ring is not None:
                child_near_pt = child.exterior.interpolate(
                    child.exterior.project(Point(cursor))
                )
                arc = _arc_along_ring(innermost_ring, cursor,
                                      (child_near_pt.x, child_near_pt.y))
                if len(arc) > 1:
                    out.extend(arc[1:])   # skip first: duplicate of cursor
                    cursor = arc[-1]

            sub_path = spiral_region(child, cursor)
            if not sub_path:
                continue
            out.extend(sub_path)
            cursor = sub_path[-1]
        return out

    def _resample_sequential(rings, samples, entry_hint):
        """Resample each ring in order, phase-anchored off the previous
        ring's own start point."""
        out, prev_start = [], entry_hint
        for r in rings:
            resampled = resample_closed_ring(r, samples, prev_start)
            out.append(resampled)
            prev_start = resampled[0]
        return out

    def spiral_region(poly, entry_hint):
        """Full DFS traversal of poly and everything nested inside it."""
        a_rings, b_rings, children = build_chains(poly)
        if not a_rings and not b_rings:
            start = entry_hint if entry_hint is not None else poly.centroid.coords[0]
            # No rings exist yet — children are direct sub-regions of poly.
            # Use poly itself as a proxy ring so the bridge stays inside it.
            proxy = poly if children else None
            return visit_children(children, start, innermost_ring=proxy)

        max_length = max(r.exterior.length for r in (a_rings + b_rings))
        samples = max(60, int(max_length / 0.75))

        a_seq = _resample_sequential(a_rings, samples, entry_hint)
        b_seq = _resample_sequential(b_rings, samples, entry_hint)

        in_path = blend_chain(a_seq, samples)
        turn_point = in_path[-1] if in_path else (a_seq[-1][0] if a_seq else entry_hint)
        child_path = visit_children(children, turn_point,
                                    innermost_ring=a_rings[-1])
        b_seq_reversed = [_reverse_winding(pts) for pts in reversed(b_seq)]
        out_path = blend_chain(b_seq_reversed, samples)

        return in_path + child_path + out_path

    path = spiral_region(base_poly, entry_hint)
    if not path:
        return []
    if start_from_inside:
        path = list(reversed(path))

    path = dedupe_path(path)

    # Close the outer-ring loop when start and end land near each other
    # (no seam phase shift on first layer, or very small shapes).
    if len(path) >= 2:
        d = (path[-1][0] - path[0][0]) ** 2 + (path[-1][1] - path[0][1]) ** 2
        if d < track_gap ** 2:
            path = path + [path[0]]

    return path
