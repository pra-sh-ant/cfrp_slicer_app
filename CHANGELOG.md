# Changelog

All behaviour changes are listed here. Refactors that keep the golden
outputs identical are listed under "Internal".

## [Unreleased]

### Added / Fixed (Phase 23 — robust STL slicing, multi-island strokes, coverage metric)
See `docs/SLICER_BENCHMARK.md` item 1. Golden outputs unchanged (no STL cases).
- **Fix: STL import was broken without scipy/networkx.** Trimesh's `section()` needs
  scipy and `fill_holes()` needs networkx; neither is a declared dependency, and
  the exceptions were swallowed, so every slice was dropped ("no valid slices") and
  non-watertight meshes crashed on load. Slicing is now done in numpy + shapely:
  triangle-plane segments (edge points always interpolated from the lower vertex,
  so shared edges join exactly), chained into rings, combined even-odd. Holes and
  every island are kept, in world XY. Mesh repair is best-effort.
- **Slice closing radius** (`geometry.stl_closing_radius_mm`, default 0.049 mm, as
  Orca's `slice_closing_radius`): closes small leaks in bad meshes.
- **Multi-island STL** (`geometry.stl_islands="all"`, GUI "Print All Islands"):
  every island of a slice is printed in one stroke. Each island is filled with a
  path that enters and leaves on its perimeter (Fermat for spiral infills,
  serpentine for line infills); connectors leave from the side facing the next
  island, retracing the outer track ring when a straight exit would cross laid
  fibre. Connector points carry `is_bridge`; validation reports bridged length.
  Default `"largest"` keeps the previous single-island behaviour.
- Fix: an STL layer whose Archimedean or Corner-Step track count did not fit was
  silently skipped (Corner-Step could yield an empty trajectory); it now falls
  back to as many tracks as fit. If no layer fills, slicing raises a clear error.
- Fix: an empty slice no longer collapses the Z of every slice above it.
- **Coverage metric** (`cfrp_core.coverage`): per-layer fibre coverage (void %) and
  tow overlap; area-weighted totals in `SliceResult.stats` and the GUI stats panel.
- Validation: new warnings for island bridges and for layers with no toolpath.
- Internal: `arc_along_ring` moved from `fermat.py` to `geometry/_common.py`.

### Changed / Fixed (Phase 22 — continuous helical seams for revolve shells)
- **Revolve shells are one continuous stroke** (vase-mode style): rings are linked
  by a forward helical connector (no trimming, no F300 `STRICT Z-SEAM LIFT` block,
  no direction reversal at seams). Z climbs linearly over each ring + connector,
  radius blends with a smoothstep, speed is planned once over the whole path.
- Single-track (single-ring) layers are exactly one helical lap each (no retraced
  lap, no false "path crosses itself" warnings); first/last layers stay whole.
- Fermat spiral on a revolve wall band narrower than 2×min radius + gap is
  replaced by Archimedean with an info note (the Fermat U-turn cannot fit).
- Flat-laminate layer ramps are tangent-continuous (cubic Hermite) instead of a
  straight chord.
- Validation: Z-aware self-crossing check; new curvature warning computed on the
  final bed-fitted trajectory (replaces the pipeline info note).
- GUI: retired slicer threads are kept alive until finished (fixes a hard crash
  when parameters change during a running slice).
- Performance: vectorized ring resampling; per-layer Archimedean path cache
  (full sphere 4.6 s -> 1.6 s cold, 0.7 s when only non-geometry params change).

### Added / Fixed (Phase 18 — full outer perimeter + 45° diagonal entry + 45° diagonal hatch)
- **Full outer-boundary trace**: outside-in archimedean spiral now opens with a
  complete closed lap of the outermost ring before stepping inward, guaranteeing
  full perimeter coverage on every even layer.
- **45° diagonal entry segment**: after the outer lap, a 3 mm bridging segment
  exits the perimeter seam at ~45° (bisector of ring tangent and inward normal)
  and lands on ring-1, replacing the previous abrupt radial step.
- **45° Diagonal Hatch** infill pattern: new option in the infill dropdown.
  Uses `oriented_serpentine` at +45° on even layers and −45° on odd layers for
  balanced cross-ply coverage.
- **Seam-trim ramp fix** (triangle and other narrow shapes): passes the previous
  layer's endpoint as `seam_hint` to inside-out `archimedean_path`, aligning the
  innermost-ring phase anchor with the outside-in seam so the layer-to-layer
  ramp trim cuts diverge correctly and the ≤30° ramp limit is always met.

### Fixed (Phase 12 — Fermat 2+ islands ring-arc fix)
- **Fermat connector no longer crosses printed rings** in non-convex shapes with
  2+ simultaneously-open child branches.  Root cause: the straight connector
  between child sub-regions exited the innermost printed A-ring boundary in
  non-convex geometries (e.g. L-bracket).
  Fix: new `_arc_along_ring(ring_poly, p_from, p_to)` helper routes the
  inter-child connector along the shorter arc of `a_rings[-1].exterior`
  instead of using a straight line; `visit_children` receives `innermost_ring`
  and applies the arc bridge between every pair of children (not before the
  first). When no rings exist yet (very small shapes), `poly` itself is used
  as a proxy ring.
- `fermat_spiral_path` docstring updated: known limit note removed.
- `docs/ARCHITECTURE.md`: Phase 12 status, known issues cleared.

### Added (Phase 11 — About dialog + trajectory CSV export)
- **Help → About**: dialog showing Python version, PySide6 version, and a
  one-line summary of completed phases.
- **Export CSV** button in G-code tab: writes the full trajectory as a CSV
  (columns: idx, x, y, z, v, dist, e_mult, layer, is_transition, layer_start).
  Enabled only when a trajectory exists; disabled until first successful slice.

### Added (Phase 10 — keyboard shortcuts)
- Ctrl+S → Save Parameters
- Ctrl+O → Load Parameters
- Ctrl+Shift+R → Reset to Defaults
- F5 → Slice Now (bypass debounce, force immediate re-slice)
- Ctrl+Q → Quit
- Escape → Cancel running slice (window-level shortcut, works without opening menu)
- All shortcuts shown in the File menu next to their actions.

### Added (Phase 9 — cancel button + window geometry persistence)
- **Cancel button** in the status bar: visible while slicing, hides on
  completion. Calls `SlicerWorker.cancel()` (cooperative flag) then
  disconnects signals so stale results are silently dropped.
- **Window geometry persistence**: `closeEvent` saves geometry via QSettings;
  `__init__` restores it on next launch (size, position, maximised state).

### Added (Phase 8 — print time, fiber length, fiber mass)
- `SliceResult.stats`: pre-computed dict of `print_time_s`, `fiber_length_mm`,
  `fiber_mass_g` (sum over non-transition trajectory points using `dist/v` for
  time and `fiber_weight_g_per_mm` for mass).
- Dashboard tab now shows Print time (h/m/s), Fiber length (m + mm), and
  Fiber mass (mg + g) below a separator after the geometry stats.

### Added / Fixed (Phase 7 — Recent Files + layer-height clamp)
- **Recent Files** submenu (File → Open Recent): remembers the last 8 saved/
  loaded `.json` parameter files via QSettings; includes "Clear Recent Files".
- **Layer-height clamp consistency**: `build_flat_laminate` now clamps
  `layer_height` to ≥ 0.05 mm (same as `build_revolve_shell`), preventing
  degenerate zero-height layers if a very small value slips through.

### Fixed (Phase 6 — Fermat seam phase-shift)
- **Fermat layer transitions now Pass validation** (previously Warning for all
  Fermat cases including `cylinder_fermat`).  Root cause: both the end of
  layer N and the start of layer N+1 landed on the outer ring at nearly the
  same angular position → XY gap ≈ `track_gap` → near-vertical ramp.
  Fix: pass `entry_hint = opposite(previous_layer_end)` to
  `fermat_spiral_path`, rotating the outer-ring phase-anchor ~180° each
  layer.  XY gap is now ≈ infill diameter, ramp angle ≈ 0.3°.
- Applies to flat laminates (`laminate_layer`) and revolve shells
  (`build_revolve_shell`) using Fermat infill.
- Golden data regenerated for all 6 Fermat cases; all now Pass.

### Added (Phase 3 — PySide6 desktop app)
- `gui_app.py` — entry point: `.venv312\Scripts\python.exe gui_app.py`
- `gui/` package: `main_window`, `controls_panel`, `viewer_gl`, `results_panel`,
  `slicer_worker` (slicing runs in a background QThread; UI stays responsive).
- Dark-themed PySide6 window: left parameter panel + right tabs (3D View /
  Dashboard / G-code). Slices automatically 500 ms after any parameter change.
- 3D viewer uses pyqtgraph OpenGL — fiber path coloured by speed (Turbo
  palette), layer isolation slider, print-bed mesh.
- G-code export via Save dialog.
- `pyproject.toml` `[gui]` extra: `PySide6 ≥ 6.7`, `pyqtgraph ≥ 0.13`,
  `PyOpenGL ≥ 3.1`.

### Changed (Phase 2)
- **Spiral Z-ramping** for all revolve shells (sphere, dome, cylinder, tube,
  taper, conical dome). Z increases continuously along each layer's path
  instead of stepping at the seam, eliminating the visible layer transition.
- **Conical Dome** promoted from a fixed-speed specialty shape to the revolve
  pipeline: now gets multi-track walls, speed planning, and spiral Z-ramping.
  Radius profile: r = R(1 - progress).
- **Default part** changed from 10 to 7 tracks per layer so the default
  135 × 25 mm rectangle slices successfully at startup.
- **Mushroom stem height** labelled as "Stem Top Height (absolute Z, mm)" in
  the UI for clarity.

### Removed
- **Torus (Z)** shape deleted (was a decorative looping curve with 360
  self-intersections, not a printable geometric torus).

### Internal
- Phase 1: all slicing logic moved into the UI-free `src/cfrp_core` package
  (golden outputs unchanged: G-code byte-identical, arrays within 4e-15).
  - Typed, frozen pydantic v2 config (`ProcessConfig` and sections) replaces
    the flat `sys_config` dict; validators carry the legacy error messages.
    `from_legacy_dict` converts old dicts.
  - `e_const` is computed by the core, no longer by the sidebar.
  - `st.cache_data` replaced by `functools.lru_cache` (copies returned).
  - Speed planner vectorised (≈7× faster); accel/decel scans kept exact.
  - Machine bed moved to a `MachineProfile` JSON (same 180 × 220 × 200 mm,
    1 mm margin default).
  - Bed auto-positioning is returned as a note instead of the core calling
    `st.info`.
  - Root modules `geometry.py`, `kinematics.py`, `specialty.py`,
    `validation.py`, `gcode.py`, `machine.py` removed; the Streamlit app is a
    thin front end over the core.
- Phase 0: git repository, `pyproject.toml` (ruff, pytest, hypothesis
  config), `requirements.lock` pinned for Python 3.12, CI workflow for
  `windows-latest`, 27 golden characterization cases, `docs/ARCHITECTURE.md`,
  `docs/SPEC_current.md`. Lint-only code fixes (import order, explicit
  `zip(strict=False)`, one statement per line); no behaviour change.
