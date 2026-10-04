# Architecture

Status: **Phase 19** (Y-direction anchor + Y-column ring-seam transitions; prior:
full outer-perimeter lap + 45° diagonal entry + 45° Diagonal Hatch infill +
seam-trim ramp fix, smooth archimedean transitions, 3D viewer presets, track-gap
coverage, L-Bracket removed, Fermat ring-arc fix, About dialog + CSV export,
keyboard shortcuts, cancel + geometry persistence, print stats, recent files,
layer-height clamp, Fermat seam phase-shift, spiral Z, Torus removed, Conical
Dome promoted, PySide6 GUI, parameter save/load, packaging).

## 1. Layout

```
src/cfrp_core/            UI-free slicing library (no streamlit/plotly/Qt imports)
  __init__.py             public API: ProcessConfig, slice_part, from_legacy_dict, ...
  config.py               pydantic v2 models: ProcessConfig + GeometryConfig,
                          MaterialConfig, KinematicsConfig, GcodeConfig;
                          field/model validators = spec §4 hard errors; e_const
  constraints.py          resolve_process_constraints: spec §4 automatic adjustments + notes
  legacy.py               legacy flat sys_config dict -> ProcessConfig
  machine.py              MachineProfile (+ data/machine_profiles/default.json),
                          fit_trajectory_to_bed, check_bounds
  geometry/               _common (fillet, inset rings, resample, blend, dedupe),
                          outlines, archimedean, fermat, serpentine, laminate, revolve
  kinematics.py           speed planner (vectorised; exact accel/decel scans), lru-cached
  layers.py               anchor, plan_layer, seam trims, spiral Z, flat & revolve layer loops
  specialty.py            Mushroom overhang test (lru-cached, copies returned)
  gcode/writer.py         compile_gcode (Marlin-style, M1995 UV)
  validation.py           validate_trajectory (spec §13)
  pipeline.py             slice_part -> SliceResult
app.py, ui/, viewer.py,   legacy Streamlit front end: collects inputs, calls
presets.py                cfrp_core, renders. Removed in Phase 10.
tests/                    pytest + hypothesis; tests/golden = characterization data
```

## 2. Data model

- **Config:** `ProcessConfig` (frozen, hashable). Units in field names
  (`track_gap_mm`, `v_max_mm_s`, `purge_f_mm_min`). Built directly, from a
  nested dict (`build_config`), or from a legacy flat dict
  (`from_legacy_dict`). Invalid input raises `ConfigError(ValueError)` whose
  message is plain language (legacy wording).
- **Machine:** `MachineProfile` JSON; the bundled default is the former
  hard-coded 180 × 220 × 200 mm bed with a 1 mm margin.
- **Trajectory:** still `list[dict]` per point (Phase 1 keeps the shape):
  `x, y, z, dist, v, e_mult, pump_floored, layer_start, is_transition,
  layer, z_layer, cure_dwell`.
- **Result:** `SliceResult(config, trajectory, target_shapes, gcode,
  validation, max_z, constraint_notes, info)`. The core never prints or
  calls UI; notes (constraint adjustments, bed auto-positioning) are returned.

## 3. Call graph

```
slice_part(config, machine)
 ├─ constraints.resolve_process_constraints        -> (resolved config, notes)
 ├─ revolve:   layers.build_revolve_shell
 │               geometry.revolve_radius_profile / revolve_cross_section
 │               geometry.archimedean_path | fermat_spiral_path
 │               layers.add_lead_in_anchor (first printed layer)
 │               layers.plan_layer -> kinematics.calculate_trajectory
 │               layers._apply_spiral_z (continuous Z distribution)
 │               seam trim + interpolated transition (no Z ramp)
 ├─ specialty: specialty.specialty_shape -> overhang_test (mushroom)
 ├─ flat:      layers.build_flat_laminate
 │               geometry.base_polygon
 │               geometry.serpentine_infill  (Serpentine Infill shape)
 │             | geometry.printable_boundary + laminate_layer
 │                   -> archimedean_path | fermat_spiral_path | oriented_serpentine
 │               layers.add_lead_in_anchor (layer 1), plan_layer, append_layer
 ├─ machine.fit_trajectory_to_bed (+ shift target outlines)
 ├─ gcode.compile_gcode
 └─ validation.validate_trajectory -> machine.check_bounds, geometry.dedupe_path
```

