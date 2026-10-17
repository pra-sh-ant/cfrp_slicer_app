# cfrp_slicer

Continuous carbon-fiber / UV-cured thermoset slicer. Core library: `src/cfrp_core/` (UI-free). Entry point: `pipeline.slice_part(ProcessConfig)`.

Read first: `docs/DEEP_DIVE.md` (how slicing works), then `docs/ARCHITECTURE.md`, `docs/SPEC_current.md`.

## Rules that must not be broken
- One continuous stroke per part: no travel moves, no retraction, no cutting the fiber.
- Min bend radius `r_min` is enforced in three places (fillet in geometry, `enforce_min_radius`, curvature audit in `validation.py`). New path generators must go through all three.
- Trajectory = `list[dict]` with `x,y,z,dist,v,e_mult,layer,layer_start,is_transition,...`. G-code, validation and viewer all read it; keep its shape.
- `cfrp_core` must not import streamlit/plotly/Qt (`tests/test_core_isolation.py`).
- Kinematics accel/decel scans are intentionally sequential loops; do not vectorise them (changes golden results).
- Golden tests (`tests/golden`): regenerate only deliberately with `python -m tests.golden.regenerate`.

## Layout
`config.py` models, `constraints.py` soft fixes, `geometry/` path generators, `layers.py` layer loops/seams/ramps, `kinematics.py` speed planner, `gcode/writer.py`, `validation.py`, `machine.py`.

`graphify-out/` is stale (2026-10-03, predates `cfrp_core`); re-run `/graphify` before relying on it.
