"""Phase 45: STL overhangs -- the part of a slice with nothing under it is
found, fibre crosses it at the Overhang Speed (zigzags across it the short
way), and long unsupported runs are reported."""
import shapely

from cfrp_core import from_legacy_dict, slice_part
from cfrp_core.capabilities import field_states
from tests.test_ranges_phase45 import _stack_stl


def _mushroom(tmp_path, **kw):
    # a 10 x 10 mm stem, 1 mm tall, under a 40 x 40 mm cap
    path = _stack_stl(tmp_path / "cap.stl", [(15, 15, 0, 25, 25, 1), (0, 0, 1, 40, 40, 1.5)])
    cfg = dict(shape="STL Import", stl_path=path, layer_height=0.25, track_gap=1.2, min_radius=3.0,
               infill_type="Oriented Serpentine", layup_seq="0, 90", fill_solid=True, v_max=10.0,
               v_overhang=4.0)
    cfg.update(kw)
    return slice_part(from_legacy_dict(cfg))


def test_fibre_over_an_overhang_slows_down(tmp_path):
    res = _mushroom(tmp_path)
    note = next(n for n in res.info if n.startswith("Overhangs:"))
    assert "layers 5" in note and "Overhang Speed (4 mm/s)" in note
    assert any(m[3] == "overhang" for m in res.markers)
    # the cap's first slice: every point over the empty ring runs at 4 mm/s
    first_cap = min(q["layer"] for q in res.trajectory if q["layer"] >= 5)
    stem = shapely.geometry.box(15, 15, 25, 25)
    xs = [q["x"] for q in res.trajectory if q["layer"] == 1]
    ys = [q["y"] for q in res.trajectory if q["layer"] == 1]
    stem = shapely.affinity.translate(stem, min(xs) - 15.6, min(ys) - 15.6)      # as placed on the bed
    over = [q for q in res.trajectory if q["layer"] == first_cap and not q.get("is_transition")
            and not stem.buffer(2.0).contains(shapely.Point(q["x"], q["y"]))]
    assert over and max(q["v"] for q in over) <= 4.0 + 1e-9
    # the stem below runs at the usual speeds
    assert max(q["v"] for q in res.trajectory if q["layer"] == 1) > 4.5


def test_long_unsupported_runs_are_reported(tmp_path):
    loose = _mushroom(tmp_path, overhang_span=5000.0)
    assert not any(w.startswith("Unsupported spans") for w in loose.validation["messages"])
    tight = _mushroom(tmp_path, overhang_span=5.0)
    assert any(w.startswith("Unsupported spans") for w in tight.validation["messages"])


def test_overhang_settings():
    stl = field_states(dict(shape="STL Import"))
    assert stl["v_overhang"].enabled and stl["overhang_span"].enabled
    flat = field_states(dict(shape="Square/Rectangle"))
    assert not flat["v_overhang"].enabled and not flat["overhang_span"].enabled
