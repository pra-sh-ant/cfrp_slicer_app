"""Toolpath geometry generation: shape outlines, wall offsets, and infill patterns."""
import math

import numpy as np
from shapely.affinity import rotate, translate
from shapely.geometry import LineString, Point, Polygon


def _check_gap_radius(track_gap, min_radius):
    """The one shared guard every path generator runs before looping on
    `track_gap` -- a value <= 0 would never advance the offset loops."""
    if track_gap <= 0:
        raise ValueError(f"Track Gap must be greater than 0 mm (got {track_gap}).")
    if min_radius < 0:
        raise ValueError(f"Min Bend Radius cannot be negative (got {min_radius}).")


def _validate_process_params(config):
    _check_gap_radius(config['track_gap'], config['min_radius'])


def _largest_polygon(geom):
    """The single biggest Polygon in geom (or geom itself if it already is one)."""
    if geom.geom_type == 'MultiPolygon':
        return max(geom.geoms, key=lambda g: g.area)
    return geom


class GeometryEngine:
    @staticmethod
    def get_revolve_cross_section(cx, cy, outer_radius, inner_radius, shape_type='round'):
        """Build one Z-slice's printable cross-section for a revolved shell:
        an annulus (outer minus inner) when there's a hole, or a solid disk
        /square once the inner radius collapses to ~0 (e.g. at a sphere's
        pole or a dome's apex, or for a solid cone).
        """
        def build(radius):
            if radius <= 1e-6:
                return Polygon()
            if shape_type == 'square':
                return Polygon([
                    (cx - radius, cy - radius), (cx + radius, cy - radius),
                    (cx + radius, cy + radius), (cx - radius, cy + radius),
                ])
            return Point(cx, cy).buffer(radius)

        outer_poly = build(outer_radius)
        if outer_poly.is_empty or inner_radius <= 1e-6:
            return outer_poly
        inner_poly = build(min(inner_radius, outer_radius * 0.999))
        return outer_poly.difference(inner_poly)

    @staticmethod
    def get_printable_boundary(base_poly, config):
        """Return the centerline envelope after wall clearance and corner rounding."""
        gap = float(config['track_gap'])
        min_rad = float(config['min_radius'])
        boundary = base_poly.buffer(-gap / 2.0, join_style=1)
        if boundary.is_empty or min_rad <= 0:
            return boundary

        rounded = GeometryEngine._fillet(boundary, min_rad)
        return rounded if not rounded.is_empty else boundary

    @staticmethod
    def _serpentine_print_extent(config):
        """The exact zigzag geometry -- X range, line spacing, and line
        count -- that generate_serpentine_infill actually walks. Shared
        with get_base_polygon so the boundary shown in the viewer can never
        drift from the path that's actually printed (they used to be two
        independently-computed rectangles that silently disagreed whenever
        invert_bends was on)."""
        w, h = config['width'], config['height']
        min_rad = config['min_radius']
        gap = config['track_gap']
        invert_bends = config.get('invert_bends', False)
        line_gap = max(gap, 2.0 * min_rad)
        effective_w = w - (min_rad * 2) if not invert_bends else w
        start_x = min_rad if not invert_bends else 0.0
        end_x = start_x + effective_w
        num_lines = int(h / line_gap)
        return start_x, end_x, line_gap, num_lines

    @staticmethod
    def get_base_polygon(config):
        shape, w, h = config['shape'], config['width'], config['height']
        if shape == "Square/Rectangle":
            poly = Polygon([(0, 0), (w, 0), (w, h), (0, h)])
        elif shape == "Serpentine Infill":
            start_x, end_x, line_gap, num_lines = GeometryEngine._serpentine_print_extent(config)
            end_y = num_lines * line_gap
            # Only the end_x-side U-turn actually bulges past its anchor
            # (by its own radius, line_gap/2, to make the turn) -- the
            # start_x-side turn curves back inward toward end_x instead, so
            # it never exceeds start_x. Padding only the side that actually
            # bulges is what keeps this boundary matching the path's real
            # envelope instead of over- or under-covering it.
            pad = line_gap / 2.0 if num_lines >= 1 else 0.0
            poly = Polygon([
                (start_x, 0), (end_x + pad, 0),
                (end_x + pad, end_y), (start_x, end_y),
            ])
        elif shape == "L-Bracket":
            t = config['thickness']
            poly = Polygon([(0, 0), (w, 0), (w, t), (t, t), (t, h), (0, h)])
        elif shape == "Triangle":
            poly = Polygon([(0, 0), (w, 0), (w / 2.0, h)])
        elif shape == "Hexagon":
            r = w / 2.0
            cx, cy = r, r
            poly = Polygon([(cx + r * math.cos(math.radians(60 * i)), cy + r * math.sin(math.radians(60 * i))) for i in range(6)])
        elif shape == "Circle":
            poly = Point(w / 2.0, h / 2.0).buffer(w / 2.0)
        else:
            poly = Polygon()

        return translate(poly, xoff=config['start_x'], yoff=config['start_y']).buffer(0)

    @staticmethod
    def generate_laminate_layer(base_poly, config, angle, layer_idx=0, last_pt=None):
        _validate_process_params(config)
        gap = config['track_gap']
        min_rad = config['min_radius']

        wall_poly = GeometryEngine.get_printable_boundary(base_poly, config)
        if wall_poly.is_empty or wall_poly.geom_type != 'Polygon':
            return []
        wall_coords = list(wall_poly.exterior.coords)

        infill_bounds = wall_poly.buffer(-gap * 0.6, join_style=1)
        if infill_bounds.is_empty:
            return wall_coords
        infill_bounds = _largest_polygon(infill_bounds)

        is_outward = (layer_idx % 2 != 0)

        infill_type = config.get('infill_type', 'Archimedean (Concentric Spiral)')
        if infill_type == "Archimedean (Concentric Spiral)":
            infill_path, _ = GeometryEngine.generate_archimedean_path(
                infill_bounds,
                gap,
                min_rad,
                track_count=config.get('tracks_per_layer'),
                start_from_inside=is_outward
            )
        elif infill_type == "Fermat Spiral (Connected Double Spiral)":
            infill_path = GeometryEngine.generate_fermat_spiral_path(
                infill_bounds,
                gap,
                min_rad,
                track_count=config.get('tracks_per_layer'),
                start_from_inside=is_outward
            )
        else:
            infill_path = GeometryEngine.generate_oriented_serpentine(
                infill_bounds, angle, gap, min_rad,
                start_from_end=is_outward
            )

        if not infill_path:
            return wall_coords if config.get('include_boundary', False) else []

        if not config.get('include_boundary', False):
            if last_pt:
                # The layer index already determines the intended start side.
                # Never reverse this path: doing so breaks the outer/inner
                # alternation and changes the spiral winding direction.
                return [last_pt] + infill_path
            return infill_path

        continuous_path = []

        if is_outward:
            if last_pt:
                continuous_path.append(last_pt)
            continuous_path.extend(infill_path)

            peel_pt = continuous_path[-1]
            distances = [math.hypot(x - peel_pt[0], y - peel_pt[1]) for x, y in wall_coords]
            min_idx = distances.index(min(distances))
            wall_reordered = wall_coords[min_idx:] + wall_coords[:min_idx] + [wall_coords[min_idx]]
            continuous_path.extend(wall_reordered)

        else:
            if last_pt:
                distances = [math.hypot(x - last_pt[0], y - last_pt[1]) for x, y in wall_coords]
                min_idx = distances.index(min(distances))
                wall_reordered = wall_coords[min_idx:] + wall_coords[:min_idx] + [wall_coords[min_idx]]
                continuous_path.append(last_pt)
            else:
                wall_reordered = wall_coords

            continuous_path.extend(wall_reordered)

            peel_pt = continuous_path[-1]
            # Both spiral patterns keep one monotonic winding direction end
            # to end (Archimedean: strictly inward/outward; Fermat: an
            # in-then-out double pass) -- reversing either one would break
            # that, so only the plain zigzag pattern gets end-swapped here.
            if config.get('infill_type') not in (
                "Archimedean (Concentric Spiral)", "Fermat Spiral (Connected Double Spiral)"
            ):
                d_start = math.hypot(infill_path[0][0] - peel_pt[0], infill_path[0][1] - peel_pt[1])
                d_end = math.hypot(infill_path[-1][0] - peel_pt[0], infill_path[-1][1] - peel_pt[1])
                if d_end < d_start:
                    infill_path.reverse()

            continuous_path.extend(infill_path)

        return GeometryEngine._dedupe_path(continuous_path)

    @staticmethod
    def generate_oriented_serpentine(poly, angle, gap, min_rad, start_from_end=False):
        _check_gap_radius(gap, min_rad)

        rot_poly = rotate(poly, -angle, origin='centroid')
        safe_poly = rot_poly.buffer(-min_rad, join_style=1)
        if safe_poly.is_empty or safe_poly.area < gap:
            safe_poly = rot_poly

        minx, miny, maxx, maxy = safe_poly.bounds
        path = []
        y = miny + gap / 2.0
        lines = []

        while y <= maxy:
            line = LineString([(minx - gap * 2, y), (maxx + gap * 2, y)])
            inter = line.intersection(safe_poly)
            if inter.geom_type == 'LineString' and not inter.is_empty:
                lines.append(inter)
            elif inter.geom_type == 'MultiLineString' and list(inter.geoms):
                longest = max(list(inter.geoms), key=lambda g: g.length)
                lines.append(longest)
            y += gap

        if start_from_end:
            lines = list(reversed(lines))
            direction = -1
        else:
            direction = 1

        for i, line in enumerate(lines):
            coords = list(line.coords)
            if direction == -1:
                coords.reverse()

            if i > 0 and path:
                p0 = np.array(path[-1])
                p3 = np.array(coords[0])
                ext_dir = -direction
                ext_mag = max(min_rad * 1.5, gap)

                p1 = p0 + np.array([ext_dir * ext_mag, 0])
                p2 = p3 + np.array([ext_dir * ext_mag, 0])

                t = np.linspace(0, 1, 15)
                t_mat = np.array([t]).T
                curve = (1 - t_mat)**3 * p0 + 3*(1 - t_mat)**2 * t_mat * p1 + \
                        3*(1 - t_mat) * t_mat**2 * p2 + t_mat**3 * p3
                path.extend(curve.tolist())

            path.extend(coords)
            direction *= -1

        if not path:
            return []
        final_line = LineString(path)
        final_line = rotate(final_line, angle, origin='centroid')
        return list(final_line.coords)

    @staticmethod
    def generate_serpentine_infill(config):
        _validate_process_params(config)
        # A U-turn between parallel tracks needs at least 2R of vertical room,
        # which _serpentine_print_extent already accounts for (line_gap =
        # max(gap, 2*min_rad)) -- shared with get_base_polygon so the two
        # can't independently drift apart.
        start_x, end_x, line_gap, num_lines = GeometryEngine._serpentine_print_extent(config)
        path = []

        def u_turn(x, y_start, y_end, side):
            center_y = (y_start + y_end) / 2.0
            radius = abs(y_end - y_start) / 2.0
            angles = np.linspace(-math.pi / 2.0, math.pi / 2.0, 15)
            if side < 0:
                angles = math.pi - angles
            return [(x + side * radius * math.cos(angle),
                     center_y + radius * math.sin(angle)) for angle in angles[1:]]

        for i in range(num_lines + 1):
            y = i * line_gap
            if i % 2 == 0:
                path.extend([(start_x, y), (end_x, y)])
                if i < num_lines:
                    path.extend(u_turn(end_x, y, y + line_gap, 1))
            else:
                path.extend([(end_x, y), (start_x, y)])
                if i < num_lines:
                    path.extend(u_turn(start_x, y, y + line_gap, -1))

        filtered_path = GeometryEngine._dedupe_path(path)
        if not filtered_path:
            return []

        linestring = LineString(filtered_path)
        if config.get('angle', 0.0) != 0.0:
            linestring = rotate(linestring, config['angle'], origin=Point(config['width'] / 2, config['height'] / 2))
        return list(translate(linestring, xoff=config['start_x'], yoff=config['start_y']).coords)

    @staticmethod
    def generate_archimedean_path(base_poly, track_gap, min_radius, track_count=None,
                                  start_from_inside=False):
        """Generate one smooth, continuously-blended spiral inside an
        arbitrary polygon boundary -- including a circle, which is just
        another polygon here (shapely already approximates Point().buffer()
        as one, so there's no exactness advantage a dedicated circle-only
        implementation would actually preserve).

        Concentric offset rings are built as before, but instead of splicing
        ring-to-ring by jumping to the nearest point (which produces the short
        back-and-forth segments at tight corners/turnarounds), each ring is
        resampled at matched arc-length parameters and consecutive rings are
        linearly blended across one full revolution. That keeps the path
        monotonically spiraling in with no direction reversals.

        The first and last ring are each also traced as one full closed lap.
        A blend lap only touches its end rings at a single point, so without
        these the outermost wall was only fully reached when the spiral
        started outside -- inside-out layers sat a track gap smaller, and
        alternating layers didn't stack into a straight wall.
        """
        _check_gap_radius(track_gap, min_radius)
        if base_poly.is_empty:
            return [], base_poly
        base_poly = _largest_polygon(base_poly)
        holes = GeometryEngine._hole_keepouts(base_poly)

        ring_polys, current_inset = [], track_gap / 2.0
        target_count = int(track_count) if track_count is not None else None
        while target_count is None or len(ring_polys) < target_count:
            comps = GeometryEngine._inset_components(
                base_poly, current_inset, min_radius, holes, track_gap / 2.0
            )
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

        # Shared sample count per revolution: fine enough to trace tight
        # fillets smoothly regardless of how big/small any given ring is,
        # without over-sampling large shapes into a point count that bogs
        # down downstream kinematics planning and the 3D viewer.
        max_length = max(poly.exterior.length for poly in ordered)
        samples = max(120, int(max_length / 0.75))

        resampled = []
        prev_start = None
        for poly in ordered:
            pts = GeometryEngine._resample_closed_ring(poly, samples, prev_start)
            resampled.append(pts)
            prev_start = pts[0]

        first_lap = resampled[0] + [resampled[0][0]]
        last_lap = resampled[-1][1:] + [resampled[-1][0]]
        continuous_path = first_lap + GeometryEngine._blend_chain(resampled, samples) + last_lap

        return GeometryEngine._dedupe_path(continuous_path), base_poly

    @staticmethod
    def _fillet(poly, min_radius):
        if min_radius <= 0:
            return poly
        return poly.buffer(-min_radius, join_style=1).buffer(min_radius, join_style=1)

    @staticmethod
    def _hole_keepouts(poly):
        """poly's holes as solid polygons (e.g. the bore of a hollow wall)."""
        if poly.is_empty or poly.geom_type != 'Polygon':
            return []
        return [Polygon(ring) for ring in poly.interiors]

    @staticmethod
    def _inset_components(poly, inset, min_radius, holes=(), clearance=0.0, area_eps=1.0):
        """One concentric ring step shared by both spiral generators: offset
        poly's OUTER boundary inward by `inset` and fillet it. Returns a list
        of Polygon components (2+ means the offset split), or None once it
        vanishes or its ring would come closer than `clearance` to a hole.

        Offsetting only the outer shell -- not the holed polygon itself -- is
        what lets rings march all the way across a hollow wall to the bore.
        Offsetting the annulus shrinks it from both edges at once, so it used
        to run out of room at the wall's midline and leave the inner half of
        every hollow-shell wall unprinted."""
        offset_poly = Polygon(poly.exterior).buffer(-inset, join_style=1)
        if offset_poly.is_empty or offset_poly.area < area_eps:
            return None
        filleted = GeometryEngine._fillet(offset_poly, min_radius)
        if filleted.is_empty:
            return None
        comps = list(filleted.geoms) if filleted.geom_type == 'MultiPolygon' else [filleted]
        comps = [c for c in comps if c.geom_type == 'Polygon' and c.area >= area_eps]
        if not comps:
            return None
        # 10% slack: shapely approximates round rings/bores as polygons, so a
        # ring sitting exactly `clearance` off a bore measures a hair under it.
        min_clearance = clearance * 0.9
        for comp in comps:
            if any(comp.exterior.distance(hole) < min_clearance for hole in holes):
                return None
        return comps

    @staticmethod
    def _dedupe_path(points, tol=0.05):
        """Drop consecutive points closer than `tol` apart -- the blend/
        resample generators all produce a few near-duplicate points at
        ring-transition seams; collapsing them keeps kinematics planning
        and the viewer from choking on effectively-zero-length segments."""
        if not points:
            return []
        out = [points[0]]
        for pt in points[1:]:
            if math.hypot(pt[0] - out[-1][0], pt[1] - out[-1][1]) > tol:
                out.append(pt)
        return out

    @staticmethod
    def _resample_closed_ring(poly, samples, phase_start=None):
        """Resample poly's exterior into `samples` evenly arc-length-spaced
        points, starting as close as possible to phase_start so consecutive
        rings stay angularly aligned for blending (same trick used by
        generate_archimedean_path)."""
        line = LineString(list(poly.exterior.coords))
        length = line.length
        offset = line.project(Point(phase_start)) if phase_start is not None else 0.0
        return [
            (pt.x, pt.y)
            for pt in (line.interpolate((offset + i / samples * length) % length) for i in range(samples))
        ]

    @staticmethod
    def _blend_chain(rings, samples):
        """Continuous path threading through consecutive phase-aligned,
        resampled rings by linearly blending across one revolution per ring
        pair -- monotonic pitch, never crosses itself."""
        if not rings:
            return []
        if len(rings) == 1:
            return list(rings[0])
        path = []
        for ring_a, ring_b in zip(rings[:-1], rings[1:], strict=False):
            for i in range(samples):
                u = i / samples
                ax, ay = ring_a[i]
                bx, by = ring_b[i]
                path.append((ax * (1 - u) + bx * u, ay * (1 - u) + by * u))
        path.append(rings[-1][0])
        return path

    @staticmethod
    def generate_fermat_spiral_path(base_poly, track_gap, min_radius, track_count=None,
                                    start_from_inside=False):
        """Generate one continuous, non-self-crossing "connected Fermat
        spiral" covering base_poly -- including holes, and shapes that split
        into separate islands as they're offset inward.

        generate_archimedean_path keeps only the single largest polygon
        whenever an inward offset splits (see its `max(..., key=area)`
        below), silently dropping the rest of the material at that split.
        This generator instead treats every split as a branch: each region
        is filled with two interleaved spirals -- pass A and pass B -- each
        built exactly like generate_archimedean_path's own single spiral
        (one full revolution's blend per ring, radius stepping by one full
        2x track_gap per ring), just started half of THAT (one track_gap)
        apart -- A's first ring sits track_gap/2 inside the boundary, B's
        sits 1.5x track_gap in -- and grown in lockstep, ring for ring.
        Two things are both required for the passes to never cross:
          1. Same per-ring pitch (2x track_gap), offset by a constant half
             (track_gap): at any angle, each pass only ever reaches its own
             exact, unique radii (two arithmetic sequences that can never
             land on the same value at the same angle).
          2. Pass B's ring order AND each ring's own point traversal are
             BOTH reversed for the return leg, not just the ring order.
             Reversing ring order alone still winds the same rotational
             sense as pass A, so the two overlapping radius bands cross
             partway through nearly every lap; reversing each ring's point
             order too flips B's rotational sense, which is what actually
             keeps the two arms a constant track_gap apart (like the two
             threads of a screw) instead of weaving through each other.

        Both passes are phase-anchored from the same entry point, so pass A
        (in) starts there and pass B (return) finishes close to that same
        point again, which is what keeps single-region and single-hole
        (annulus) results a provably non-self-crossing double spiral.

        Multiple, separately-visited branches (two or more disjoint holes
        or islands within the same region) are a documented remaining
        limitation: each branch is individually a clean double spiral, but
        the connector between one branch's exit and the next branch's
        entry is a plain nearest-point straight line, not a routed path
        that's guaranteed to avoid already-printed track -- so a region
        with several separate branches can, rarely, still produce a short
        crossing at a branch connector. A single region or a single hole
        never has this problem; only 2+ simultaneously-open branches can.

        `track_count`, when given, is a soft global cap on the total number
        of tracks laid across the whole branch tree (not per-branch). Unlike
        generate_archimedean_path this never raises if fewer tracks fit --
        how many tracks a branching region can hold isn't known up front.
        """
        _check_gap_radius(track_gap, min_radius)
        if base_poly.is_empty or base_poly.geom_type != 'Polygon':
            return []

        holes = GeometryEngine._hole_keepouts(base_poly)
        remaining = [int(track_count)] if track_count is not None else [None]

        def _next_ring(poly, inset):
            """One offset+fillet step. Returns None (vanished), a list of
            >=2 Polygons (split), or a single-element list (clean ring)."""
            return GeometryEngine._inset_components(
                poly, inset, min_radius, holes, track_gap / 2.0
            )

        def build_chains(poly):
            """Grow two ring chains in lockstep, one full track_gap apart in
            radius from the very first ring, both stepping inward by a full
            track_gap per ring (same pitch generate_archimedean_path uses
            for its single spiral). Stops -- for both chains together -- at
            the first depth where either one vanishes or splits; whichever
            one did is where `children` comes from. Returns (a_rings,
            b_rings, children)."""
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
            """DFS the split-off branches nearest-first, each fully in and
            back out, chained end-to-start so the whole walk stays one
            continuous stroke."""
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
            """Resample each ring in order, each one phase-anchored off the
            previous ring's own start point (drift-minimizing, same trick
            generate_archimedean_path uses)."""
            out, prev_start = [], entry_hint
            for r in rings:
                resampled = GeometryEngine._resample_closed_ring(r, samples, prev_start)
                out.append(resampled)
                prev_start = resampled[0]
            return out

        def _reverse_winding(ring_pts):
            """Reverse a resampled ring's own point traversal while keeping
            its anchor (index 0) point fixed. Two spirals built with the SAME
            winding direction, radially interleaved and simply walked in
            opposite ring order, still cross: reversing ring order alone
            flips which radius comes first but not the rotational sense, so
            the "return" pass keeps winding the same way as the "entry" pass
            and their overlapping radius bands intersect nearly every lap.
            Reversing each ring's own point order too is what actually
            reverses the return pass's rotational sense -- like a screw
            thread, the two arms of a real double spiral wind oppositely
            relative to time, and that's what keeps them a constant
            track_gap apart instead of weaving through each other."""
            return [ring_pts[0]] + list(reversed(ring_pts[1:]))

        def spiral_region(poly, entry_hint):
            """Full DFS traversal of poly and everything nested inside it.
            Returns a continuous point list starting and (when at least one
            ring fits) ending close to poly's own outer boundary."""
            a_rings, b_rings, children = build_chains(poly)
            if not a_rings and not b_rings:
                start = entry_hint if entry_hint is not None else poly.centroid.coords[0]
                return visit_children(children, start)

            max_length = max(r.exterior.length for r in (a_rings + b_rings))
            samples = max(60, int(max_length / 0.75))

            a_seq = _resample_sequential(a_rings, samples, entry_hint)
            b_seq = _resample_sequential(b_rings, samples, entry_hint)

            in_path = GeometryEngine._blend_chain(a_seq, samples)
            turn_point = in_path[-1] if in_path else (a_seq[-1][0] if a_seq else entry_hint)
            child_path = visit_children(children, turn_point)
            b_seq_reversed = [_reverse_winding(pts) for pts in reversed(b_seq)]
            out_path = GeometryEngine._blend_chain(b_seq_reversed, samples)

            return in_path + child_path + out_path

        path = spiral_region(base_poly, None)
        if not path:
            return []
        if start_from_inside:
            path = list(reversed(path))

        return GeometryEngine._dedupe_path(path)
