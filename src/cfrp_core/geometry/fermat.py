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


def _reverse_winding(ring_pts):
    """Reverse a resampled ring's own point traversal while keeping its
    anchor (index 0) fixed. Reversing ring order alone keeps the return
    pass winding the same way as the entry pass, so their radius bands
    cross nearly every lap; reversing each ring's point order too flips the
    rotational sense, keeping the two arms a constant gap apart like the
    two threads of a screw."""
    return [ring_pts[0]] + list(reversed(ring_pts[1:]))


def fermat_spiral_path(base_poly, track_gap, min_radius, track_count=None, start_from_inside=False):
    """One continuous "connected Fermat spiral" covering base_poly --
    including holes, and shapes that split into islands as they're offset.

    Each region is filled with two interleaved spirals grown in lockstep:
        chain A insets: gap/2 + 2k·gap      (inward pass)
        chain B insets: 1.5·gap + 2k·gap    (return pass, ring order AND
                                             per-ring winding reversed)
    Same pitch, constant half-pitch offset: at any angle each pass reaches
    its own unique radii, so the passes never cross. Splits become child
    regions, visited depth-first, nearest first.

    Known limit: with 2+ simultaneously-open branches, the straight
    connector between branches can cross printed track.

    `track_count` is a soft global cap on tracks across the whole branch
    tree (2 per ring pair); unlike the Archimedean spiral this never raises.
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

    def visit_children(children, near_point):
        """DFS the split-off branches nearest-first, each fully in and back
        out, chained end-to-start so the walk stays one continuous stroke."""
        if not children:
            return []
        remaining_children = list(children)
        cursor = near_point
        out = []
        while remaining_children:
            remaining_children.sort(key=lambda c: c.exterior.distance(Point(cursor)))
            child = remaining_children.pop(0)
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
            return visit_children(children, start)

        max_length = max(r.exterior.length for r in (a_rings + b_rings))
        samples = max(60, int(max_length / 0.75))

        a_seq = _resample_sequential(a_rings, samples, entry_hint)
        b_seq = _resample_sequential(b_rings, samples, entry_hint)

        in_path = blend_chain(a_seq, samples)
        turn_point = in_path[-1] if in_path else (a_seq[-1][0] if a_seq else entry_hint)
        child_path = visit_children(children, turn_point)
        b_seq_reversed = [_reverse_winding(pts) for pts in reversed(b_seq)]
        out_path = blend_chain(b_seq_reversed, samples)

        return in_path + child_path + out_path

    path = spiral_region(base_poly, None)
    if not path:
        return []
    if start_from_inside:
        path = list(reversed(path))

    return dedupe_path(path)
