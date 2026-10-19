# Architecture

Status: **Phase 38** (layers package, per-layer plan with solid skins, typed
trajectory check, property tests, audits in CI, Streamlit front end retired to
`legacy/`; see docs/CHANGELOG.md for the history).

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
  layers/                 layer loops, one module per kind of part:
    common.py             KinematicsParams, lead-in anchors, plan_layer, seam trims, append_layer
    planning.py           plan_layers (pattern, fill, spiral direction per layer; solid
                          skins), stl_skins (exposed STL slices), fill track counts
    flat.py               build_flat_laminate (built-in outlines, Custom Outline)
    stl.py                build_stl_laminate (slices, fallbacks, mesh notes and markers)
    islands.py            one stroke over several pieces (STL islands, flat parts split
                          by holes): multi_island_layer, flag_bridges
    revolve.py            build_revolve_shell (helical shells, spiral Z)
  trajectory.py           TrajectoryPoint (the point dict's keys) + check_trajectory
  hints.py                fix_hint: plain-language fix for each check message
  project.py              .cfrpproj: settings + PCB design + ink settings + print mode
  profiles.py             named machine / fibre-resin / ink profiles (user folder)
  specialty.py            Mushroom overhang test (lru-cached, copies returned)
  gcode/writer.py         compile_gcode (Marlin-style, M1995 UV)
  validation.py           validate_trajectory (spec §13)
  pipeline.py             slice_part -> SliceResult
  capabilities.py         which settings / patterns apply to a setup, and why not
  diw/                    DIW structural electronics: design (BoardDesign, footprints,
                          sensors), config (DiwConfig), slicer, validation (DRC), gcode
  board.py                slice_structural_board: fibre laminate + DIW on top
legacy/streamlit/         retired Streamlit front end (own requirements + tests);
                          not installed or tested by CI
gui/                      PySide6 app: controls_panel (greyed settings), results_panel
                          (issues, dashboard, G-code), viewer_gl + toolpath_mesh +
                          range_slider (shaded tube viewer), pcb_editor (structural PCB)
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
  layer, z_layer, cure_dwell` (+ `is_bridge`, `is_anchor`; DIW adds
  `feature, extruding, width, height`). `trajectory.TrajectoryPoint` lists
  them; `check_trajectory` reports missing / unknown keys and bad values
  (used by the property tests).
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
 │               layers.plan_layers (pattern / solid / direction per layer)
 │               geometry.base_polygon, printable_boundary + laminate_layer
 │                   -> archimedean_path | fermat_spiral_path | oriented_serpentine
 │                      | corner_step_path
 │               layers.add_lead_in_anchor (layer 1), enforce_min_radius_clear,
 │               plan_layer, append_layer
 ├─ STL:       layers.build_stl_laminate
 │               mesh_slicer.slice_mesh_regions, stl_skins + plan_layers,
 │               _stl_layer_path (pattern fallbacks, islands in one stroke)
 ├─ machine.fit_trajectory_to_bed (+ shift target outlines)
 ├─ gcode.compile_gcode
 └─ validation.validate_trajectory -> machine.check_bounds, geometry.dedupe_path
```

GUI: `gui.controls_panel.get_params` -> flat dict -> `from_legacy_dict` ->
`slice_part` (worker thread) -> `gui.results_panel` (issues with
`hints.fix_hint`, dashboard, viewer).

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

- **Remaining curvature violations** (Rectangle/Circle Fermat, 10-11 points each):
  structural Fermat reversals; validation reports them as a "Curvature" warning.
- **Seam angle drifts** between revolve layers (ring phases are not aligned to a
  fixed angle); the helical connector hides the lift but the seam position moves.
- G-code writer breaks G2/G3 arcs at every layer start flag, so revolve G-code is
  larger than necessary.
- Transition (ramp) points are excluded from the curvature audit; on tiny
  single-track squares they run at `v_min` with R ~1.4 mm < min radius.

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

## 7b. Phase 22 — revolve shells as one helical stroke

`build_revolve_shell` collects each layer's min-radius-enforced ring, joins them
with `_helical_connector` (forward lap, smoothstep radius; `_skip_behind` trims
the next ring's head so the connector is short), collapses single-ring layers
into the connector, runs one speed plan over the whole path and assigns Z with
`_spiral_z` (linear in path length over ring + connector). Archimedean ring paths
are cached per cross-section (`_archimedean_cached`). `append_layer` (flat
laminates) now uses a tangent-continuous Hermite ramp.

## 8. Safety net

- `tests/golden/`: 27 reference configs (torus removed); `tests/test_golden.py`
  requires byte-identical G-code and arrays within 1e-9. Regenerate only
  deliberately: `python -m tests.golden.regenerate [NAMES]`.
- `tests/test_kinematics.py`: hypothesis comparison against a verbatim copy
  of the legacy planner (random and tie-heavy grid paths).
- `tests/test_core_isolation.py`: imports every `cfrp_core` module and slices
  with streamlit/plotly/Qt/VTK imports blocked.
- `tests/test_property_fibre.py`: hypothesis-generated outlines (convex shapes,
  plates with holes) x patterns x settings must keep the fibre rules (never
  over a hole, on the part, Z never down, spiral layers without crossings, a
  well-formed trajectory); refusals must be plain ValueErrors. Cases found by
  longer runs are pinned as `@example`s.
- Audits, also run by CI in quick mode (exit 1 on a failure):
  `tools/validation_sweep.py --quick` (shapes x patterns x settings),
  `tools/multilayer_audit.py --quick` (layer-to-layer planning),
  `tools/pcb_audit.py 12` (random structural boards in every print mode).
- `ruff`, CI on `windows-latest` / Python 3.12, `requirements.lock`.

## 9. Phase 38 — layer plan, skins, robustness

- `layers.plan_layers(config, n, skins)` decides each layer's pattern, fill and
  generator index (the index's parity sets a spiral's direction). Solid
  bottom/top skins use the First Layer Pattern. A run of Archimedean /
  Corner-Step layers ends inside-out before a zigzag (or Fermat) layer and
  starts outside-in after one; an odd run between zigzags ends with a Fermat
  layer (noted in `info`). With no skins and one pattern the plan is the old
  one (index = layer), so goldens are unchanged.
- STL skins: `stl_skins` marks a slice when it is within the skin count of the
  first/last slice or more than 20 % of it is uncovered by the next (top) /
  previous (bottom) slices.
- Holes: `stitch_rings(keepouts=...)` moves the lane-change seam off a hole
  (scoring the arrival jump when a seam hint is given), zigzag connectors that
  no single margin ring serves go round on a roadmap of the margin rings
  (`serpentine._Roadmap`) and are never allowed off the part, and
  `enforce_min_radius_clear` keeps the radius smoothing out of holes.
- Validation adds: fibre over a hole, Z going down, a layer climb over a hole
  or off the part (all errors).
