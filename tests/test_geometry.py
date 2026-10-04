"""Unit tests for the geometry building blocks."""
import math

from cfrp_core import geometry as geo


def test_revolve_cross_section_annulus_area():
    poly = geo.revolve_cross_section(0.0, 0.0, 10.0, 6.0, "round")
    expected = math.pi * (10.0 ** 2 - 6.0 ** 2)
    # Shapely approximates a circle as a polygon (default buffer resolution),
    # so a small, consistent area deficit vs. the true circle is expected.
    assert math.isclose(poly.area, expected, rel_tol=0.01)


def test_revolve_cross_section_collapses_to_disk_when_inner_zero():
    poly = geo.revolve_cross_section(0.0, 0.0, 10.0, 0.0, "round")
    expected = math.pi * 10.0 ** 2
    assert math.isclose(poly.area, expected, rel_tol=0.01)


def test_revolve_cross_section_empty_when_outer_zero():
    poly = geo.revolve_cross_section(0.0, 0.0, 0.0, 0.0, "round")
    assert poly.is_empty


def test_revolve_cross_section_square_annulus_area():
    poly = geo.revolve_cross_section(0.0, 0.0, 5.0, 3.0, "square")
    expected = (10.0 ** 2) - (6.0 ** 2)  # side = 2*radius for the axis-aligned square
    assert math.isclose(poly.area, expected, rel_tol=1e-3)


def test_revolve_cross_section_inner_never_bigger_than_outer():
    """The annulus must never invert (inner radius clamped past outer)."""
    poly = geo.revolve_cross_section(0.0, 0.0, 5.0, 50.0, "round")
    # inner clamped to just under outer -- a thin ring, not empty/negative.
    assert not poly.is_empty
    assert poly.area < math.pi * 5.0 ** 2


def test_generate_archimedean_path_thin_annulus_survives_without_fillet():
    """Regression: requesting corner-rounding (min_radius > 0) on an annulus
    erodes it from both the outer AND inner edge at once, which can collapse
    a thin wall band ring-by-ring even when the overall band looks wide
    enough -- this produced a fully empty trajectory for Hollow Cylinder.
    Generating with min_radius=0 must not have this problem."""
    poly = geo.revolve_cross_section(0.0, 0.0, 15.6, 8.4, "round")
    path, _ = geo.archimedean_path(poly, 1.2, 0.0, track_count=None)
    assert path


def test_generate_archimedean_path_more_gap_yields_fewer_rings():
    poly = geo.revolve_cross_section(0.0, 0.0, 20.0, 0.0, "round")
    tight_path, _ = geo.archimedean_path(poly, 1.0, 0.0, track_count=None)
    loose_path, _ = geo.archimedean_path(poly, 4.0, 0.0, track_count=None)

    def ring_count(path):
        return len({round(math.hypot(x, y), 1) for x, y in path})

    assert ring_count(tight_path) > ring_count(loose_path)


def test_archimedean_fills_the_whole_hollow_wall_to_the_bore():
    """Regression: rings were offsets of the annulus itself, which shrinks
    from both edges at once -- a 4-track wall stopped at its midline and
    printed only the outer 2 tracks, leaving the inner half empty."""
    outer, gap, tracks = 18.0, 1.2, 4
    poly = geo.revolve_cross_section(0.0, 0.0, outer, outer - tracks * gap, "round")
    path, _ = geo.archimedean_path(poly, gap, 0.0, track_count=None)
    radii = [math.hypot(x, y) for x, y in path]
    innermost_centerline = outer - tracks * gap + gap / 2.0
    assert min(radii) < innermost_centerline + 0.1
    assert max(radii) > outer - gap / 2.0 - 0.1


def test_fermat_fills_the_whole_hollow_wall_to_the_bore():
    outer, gap, tracks = 18.0, 1.2, 4
    poly = geo.revolve_cross_section(0.0, 0.0, outer, outer - tracks * gap, "round")
    path = geo.fermat_spiral_path(poly, gap, 0.0, track_count=None)
    radii = [math.hypot(x, y) for x, y in path]
    assert min(radii) < outer - tracks * gap + gap / 2.0 + 0.1


def test_archimedean_spiral_never_enters_the_bore():
    outer, inner, gap = 18.0, 13.2, 1.2
    poly = geo.revolve_cross_section(0.0, 0.0, outer, inner, "round")
    path, _ = geo.archimedean_path(poly, gap, 0.0, track_count=None)
    assert min(math.hypot(x, y) for x, y in path) >= inner + gap / 2.0 * 0.85


def test_archimedean_traces_outer_ring_fully_in_both_directions():
    """Both spiral directions cover the outer ring to within one track_gap.

    Outside-in: blend starts at ring0 (outer) and drifts inward — max extent
    is slightly less than the exact outer-ring radius (≤ gap/2 short at the
    point diametrically opposite the seam), but both directions stay within
    one track_gap of each other so alternating layers overlap correctly.
    Inside-out: inner_close explicitly closes the outermost ring at full radius.
    """
    gap = 1.2
    poly = geo.revolve_cross_section(0.0, 0.0, 20.0, 0.0, "square")
    inward, _ = geo.archimedean_path(poly, gap, 0.0, track_count=4)
    outward, _ = geo.archimedean_path(poly, gap, 0.0, track_count=4, start_from_inside=True)
    for axis in (0, 1):
        assert math.isclose(min(p[axis] for p in inward), min(p[axis] for p in outward), abs_tol=gap)
        assert math.isclose(max(p[axis] for p in inward), max(p[axis] for p in outward), abs_tol=gap)