Legacy UI: `app.main` -> `ui.sidebar.render_*` -> flat dict ->
`from_legacy_dict` -> `slice_part` -> `ui.results.render_results` ->
`viewer.render_3d_viewer`.

## 4. Behaviour-preservation notes (Phase 1)

- G-code is byte-identical to the baseline for all 28 golden cases;
  `x, y, z, dist` arrays are bit-identical; `v` and `e_mult` differ by at
  most 4e-15 (numpy `hypot`/`dot` in the vectorised corner limits).
- The accel/decel passes are kept as exact sequential scans: a closed-form
  (cumulative-minimum) version rounds ~1 ulp differently, which flips the
  pump-floor comparison at speeds exactly equal to `v_min` and changed a
  golden validation status. See `kinematics._limit_accel`.
- The pressure-advance look-ahead uses prefix sums + `searchsorted`, with
  near-exact ties re-resolved by the legacy loop.
- A few `ProcessConfig` fields are `int | float` (`min_radius_mm`,
  `layer_height_mm`, `anchor_length_mm`) because the legacy G-code header
  prints them raw ("0" vs "0.0"). Phase 5 normalises number formatting.
- Speed planner: 0.10 s at 100k points (legacy 0.73 s), 0.96 s at 1M.

## 5. Spec/code mismatches

1. **E-constant units.** The spec calls `e_const` "E-units per mm";
   `needle_dia[mm]·layer_h[mm]·E_mult·density[g/mm³] / weight[g/mm]` is
   dimensionless. E-units are calibrated in firmware; the formula works.
3. **Mushroom stem height is absolute**, not relative to the Z-offset (labelled
   clearly in the UI).
4. **Pump floor is unmeasured** (defaults to `v_min`): no real stall protection.
5. **Extrusion per mm scales with v/v_max** (a modelling assumption).

## 6. Known issues

*(No open slicer bugs — all known issues resolved through Phase 19.)*

## 7. Phase 2 behaviour changes

1. **Spiral Z-ramping** for all revolve shells (sphere, dome, cylinder, tube,
   taper, conical dome). Z increases linearly along each layer's path based
   on cumulative distance, eliminating the discrete Z-step at the seam. The
   old `append_layer` ramp is replaced by interpolated transition points with
   near-zero Z change.
2. **Torus removed.** It was a decorative looping curve with 360
   self-intersections, not a geometric torus.
3. **Conical Dome promoted** from a flat-speed specialty shape to the revolve
   pipeline, with its own radius profile `r = R(1 - progress)`. It now gets
   speed planning, multi-track walls, and spiral Z-ramping.
4. **Default part fixed.** Tracks per layer changed from 10 to 7 so the
   default 135 x 25 mm rectangle slices at startup.
5. **Mushroom stem height labelled** as absolute Z in the UI.

## 8. Safety net

- `tests/golden/`: 27 reference configs (torus removed); `tests/test_golden.py`
  requires byte-identical G-code and arrays within 1e-9. Regenerate only
  deliberately: `python -m tests.golden.regenerate [NAMES]`.
- `tests/test_kinematics.py`: hypothesis comparison against a verbatim copy
  of the legacy planner (random and tie-heavy grid paths).
- `tests/test_core_isolation.py`: imports every `cfrp_core` module and slices
  with streamlit/plotly/Qt/VTK imports blocked.
- `ruff`, CI on `windows-latest` / Python 3.12, `requirements.lock`.
