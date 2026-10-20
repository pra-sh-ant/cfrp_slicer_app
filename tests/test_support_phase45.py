"""Phase 45: the support map -- areas of an STL slice with nothing under
them (overhangs), noted and marked in the 3D view."""
from shapely.geometry import box

from cfrp_core import from_legacy_dict, slice_part
from cfrp_core.geometry.mesh_slicer import support_map


def _boxes_stl(path, boxes):
    """ASCII STL of axis-aligned boxes (x0, y0, z0, x1, y1, z1)."""
    faces = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
             (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    with open(path, "w") as f:
        f.write("solid boxes\n")
        for x0, y0, z0, x1, y1, z1 in boxes:
            v = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
                 (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
            for a, b, c in faces:
                f.write("facet normal 0 0 0\nouter loop\n")
                for k in (a, b, c):
                    f.write(f"vertex {v[k][0]} {v[k][1]} {v[k][2]}\n")
                f.write("endloop\nendfacet\n")
        f.write("endsolid boxes\n")
    return str(path)


def test_support_map_finds_what_hangs_over_the_slice_below():
    stem, cap = box(10, 10, 30, 30), box(0, 0, 40, 40)
    out = support_map([stem, stem, cap, cap, None, stem], gap=1.2)
    assert out[0] == [] and out[1] == [] and out[3] == []
    # the cap's ring round the stem, less the half track that rests on its edge
    assert abs(sum(p.area for p in out[2]) - (cap.area - stem.buffer(0.6, join_style=2).area)) < 2.0
    assert out[4] == []
    assert out[5] and abs(out[5][0].area - stem.area) < 1.0        # over an empty slice (corners rounded)
    # a step narrower than half a track is not an overhang
    assert support_map([stem, box(10, 10, 30.5, 30)], gap=1.2)[1] == []


def test_stl_overhangs_are_noted_and_marked(tmp_path):
    path = _boxes_stl(tmp_path / "t.stl", [(10, 10, 0, 30, 30, 2.0), (0, 0, 2.0, 40, 40, 3.0)])
    cfg = dict(shape="STL Import", stl_path=path, layer_height=0.5, infill_type="Archimedean (Concentric Spiral)",
               fill_solid=True)
    res = slice_part(from_legacy_dict(cfg))
    note = next(n for n in res.info if n.startswith("Overhangs:"))
    assert "1 layer(s)" in note and "z 2.2" in note, note
    over = [m for m in res.markers if m[3] == "overhang"]
    assert over and all(abs(m[2] - over[0][2]) < 1e-9 for m in over)
    # a plain block has none
    block = _boxes_stl(tmp_path / "b.stl", [(0, 0, 0, 40, 30, 2.0)])
    res = slice_part(from_legacy_dict(dict(cfg, stl_path=block)))
    assert not any(n.startswith("Overhangs:") for n in res.info)
