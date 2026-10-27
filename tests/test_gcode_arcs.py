"""G2/G3 arcs in the G-code turn the way the path does: each arc's
direction, centre and end match the points it stands for (a viewer or the
firmware drawing a clockwise arc for an anticlockwise path goes the long way
round, nearly a full circle). Every arc turns less than half a turn (an arc
ending at its start is a full circle to the firmware), and its start and end
lie at one radius in the written G-code (GRBL-style firmware refuses arcs
whose two radii differ by more than ~0.005 mm)."""
import math

import pytest

from cfrp_core import from_legacy_dict, slice_part
from cfrp_core.gcode.writer import _detect_arcs, compile_gcode
from tests.helpers import base_2d_config, base_revolve_config


def _check_arcs(trajectory):
    arcs = [seg for seg in _detect_arcs(trajectory) if seg[0] == "arc"]
    for _, s, e, cx, cy, cmd in arcs:
        pts = [(trajectory[k]["x"], trajectory[k]["y"]) for k in range(s - 1, e + 1)]
        r0 = math.hypot(pts[0][0] - cx, pts[0][1] - cy)
        sense = 1.0 if cmd == "G3" else -1.0
        sweep = 0.0
        for (ax, ay), (bx, by) in zip(pts[:-1], pts[1:], strict=True):
            step = math.atan2((ax - cx) * (by - cy) - (ay - cy) * (bx - cx), (ax - cx) * (bx - cx) + (ay - cy) * (by - cy))
            assert step * sense > 0, f"{cmd} at ({ax:.2f}, {ay:.2f}) turns the other way"
            sweep += step
            assert math.hypot(bx - cx, by - cy) == pytest.approx(r0, abs=0.03)
        assert abs(sweep) < math.pi
        assert math.hypot(pts[-1][0] - cx, pts[-1][1] - cy) == pytest.approx(r0, abs=1e-9)
    return len(arcs)


def _gcode_arcs(gcode):
    """(start radius, end radius, sweep sign) of every G2/G3 as written."""
    x = y = 0.0
    out = []
    for ln in gcode.splitlines():
        words = ln.split(";")[0].split()
        if not words or words[0] not in ("G0", "G1", "G2", "G3"):
            continue
        w = {p[0]: float(p[1:]) for p in words[1:] if len(p) > 1}
        nx, ny = w.get("X", x), w.get("Y", y)
        if words[0] in ("G2", "G3"):
            cx, cy = x + w["I"], y + w["J"]
            out.append((math.hypot(x - cx, y - cy), math.hypot(nx - cx, ny - cy), math.dist((x, y), (nx, ny))))
        x, y = nx, ny
    return out


def _circle(cx, cy, r, a0, a1, n):
    return [(cx + r * math.cos(a0 + (a1 - a0) * k / n), cy + r * math.sin(a0 + (a1 - a0) * k / n)) for k in range(n + 1)]


@pytest.mark.parametrize("cx,cy", [(100.0, 100.0), (20.0, 150.0), (150.0, 15.0)])
@pytest.mark.parametrize("a0,a1", [(0.0, 1.5), (1.5, 0.0), (3.0, 4.6), (4.6, 3.0), (-2.5, -0.9)])
def test_arc_far_from_the_origin_turns_its_own_way(cx, cy, a0, a1):
    pts = _circle(cx, cy, 20.0, a0, a1, 24)
    traj, prev = [], pts[0]
    for x, y in pts:
        traj.append({"x": x, "y": y, "z": 0.2, "dist": math.hypot(x - prev[0], y - prev[1]), "v": 10.0, "e_mult": 1.0})
        prev = (x, y)
    assert _check_arcs(traj) == 1
    gcode = compile_gcode(traj, from_legacy_dict(base_2d_config()))
    arc = next(ln for ln in gcode.splitlines() if ln.split()[:1] in (["G2"], ["G3"]))
    assert arc.startswith("G3" if a1 > a0 else "G2")


@pytest.mark.parametrize("cfg", [
    base_2d_config(shape="Circle", width=40.0, height=40.0),
    base_revolve_config("Hollow Cylinder (Z)", tracks_per_layer=2),
    base_revolve_config("Wind Turbine Blade (Z)", stem_height=20.0, tracks_per_layer=2, blade_chord=50.0,
                        blade_tip_chord=25.0, blade_root_length=5.0, blade_root_width=36.0,
                        blade_root_thickness=16.0, min_radius=2.0),
])
def test_sliced_arcs_follow_the_path(cfg):
    res = slice_part(from_legacy_dict(cfg))
    assert _check_arcs(res.trajectory) > 0
    for r0, r1, chord in _gcode_arcs(res.gcode):
        assert abs(r1 - r0) <= 0.005                                      # one radius, after rounding
        assert chord > 0.001                                              # never back at its start


