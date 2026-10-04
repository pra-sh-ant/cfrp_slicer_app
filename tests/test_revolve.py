"""Regression tests for the Z-revolve shell pipeline: Hollow Cylinder,
Hollow Square Tube, Hollow Sphere, Dome, Tapered Cylinder.

Each test pins down a bug actually hit and fixed this session -- see each
docstring for what regressed without it.
"""
import math

import pytest

import app as app_mod
import machine as machine_mod
from tests.helpers import base_revolve_config
from validation import resolve_process_constraints

REVOLVE_SHAPES = [
    "Hollow Cylinder (Z)", "Hollow Square Tube (Z)", "Hollow Sphere (Z)",
    "Dome (Z)", "Tapered Cylinder (Z)",
]


def _run(cfg):
    cfg = resolve_process_constraints(cfg)
    return app_mod._run_slicing_pipeline(cfg, True, [0.0], machine_mod.DEFAULT_MACHINE)


@pytest.mark.parametrize("shape", REVOLVE_SHAPES)
def test_shape_produces_a_valid_trajectory(shape):
    """Regression: Hollow Cylinder and Tapered Cylinder used to produce a
    fully empty trajectory because requesting corner-rounding on a thin
    annulus collapsed every ring."""
    cfg = base_revolve_config(shape=shape, tracks_per_layer=4, layer_height=1.0)
    traj, _, _, val, max_z = _run(cfg)
    assert traj, f"{shape} produced an empty trajectory"
    assert val["status"] in ("Pass", "Warning")


@pytest.mark.parametrize("shape", REVOLVE_SHAPES)
def test_no_negative_radius_points(shape):
    cfg = base_revolve_config(shape=shape, tracks_per_layer=4, layer_height=0.5)
    traj, _, _, _, _ = _run(cfg)
    cx, cy = cfg["start_x"], cfg["start_y"]
    for pt in traj:
        if pt.get("is_transition"):
            continue
        assert math.hypot(pt["x"] - cx, pt["y"] - cy) >= -1e-6


def test_tracks_per_layer_controls_ring_count():
    """Regression: wall thickness used to be a fixed mm value; it's now
    `tracks_per_layer`, and more tracks must mean more concentric rings."""
    cx, cy = 90.0, 110.0
    ring_counts = []
    for tracks in (2, 4, 6):
        cfg = base_revolve_config(
            shape="Hollow Cylinder (Z)", tracks_per_layer=tracks,
            layer_height=5.0, stem_height=20.0,
        )
        traj, _, _, val, _ = _run(cfg)
        layer1 = [p for p in traj if p.get("layer") == 1 and not p.get("is_transition")]
        radii = {round(math.hypot(p["x"] - cx, p["y"] - cy), 1) for p in layer1}
        ring_counts.append(len(radii))
    assert ring_counts[0] < ring_counts[1] < ring_counts[2]


def test_layers_alternate_outside_in_and_inside_out():
    cfg = base_revolve_config(
        shape="Hollow Cylinder (Z)", tracks_per_layer=4,
        layer_height=5.0, stem_height=20.0,
    )
    traj, _, _, val, _ = _run(cfg)
    cx, cy = cfg["start_x"], cfg["start_y"]

    directions = []
    for layer in sorted({pt["layer"] for pt in traj if not pt.get("is_transition")}):
        pts = [p for p in traj if p.get("layer") == layer and not p.get("is_transition")]
        start_r = math.hypot(pts[0]["x"] - cx, pts[0]["y"] - cy)
        end_r = math.hypot(pts[-1]["x"] - cx, pts[-1]["y"] - cy)
        directions.append(start_r > end_r)  # True = outside->inside

    assert len(directions) >= 3
    for a, b in zip(directions, directions[1:], strict=False):
        assert a != b, "consecutive layers must alternate outside-in / inside-out"


def test_sphere_height_is_derived_from_radius_not_config():
    """Regression: an independent Height field let the sphere be squashed
    into a lens/egg shape. Height must always be exactly 2x the radius,
    regardless of what stem_height says."""
    cfg = base_revolve_config(shape="Hollow Sphere (Z)", base_radius=20.0, stem_height=999.0)
    traj, _, _, _, max_z = _run(cfg)
    assert math.isclose(max_z, cfg["z_offset"] + 2 * 20.0, abs_tol=0.5)


def test_sphere_profile_matches_true_circle_equation():
    """Regression: the radius profile used sin(pi*progress) as a rough
    stand-in for a circle, which visibly flattens/points the poles even
    with the height fixed. (anchor_length=0: the lead-in's tip would be
    measured as the first layer's first point.)"""
    cfg = base_revolve_config(
        shape="Hollow Sphere (Z)", base_radius=20.0, tracks_per_layer=2,
        layer_height=0.5, anchor_length=0.0,
    )
    traj, _, _, _, _ = _run(cfg)
    cx, cy, z0, outer_r, gap = cfg["start_x"], cfg["start_y"], cfg["z_offset"], 20.0, cfg["track_gap"]

    errors = []
    for layer in sorted({pt["layer"] for pt in traj if not pt.get("is_transition")}):
        pts = [p for p in traj if p.get("layer") == layer and not p.get("is_transition")]
        z_centered = pts[0]["z"] - z0 - outer_r
        if abs(z_centered) >= outer_r:
            continue
        r_expected = math.sqrt(max(0.0, outer_r ** 2 - z_centered ** 2))
        # The outermost track is the sphere's skin. (The first point isn't:
        # inside-out layers start on the innermost track of the wall.)
        r_actual = max(math.hypot(p["x"] - cx, p["y"] - cy) for p in pts)
        errors.append(abs(r_actual - r_expected))

    assert errors
    # Only the expected half-track-gap centerline offset should show up.
    assert max(errors) < gap * 0.75 + 0.05


def test_sphere_gets_lead_in_anchor_on_first_printed_layer():
    """Regression: the anchor was only added when layer index 0 printed, but
    a sphere's bottom-pole slice is empty -- so spheres silently got none."""
    with_anchor, _, _, _, _ = _run(base_revolve_config(shape="Hollow Sphere (Z)", anchor_length=5.0))
    without, _, _, _, _ = _run(base_revolve_config(shape="Hollow Sphere (Z)", anchor_length=0.0))
    first = with_anchor[0]
    second = with_anchor[1]
    assert math.isclose(math.hypot(second["x"] - first["x"], second["y"] - first["y"]), 5.0, abs_tol=0.05)
    assert len(with_anchor) == len(without) + 1


def test_hollow_cylinder_wall_reaches_the_bore():
    """Regression: a 4-track wall only printed its outer 2 tracks."""
    cfg = base_revolve_config(
        shape="Hollow Cylinder (Z)", tracks_per_layer=4, layer_height=5.0,
        stem_height=20.0, anchor_length=0.0,
    )
    traj, _, _, val, _ = _run(cfg)
    cx, cy, gap = cfg["start_x"], cfg["start_y"], cfg["track_gap"]
    radii = [math.hypot(p["x"] - cx, p["y"] - cy) for p in traj if not p.get("is_transition")]
    bore = cfg["base_radius"] - 4 * gap
    assert min(radii) < bore + gap / 2.0 + 0.1
    assert min(radii) > bore
    assert val["status"] in ("Pass", "Warning")
