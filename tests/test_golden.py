"""Characterization tests: the slicer must reproduce the stored reference
outputs exactly (G-code byte-for-byte; trajectory arrays within 1e-9).

These pin behaviour, not correctness -- a reference can encode a known
issue (see docs/ARCHITECTURE.md, "Known issues"). To change a reference on
purpose, regenerate it with tests/golden/regenerate.py and record why in
CHANGELOG.md.
"""
import json

import numpy as np
import pytest

from tests.golden.cases import CASES
from tests.golden.regenerate import GOLDEN_DIR
from tests.golden.runner import BOOL_FIELDS, FLOAT_FIELDS, INT_FIELDS, run_case

ATOL = 1e-9

pytestmark = pytest.mark.golden


@pytest.fixture(scope="module", params=CASES, ids=lambda c: c["name"])
def result(request):
    case = request.param
    gcode, arrays, meta = run_case(case)
    return case, gcode, arrays, meta


def _expected(case):
    folder = GOLDEN_DIR / case["name"]
    gcode = (folder / "output.gcode").read_bytes().decode("utf-8")
    with np.load(folder / "trajectory.npz") as data:
        arrays = {k: data[k] for k in data.files}
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    return gcode, arrays, meta


def test_gcode_is_byte_identical(result):
    case, gcode, _, _ = result
    expected, _, _ = _expected(case)
    assert gcode == expected


def test_trajectory_arrays_match(result):
    case, _, arrays, _ = result
    _, expected, _ = _expected(case)
    assert arrays["x"].shape == expected["x"].shape, "point count changed"
    for key in FLOAT_FIELDS + ("ts_z", "ts_area"):
        np.testing.assert_allclose(arrays[key], expected[key], rtol=0, atol=ATOL, err_msg=key)
    np.testing.assert_allclose(arrays["ts_bounds"], expected["ts_bounds"], rtol=0, atol=ATOL, equal_nan=True)
    for key in INT_FIELDS + BOOL_FIELDS + ("ts_layer",):
        np.testing.assert_array_equal(arrays[key], expected[key], err_msg=key)


def test_validation_and_notes_match(result):
    case, _, _, meta = result
    _, _, expected = _expected(case)
    assert meta["status"] == expected["status"]
    assert meta["messages"] == expected["messages"]
    assert meta["constraint_notes"] == expected["constraint_notes"]
    assert abs(meta["max_z"] - expected["max_z"]) <= ATOL
