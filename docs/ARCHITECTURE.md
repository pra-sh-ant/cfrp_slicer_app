# Architecture

Status: **Phase 1** (core extracted, behaviour-identical). Phase 0 recon is
kept below where still accurate.

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
  layers.py               anchor, plan_layer, seam trims + ramp, flat & revolve layer loops
  specialty.py            Mushroom / Torus / Conical Dome (lru-cached, copies returned)
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
 │               layers.append_layer -> trim_trajectory_tail, trim_path_head, ramp
 ├─ specialty: specialty.specialty_shape -> overhang_test | torus | conical dome
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
   dimensionless. **[ASK] Q1** before Phase 2.
2. **Layer-height clamp is inconsistent:** revolve clamps to ≥ 0.05 mm; flat
   uses it unclamped (only > 0 is required).
3. **Physics in the UI:** fixed in Phase 1 (`ProcessConfig.e_const`).

## 6. Known issues (pinned by golden/UI tests, not yet fixed)

1. **Fermat layer transitions are near-vertical.** A Fermat layer ends on its
   return arm and the next layer is the same path reversed, so both seam
   trims land on the same track and the ramp gets almost no horizontal run.
   Every transition of `cylinder_fermat` and the 2D Fermat cases exceeds the
   30° ramp limit (validation warns).
2. **Fermat with 2+ islands:** the straight connector can cross printed track.
3. **Torus** is a decorative looping curve (360 self-intersections in its
   golden case), not a geometric torus. **[ASK] Q3.**
4. **Conical Dome** steps vertically between rings with no ramps. **[ASK] Q4.**
5. **Mushroom stem height is absolute**, not relative to the Z-offset. **[ASK] Q5.**
6. **Pump floor is unmeasured** (defaults to `v_min`): no real stall protection.
7. **Extrusion per mm scales with v/v_max** (a modelling assumption).
8. **The default part can't be sliced.** The legacy defaults (135 × 25 mm
   rectangle, 10 tracks, 1.2 mm gap, 3 mm radius) fit only 7 tracks, so the
   app opens on "Slicing failed". Fixing it changes defaults (behaviour).

## 7. Safety net

- `tests/golden/`: 28 reference configs; `tests/test_golden.py` requires
  byte-identical G-code and arrays within 1e-9. Regenerate only deliberately:
  `python -m tests.golden.regenerate [NAMES]`.
- `tests/test_kinematics.py`: hypothesis comparison against a verbatim copy
  of the legacy planner (random and tie-heavy grid paths).
- `tests/test_core_isolation.py`: imports every `cfrp_core` module and slices
  with streamlit/plotly/Qt/VTK imports blocked.
- `ruff`, CI on `windows-latest` / Python 3.12, `requirements.lock`.
