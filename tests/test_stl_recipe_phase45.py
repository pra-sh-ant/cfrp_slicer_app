"""Phase 45 (first part): the layer recipe on STL slices -- Edge Rings along
each one-island slice's outline (bending round its holes), then the core at
the layer's angle; edge rings alone where a wall is too narrow for the
core."""
import numpy as np
import pytest

from cfrp_core import from_legacy_dict, slice_part
from cfrp_core.capabilities import ARCHIMEDEAN, FERMAT, ORIENTED_SERPENTINE, field_states
from cfrp_core.geometry._common import count_crossings


def _boxes_stl(path, boxes, h=1.0):
    """An ASCII STL of axis-aligned boxes (x0, y0, x1, y1), `h` tall; the
    slicer merges the bodies where they overlap."""
    faces = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
             (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    with open(path, "w") as f:
        f.write("solid boxes\n")
        for x0, y0, x1, y1 in boxes:
            v = np.array([[x0, y0, 0], [x1, y0, 0], [x1, y1, 0], [x0, y1, 0],
                          [x0, y0, h], [x1, y0, h], [x1, y1, h], [x0, y1, h]], dtype=float)
            for a, b, c in faces:
                f.write("facet normal 0 0 0\nouter loop\n")
                for k in (a, b, c):
                    f.write(f"vertex {v[k][0]} {v[k][1]} {v[k][2]}\n")
                f.write("endloop\nendfacet\n")
        f.write("endsolid boxes\n")
    return str(path)


def _frame_stl(path, w, d, wall, h=1.0):
    """An ASCII STL of a w x d rectangular frame with walls `wall` wide."""
    o = [(0, 0), (w, 0), (w, d), (0, d)]
    i = [(wall, wall), (w - wall, wall), (w - wall, d - wall), (wall, d - wall)]
    tris = []
    for k in range(4):
        a, b = o[k], o[(k + 1) % 4]
        c, e = i[k], i[(k + 1) % 4]
        tris += [((a, h), (b, h), (e, h)), ((a, h), (e, h), (c, h))]            # top, facing up
        tris += [((a, 0), (e, 0), (b, 0)), ((a, 0), (c, 0), (e, 0))]            # bottom, facing down
        tris += [((a, 0), (b, 0), (b, h)), ((a, 0), (b, h), (a, h))]            # outer wall, facing out
        tris += [((c, 0), (e, h), (e, 0)), ((c, 0), (c, h), (e, h))]            # inner wall, facing the hole
    with open(path, "w") as f:
        f.write("solid frame\n")
        for tri in tris:
            f.write("facet normal 0 0 0\nouter loop\n")
            for (x, y), z in tri:
                f.write(f"vertex {x} {y} {z}\n")
            f.write("endloop\nendfacet\n")
        f.write("endsolid frame\n")
    return str(path)


def _slice(path, **kw):
    cfg = dict(shape="STL Import", stl_path=path, layer_height=0.25, layup_seq="0, 90", fill_solid=True,
               strict_bend_radius=True, track_gap=1.2, min_radius=3.0, infill_type=ORIENTED_SERPENTINE)
    cfg.update(kw)
    return slice_part(from_legacy_dict(cfg))


def _layers(res):
    out = {}
    for q in res.trajectory:
        if not q.get("is_transition") and not q.get("is_anchor"):
            out.setdefault(q["layer"], []).append((q["x"], q["y"]))
    return out


@pytest.mark.parametrize("core", [ORIENTED_SERPENTINE, FERMAT])
def test_edge_rings_on_every_slice(tmp_path, core):
    path = _boxes_stl(tmp_path / "plate.stl", [(0, 0, 60, 30)])
    plain = _slice(path, infill_type=core)
    res = _slice(path, infill_type=core, edge_rings=2)
    assert res.validation["status"] == "Pass", res.validation["messages"]
    note = next(n for n in res.info if n.startswith("Layer recipe:"))
    assert "2 edge ring(s) along the outline on 4 slice(s)" in note
    assert all(count_crossings(p) == 0 for p in _layers(res).values())
    if core == ORIENTED_SERPENTINE:            # strict zigzags leave the edge band empty; the rings fill it
        assert res.stats["coverage"] > plain.stats["coverage"] + 0.08


def test_walls_too_narrow_for_the_core_get_rings_along_them(tmp_path):
    # a 60 x 10 mm bar: no zigzag core fits beside 2 edge rings, so rings
    # follow its walls across (as without the recipe)
    path = _boxes_stl(tmp_path / "bar.stl", [(0, 0, 60, 10)])
    res = _slice(path, edge_rings=2)
    note = next(n for n in res.info if n.startswith("Layer recipe:"))
    assert "4 slice(s) too narrow for the core got rings along their walls" in note, note
    plain = _slice(path)
    assert [(q["x"], q["y"]) for q in res.trajectory] == [(q["x"], q["y"]) for q in plain.trajectory]


def test_ring_shaped_slices_are_laid_without_the_recipe(tmp_path):
    # a frame (one big hole): walls, no middle
    path = _frame_stl(tmp_path / "frame.stl", 60.0, 40.0, 5.0)
    res = _slice(path, edge_rings=2)
    plain = _slice(path)
    note = next(n for n in res.info if n.startswith("Layer recipe:"))
    assert "4 ring-shaped slice(s)" in note, note
    assert [(q["x"], q["y"]) for q in res.trajectory] == [(q["x"], q["y"]) for q in plain.trajectory]


def test_slices_in_pieces_are_laid_without_the_recipe(tmp_path):
    path = _boxes_stl(tmp_path / "two.stl", [(0, 0, 30, 20), (40, 0, 70, 20)])
    res = _slice(path, edge_rings=1, stl_islands="all")
    note = next(n for n in res.info if n.startswith("Layer recipe:"))
    assert "in several pieces laid without it" in note
    assert res.validation["status"] != "Fail", res.validation["messages"]


def test_recipe_settings_on_stl():
    stl = dict(shape="STL Import", infill_type=ORIENTED_SERPENTINE, layers=2)
    s = field_states(stl)
    assert s["edge_rings"].enabled
    assert not s["wrap_holes"].enabled and "STL" in s["wrap_holes"].reason
    assert not field_states(dict(stl, infill_type=ARCHIMEDEAN))["edge_rings"].enabled
    assert not field_states(dict(stl, edge_rings=2))["include_boundary"].enabled
    assert field_states(dict(stl, edge_rings=2, stl_islands="all"))["include_boundary"].enabled
