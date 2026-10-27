"""Project files (.cfrpproj) and named profiles."""
import json

import pytest

from cfrp_core.diw import DiwConfig, example_board
from cfrp_core.profiles import delete_profile, list_profiles, load_profile, save_profile
from cfrp_core.project import load_project, save_project


def test_project_round_trip(tmp_path):
    stl = tmp_path / "models" / "cup.stl"
    stl.parent.mkdir()
    stl.write_bytes(b"solid x\nendsolid x\n")
    params = {"shape": "STL Import", "stl_path": str(stl), "track_gap": 1.1, "layers": 3}
    path = save_project(tmp_path / "job.cfrpproj", params, example_board(), DiwConfig(layer_wait_s=5.0),
                        "embedded", app="test")
    raw = json.loads(path.read_text())
    assert raw["params"]["stl_path"].replace("\\", "/") == "models/cup.stl"      # relative: the folder moves
    back = load_project(path)
    assert back["params"]["stl_path"] == str(stl.resolve())
    assert back["params"]["track_gap"] == 1.1
    assert back["board"] == example_board()
    assert back["diw"] == DiwConfig(layer_wait_s=5.0)
    assert back["print_mode"] == "embedded" and back["ignored"] == []


def test_project_reports_unknown_keys_and_refuses_other_files(tmp_path):
    path = save_project(tmp_path / "a.cfrpproj", {"layers": 2}, None, {"line_width_mm": 0.6, "future_knob": 1})
    data = json.loads(path.read_text())
    data["extra_section"] = {}
    path.write_text(json.dumps(data))
    back = load_project(path)
    assert back["ignored"] == ["extra_section", "diw.future_knob"]
    assert back["diw"].line_width_mm == 0.6 and back["board"] is None
    other = tmp_path / "params.json"
    other.write_text(json.dumps({"layers": 2}))
    with pytest.raises(ValueError, match="not a CFRP slicer project"):
        load_project(other)
    data["version"] = 99
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="newer version"):
        load_project(path)


def test_profiles_keep_only_their_settings(tmp_path):
    save_profile("machine", "Big bed", {"bed_x": 300.0, "bed_y": 300.0, "track_gap": 9.0}, root=tmp_path)
    assert list_profiles("machine", root=tmp_path) == ["Big bed"]
    assert load_profile("machine", "Big bed", root=tmp_path) == {"bed_x": 300.0, "bed_y": 300.0}
    save_profile("material", "CF 1k / UV resin", {"track_gap": 1.0, "min_radius": 2.5}, root=tmp_path)
    assert list_profiles("material", root=tmp_path) == ["CF 1k _ UV resin"]       # '/' is not a file name
    # the cure: the dose is the resin's, the LED the machine's
    save_profile("material", "resin", {"uv_dose": 300.0, "uv_irradiance": 600.0}, root=tmp_path)
    assert load_profile("material", "resin", root=tmp_path) == {"uv_dose": 300.0}
    save_profile("machine", "head", {"uv_irradiance": 600.0, "uv_spot": 3.0, "uv_dose": 300.0}, root=tmp_path)
    assert load_profile("machine", "head", root=tmp_path) == {"uv_irradiance": 600.0, "uv_spot": 3.0}
    delete_profile("machine", "head", root=tmp_path)
    save_profile("ink", "silver", DiwConfig(spread_factor=1.2).model_dump(), root=tmp_path)
    ink = load_profile("ink", "silver", root=tmp_path)
    assert DiwConfig.model_validate(ink).spread_factor == 1.2
    with pytest.raises(ValueError):
        save_profile("ink", "bad", {"line_width_mm": -1.0}, root=tmp_path)
    with pytest.raises(ValueError, match="Nothing to save"):
        save_profile("machine", "empty", {"layers": 2}, root=tmp_path)
    delete_profile("machine", "Big bed", root=tmp_path)
    assert list_profiles("machine", root=tmp_path) == []
    with pytest.raises(ValueError, match="No machine profile"):
        load_profile("machine", "Big bed", root=tmp_path)
