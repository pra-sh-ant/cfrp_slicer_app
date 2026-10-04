"""Regression tests for the flat 2D laminate pipeline's layer-transition
mechanism: seam-trim + slope ramp between layers.

Each test here pins down a bug that was actually hit and fixed this session
-- see the docstring on each for what regressed without it.
"""
import app as app_mod
import machine as machine_mod
from tests.helpers import base_2d_config, layer_xy_bounds, max_transition_angle_deg
from validation import resolve_process_constraints


def _run(cfg, layup=(0.0,)):
    cfg = resolve_process_constraints(cfg)
    return app_mod._run_slicing_pipeline(cfg, False, list(layup), machine_mod.DEFAULT_MACHINE)


def test_multitrack_layers_stack_without_lateral_drift():
    """Regression: the original transition-ramp implementation translated an
    entire layer's geometry sideways to fake a ramp, so layers with
    different layup angles drifted away from each other instead of stacking
    into a straight part.

    anchor_length=0: the lead-in anchor deliberately hangs off layer 1 only,
    so it isn't part of the stacked part's footprint."""
    cfg = base_2d_config(layers=6, anchor_length=0.0)
    traj, _, _, val, _ = _run(cfg, layup=(0.0, 90.0, 45.0, -45.0))
    assert val["status"] == "Pass"

    bounds = layer_xy_bounds(traj)
    ref = None
    for layer in sorted(bounds):
        b = bounds[layer]
        if ref is None:
            ref = b
            continue
        assert abs(b[0] - ref[0]) < 0.5, f"layer {layer} drifted in X"
        assert abs(b[2] - ref[2]) < 0.5, f"layer {layer} drifted in Y"


def test_ramp_respects_configured_angle_when_geometry_allows():
    cfg = base_2d_config(layers=6, transition_angle=30.0)
    traj, _, _, val, _ = _run(cfg, layup=(0.0, 90.0))
    assert val["status"] == "Pass"
    assert max_transition_angle_deg(traj) <= 30.0 + 1e-6


def test_single_track_circle_ramps_instead_of_jumping_vertically():
    """Regression: a closed single-track loop's own start and end point
    coincide, which produced a zero-length tangent that silently disabled
    the ramp entirely -- the transition collapsed to a near-90-degree jump."""
    cfg = base_2d_config(
        shape="Circle", width=30.0, height=30.0, layers=5,
        tracks_per_layer=1, layer_height=1.0,
    )
    traj, _, _, val, _ = _run(cfg)
    assert val["status"] == "Pass"
    assert max_transition_angle_deg(traj) < 45.0


def test_single_track_square_ramps_instead_of_jumping_vertically():
    cfg = base_2d_config(
        shape="Square/Rectangle", width=30.0, height=30.0, layers=5,
        tracks_per_layer=1, layer_height=1.0,
    )
    traj, _, _, val, _ = _run(cfg)
    assert val["status"] == "Pass"
    assert max_transition_angle_deg(traj) < 45.0


def test_concave_shape_does_not_trigger_false_seam_jump_error():
    """Regression: layer_start was flagged on the next original path vertex
    (which can be a long, normal straight run away, e.g. a full wall edge)
    instead of the ramp's own landing point, tripping a false 'Disjointed
    Z-Seam' error on concave shapes like the L-Bracket."""
    cfg = base_2d_config(
        shape="L-Bracket", width=60.0, height=60.0, thickness=15.0,
        layers=6, tracks_per_layer=3, include_boundary=True,
        layer_height=1.5, transition_angle=15.0,
    )
    traj, _, _, val, _ = _run(cfg, layup=(0.0, 90.0))
    assert val["status"] in ("Pass", "Warning")
    assert not any("Disjointed Z-Seam" in m for m in val["messages"])


def test_steep_ramp_is_flagged_not_silently_violated():
    """When the part genuinely can't fit the requested ramp angle at this
    layer height, the slicer must say so instead of pretending it succeeded
    or silently printing steeper than configured."""
    cfg = base_2d_config(
        shape="Square/Rectangle", width=100.0, height=15.0, layers=6,
        layer_height=3.0, transition_angle=10.0,
    )
    traj, _, _, val, _ = _run(cfg)
    assert val["status"] == "Warning"
    assert any("exceeded the configured" in m for m in val["messages"])


def test_seam_trim_length_controls_ramp_steepness():
    """A longer trim should never produce a steeper max ramp angle than a
    shorter one, for the same geometry."""
    angles = {}
    for trim in (1.0, 3.0, 8.0):
        cfg = base_2d_config(
            shape="Circle", width=30.0, height=30.0, layers=5,
            tracks_per_layer=1, layer_height=1.0, seam_trim_length=trim,
        )
        traj, _, _, val, _ = _run(cfg)
        angles[trim] = max_transition_angle_deg(traj)
    assert angles[1.0] >= angles[3.0] >= angles[8.0]
