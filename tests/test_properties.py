"""Property-based (hypothesis) tests for small, pure building blocks."""
import math

from hypothesis import given, settings
from hypothesis import strategies as st

from cfrp_core.geometry import dedupe_path

coords = st.floats(min_value=-500, max_value=500, allow_nan=False, allow_infinity=False)
points = st.lists(st.tuples(coords, coords), min_size=0, max_size=200)


@given(points, st.floats(min_value=0.0, max_value=5.0))
@settings(max_examples=200, deadline=None)
def test_dedupe_path_keeps_endpoints_order_and_spacing(path, tol):
    out = dedupe_path(path, tol)
    if not path:
        assert out == []
        return
    assert out[0] == path[0]
    # Output is a subsequence of the input, in order.
    it = iter(path)
    assert all(any(p == q for q in it) for p in out)
    # Every consecutive output pair is strictly farther apart than tol.
    for a, b in zip(out[:-1], out[1:], strict=True):
        assert math.hypot(b[0] - a[0], b[1] - a[1]) > tol