def test_a_full_ring_lap_is_split_into_arcs_under_half_a_turn():
    pts = _circle(100.0, 100.0, 15.0, 0.0, 2.0 * math.pi * 1.002, 400)    # just over one lap
    traj, prev = [], pts[0]
    for x, y in pts:
        traj.append({"x": x, "y": y, "z": 0.2, "dist": math.hypot(x - prev[0], y - prev[1]), "v": 10.0, "e_mult": 1.0})
        prev = (x, y)
    assert _check_arcs(traj) >= 2                                         # (each under half a turn)
    gcode = compile_gcode(traj, from_legacy_dict(base_2d_config()))
    assert all(chord > 0.001 and abs(r1 - r0) <= 0.005 for r0, r1, chord in _gcode_arcs(gcode))


def _moves(gcode):
    """(length, delta E, end) of every extruding G1 / G2 / G3, as written."""
    x = y = z = e = 0.0
    out = []
    for ln in gcode.splitlines():
        words = ln.split(";")[0].split()
        if words[:1] == ["G92"]:
            e = float(next(p[1:] for p in words[1:] if p[0] == "E"))
            continue
        if not words or words[0] not in ("G0", "G1", "G2", "G3"):
            continue
        w = {p[0]: float(p[1:]) for p in words[1:] if len(p) > 1}
        nx, ny, nz, ne = w.get("X", x), w.get("Y", y), w.get("Z", z), w.get("E", e)
        if not any(p[0] in "XY" for p in words[1:]):           # the plunge, the purge, the lift
            x, y, z, e = nx, ny, nz, ne
            continue
        if words[0] in ("G0", "G1"):
            length = math.dist((x, y, z), (nx, ny, nz))
        else:
            cx, cy = x + w["I"], y + w["J"]
            a0, a1 = math.atan2(y - cy, x - cx), math.atan2(ny - cy, nx - cx)
            sweep = (a1 - a0) % (2 * math.pi) if words[0] == "G3" else (a0 - a1) % (2 * math.pi)
            length = math.hypot(sweep * math.hypot(x - cx, y - cy), nz - z)
        if ne > e:
            out.append((length, ne - e, (nx, ny)))
        x, y, z, e = nx, ny, nz, ne
    return out


def test_each_move_gets_its_own_resin_and_none_is_dropped():
    """A point's `dist` from the speed planner is the step to the NEXT point:
    each move's resin comes from its own length, and a point followed by one
    on top of it is still moved to (it used to be dropped)."""
    cfg = from_legacy_dict(base_2d_config(e_const=0.05))
    pts = [(10.0, 10.0), (110.0, 10.0), (110.2, 10.0), (110.2, 10.0), (110.4, 10.1), (110.6, 10.3)]
    traj = []
    for k, (x, y) in enumerate(pts):
        nxt = pts[k + 1] if k + 1 < len(pts) else (x, y)
        traj.append({"x": x, "y": y, "z": 0.2, "dist": math.dist((x, y), nxt), "v": 10.0, "e_mult": 1.0})
    moves = _moves(compile_gcode(traj, cfg))
    assert [m[2] for m in moves] == [(110.0, 10.0), (110.2, 10.0), (110.4, 10.1), (110.6, 10.3)]   # the last one too
    for length, de, _ in moves:
        assert de == pytest.approx(length * 0.05, abs=2e-4)


@pytest.mark.parametrize("cfg", [
    base_2d_config(infill_type="Oriented Serpentine", layers=3, layup_seq="0, 90"),
    base_2d_config(infill_type="45° Diagonal Hatch", layers=2),
    base_revolve_config("Hollow Sphere (Z)", tracks_per_layer=2),
])
def test_resin_follows_the_moves_on_sliced_parts(cfg):
    res = slice_part(from_legacy_dict(cfg))
    e_const = res.config.e_const
    for length, de, _ in _moves(res.gcode):
        if length > 0.5:
            assert de <= length * e_const * 1.002 + 2e-4          # e_mult <= 1: never more than the move's length allows
    _check_arcs(res.trajectory)
