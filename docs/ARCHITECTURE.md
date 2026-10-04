# Architecture

Status: **Phase 0** (recon). This describes the code as it stands at the
start of the re-architecture; later phases update it.

## 1. What exists

A Streamlit app with physics and UI interleaved, in a flat module layout:

| Module | Role | UI coupling |
|---|---|---|
| `app.py` | Entry point; the three pipelines (flat, revolve, specialty) and their shared helpers (`_add_lead_in_anchor`, `_plan_layer`, `_append_layer`, `_finalize`, `_trim_*`) | `st.set_page_config`, `st.session_state`, `st.info` inside `_finalize` |
| `geometry.py` | Outlines, wall offset + fillet, Archimedean / Fermat / oriented serpentine / serpentine-infill generators, revolve cross-sections | none |
| `kinematics.py` | Speed planner (corner radius, heading, accel/decel passes, pump floor, pressure advance) | `@st.cache_data` |
| `specialty.py` | Mushroom, Torus, Conical Dome generators | `@st.cache_data` |
| `validation.py` | `resolve_process_constraints` (pre-slice) and `ValidationEngine` (post-slice) | none |
| `gcode.py` | G-code writer (Marlin-style, custom `M1995 P47 S{uv}` UV command) | none |
| `machine.py` | Hard-coded 180 × 220 × 200 mm bed, `fit_trajectory_to_bed`, `check_bounds` | none |
| `presets.py` | Default parameters, shape lists | none |
| `ui/sidebar.py` | Parameter panels; **also computes `e_const`** (physics in UI) | Streamlit |
| `ui/results.py`, `viewer.py` | Dashboard, Plotly 3D viewer, G-code tab | Streamlit / Plotly |

Data model: a single flat `sys_config: dict` (≈ 45 keys, merged from the
sidebar panels) flows through every stage. A trajectory is a `list[dict]`
with keys `x, y, z, dist, v, e_mult, pump_floored, layer_start,
is_transition, layer, z_layer, cure_dwell` (not every point has every key).

## 2. Call graph

```
app.main
 ├─ ui.sidebar.render_* ──────────────► sys_config (dict), e_const computed here
 ├─ validation.resolve_process_constraints(sys_config) ─► resolved dict + constraint_notes
 └─ app._run_slicing_pipeline(cfg, is_3d, layup, machine)
     ├─ [specialty] specialty.SpecialtyEngine.generate_specialty_shape(cfg)
     │               └─ generate_overhang_test(cfg)          (mushroom)
     ├─ [revolve]  app._run_revolve_pipeline(cfg, machine)
     │     per Z slice:
     │       app._revolve_radius_profile → GeometryEngine.get_revolve_cross_section
     │       GeometryEngine.generate_archimedean_path | generate_fermat_spiral_path
     │       app._add_lead_in_anchor (first printed layer)
     │       app._plan_layer → KinematicsEngine.calculate_trajectory
     │       app._append_layer → _trim_trajectory_tail, _trim_path_head (+ ramp)
     └─ [flat]     per layer:
             GeometryEngine.get_base_polygon
             GeometryEngine.generate_serpentine_infill            (Serpentine Infill shape)
           | GeometryEngine.get_printable_boundary + generate_laminate_layer
                 └─ generate_archimedean_path | generate_fermat_spiral_path
                    | generate_oriented_serpentine
             app._add_lead_in_anchor (layer 1), app._plan_layer, app._append_layer
     └─ app._finalize
           ├─ machine.fit_trajectory_to_bed
           ├─ gcode.GCodeCompiler.compile
           └─ validation.ValidationEngine.validate_trajectory
                 └─ machine.check_bounds, GeometryEngine._dedupe_path
 └─ ui.results.render_results → viewer.render_3d_viewer
```

Import graph (no cycles): `gcode → validation → geometry, machine, presets`;
`app → everything`; `kinematics`, `specialty` → `streamlit`.

## 3. Spec/code mismatches

`docs/SPEC_current.md` was written from this code, so it agrees with it by
construction except for these points of imprecision or design concern:

1. **E-constant units.** The spec calls `e_const` "E-units per mm".
   Dimensionally `needle_dia[mm] · layer_h[mm] · E_mult[-] · density[g/mm³] /
   weight[g/mm]` is **dimensionless**; the code multiplies it by travel in mm,
   so E is "mm of something" whose physical meaning depends on how the pump
   firmware interprets E. Needs the **[ASK] Q1** answer before Phase 2.
2. **Layer-height clamp is inconsistent.** The revolve pipeline clamps
   `layer_height ≥ 0.05` mm; the flat pipeline uses it unclamped (the
   pre-slice check only requires > 0). The spec mentions only the revolve clamp.
3. **Physics in the UI.** The spec places the `e_const` formula in the
   sidebar; it belongs in the core (moved in Phase 1).

## 4. Known issues (pinned by the golden tests, not yet fixed)

1. **Fermat layer transitions are near-vertical.** A Fermat layer ends on its
   return arm, and the next layer is the same path reversed, so both seam
   trims land on the same stretch of track and the ramp gets almost no
   horizontal run. Every layer transition of `cylinder_fermat` and the 2D
   Fermat cases exceeds the 30° ramp limit (validation warns). Needs a
   different seam strategy (e.g. rotate the next layer's start point).
2. **Fermat with 2+ islands:** the straight connector can cross printed track.
3. **Torus** is a decorative looping curve (360 self-intersections in its
   golden case), not a geometric torus. **[ASK] Q3.**
4. **Conical Dome** steps vertically between rings with no ramps. **[ASK] Q4.**
5. **Mushroom stem height is absolute**, not relative to the Z-offset. **[ASK] Q5.**
6. **Pump floor is unmeasured** (defaults to `v_min`): no real stall protection.
7. **Extrusion per mm scales with v/v_max** (a modelling assumption to verify).

## 5. Safety net

- `tests/golden/`: 28 reference configs (5 shapes × 3 patterns, layup
  `0,90,45,-45`, outer boundary on, serpentine-infill shape, 4 revolve
  shells + Fermat cylinder, 3 specialty shapes). Stored per case: exact
  G-code, trajectory arrays (`.npz`), validation result, input config.
  `tests/test_golden.py` requires byte-identical G-code and arrays within 1e-9.
- Regenerate only deliberately: `python -m tests.golden.regenerate [NAMES]`.
- `ruff`, `pytest`, `hypothesis`; CI on `windows-latest`, Python 3.12
  (`.github/workflows/ci.yml`), dependencies pinned in `requirements.lock`.
