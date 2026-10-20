# cfrp_slicer

Continuous carbon-fiber / UV-cured thermoset slicer. Core library: `src/cfrp_core/` (UI-free). Entry point: `pipeline.slice_part(ProcessConfig)`. Desktop app: `gui/` (PySide6), started by `gui_app.py` / `run.bat`.

Read first: `docs/DEEP_DIVE.md` (how slicing works), then `docs/ARCHITECTURE.md`, `docs/SPEC_current.md`. Next work: `docs/UPGRADE_PLAN_43-49.md`.

## Rules that must not be broken
- One continuous stroke per part: no travel moves, no retraction, no cutting the fiber.
- Min bend radius `r_min` is enforced in three places (fillet in geometry, `enforce_min_radius`, curvature audit in `validation.py`). New path generators must go through all three. With Strict Bend Radius (`strict_bend_radius`, Phase 43) the layer loops also pass every layer path through `StrictBends.repair` (`geometry/strict.py`) and climb with `strict_climb`; new layer loops must do the same.
- Trajectory = `list[dict]` with `x,y,z,dist,v,e_mult,layer,layer_start,is_transition,...`. G-code, validation and viewer all read it; keep its shape.
- `cfrp_core` must not import Qt or any other UI library (`tests/test_core_isolation.py`).
- Kinematics accel/decel scans are intentionally sequential loops; do not vectorise them (changes golden results).
- Golden tests (`tests/golden`): regenerate only deliberately with `python -m tests.golden.regenerate`.

## Layout
`config.py` models, `constraints.py` soft fixes, `geometry/` path generators (`zones.py`: layer recipe pieces), `layers/` layer loops/seams/ramps (`recipe.py`: Edge Rings / Wrap Holes), `kinematics.py` speed planner, `gcode/writer.py`, `validation.py`, `machine.py`, `diw/` + `board.py` structural electronics. `tools/` audits (also run in CI).
