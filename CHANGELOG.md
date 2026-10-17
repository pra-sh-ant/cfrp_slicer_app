# Changelog

All behaviour changes are listed here. Refactors that keep the golden
outputs identical are listed under "Internal".

## [Unreleased]

### Fixed (Phase 25 — curvature: real kinks removed, audit made exact)
Sweep (`tools/validation_sweep.py`, 1182 cases): Pass 95 → 451, crossings 60 → 16,
steep ramps 33 → 6, no crashes. Goldens regenerated deliberately (see bottom).
- **`enforce_min_radius` was creating kinks**: arcs built at exactly r_min read
  2.99999… and were "smoothed", bending their joins to R ≈ 1.6 mm (every Corner-Step
  corner; also crossings). It now ignores radii within 0.1% of r_min.
- **Curvature audit measures bend over 1.5 mm of tow** (`local_bend_radii`):
  length / turning within the window. Exact on arcs at any sampling; a corner after
  a long straight still reads tight; a coarse polygonal arc resampled finely no
  longer reads as a tight bend.
- **Corner-Step on polygons**: ring-to-ring steps are lane changes of length
  1.5·√(6·gap·r) on one seam (middle of the longest straight), so they bend at
  ≥ r_min and never drift onto corners; outward (odd) layers keep the winding
  instead of reversing the fibre at the seam. Circle/hexagon/triangle multi-layer
  now pass. Rectangles keep the analytic path; its odd layers are still reversed.
- **Boundary wall hand-over**: the wall seam is the projection of the previous end
  (or the middle of the longest side), not the nearest polygon vertex (usually a
  corner), and wall↔infill steps are the same lane change. Fermat and Archimedean
  enter at the wall's seam in its direction.
- **Fermat on revolve shells** switches to Archimedean whenever gap < 2·r_min (its
  reversal joins adjacent tracks, ≤ gap/2 radius, on every layer — the old
  wall-band test did not capture that). Flat Fermat gets an explanatory note.
- **Ramp head extension** cuts inside long segments (it skipped to the next vertex,
  e.g. 120 mm away on a Corner-Step rectangle) and is capped at two seam-trims, so
  an impossible ramp (thick layer, shallow angle) is still reported. Reversal seams
  start the ramp at the tail cut point.
- **Coverage metric** repairs invalid tow strips and can no longer fail a slice.

Remaining curvature is physical: serpentine/hatch and Fermat U-turns join adjacent
tracks (radius ≤ gap/2) whenever gap < 2·r_min; the constraint notes say so.

Golden changes: G-code changed for `*_fermat`, `*_serp`, `layup_*_serp`,
`rectangle_arch`, `hexagon_arch`, `triangle_arch`, `rectangle_arch_boundary` (smoother
seams). Now Pass: `circle_arch`, `rectangle_arch`, `rectangle_arch_boundary`,
`sphere_arch`, `dome_arch`, `taper_arch`, `conical_dome`. Serpentine counts rise
(every point of their 0.6 mm U-turns is counted now).

### Fixed (Phase 24 — validation sweep: every shape × infill × key setting)
Found with `tools/validation_sweep.py` (1182 cases from GUI defaults). Before:
406 Pass / 702 Warning / 42 Fail / 32 config errors, and two checks were blind.
Goldens regenerated deliberately (see bottom).
- **Fail: empty trajectory, square tube with 1 track** (every size). The hole kept
  sharp corners inside ~5 mm outer fillets, thinning the wall to 0.8 mm at the
  corners so no ring passed the hole clearance. Hole corners are now concentric
  with the outer ones (`r_outer − wall`).
- **Fail: empty trajectory, revolve wall wider than the radius allows** (e.g. 7
  tracks at R 10 mm). The track count is now capped to what fits at min bend
  radius, with a constraint note; a part too small for one track raises a clear
  error, as does a shell where no layer fills.
- **Missing layers mid-shell** (tapered/conical, 1 track). pyclipper occasionally
  returns a micro self-intersecting offset; `fillet` grew it into a spike that hit
  the hole and dropped the ring. Invalid clipper output is now repaired.
- **Square tube connector cut the corners**: the helical connector lapped a circle.
  For non-round shells it now follows the rings' own outline (polar blend), and
  their spiral pairs ring points by angle (`resample_polar_ring`), which cannot
  cross for nested star-shaped rings. Round shells unchanged (byte-identical).
- **Fermat layers started across the part**: the 180° seam phase-shift left the
  jump from the previous layer as a straight chord at layer Z under every ring.
  Layers now enter where the last one ended, choosing the winding (`mirror`) that
  continues its direction instead of reversing.
- **Zigzag (serpentine / 45° hatch) entries cut across their own lines**: the
  layer now starts at the zigzag corner nearest the previous end (or the wall's
  end), routed along an edge corridor if the straight jump would still cross.
- **Zigzag U-turns**: were circular caps fitted to the chord (90° kinks at the
  joins; wrong-way bulges at slanted/curved edges). Now a straight level-off plus a
  semicircle of radius gap/2 (`_uturn`): tangent-continuous, never crosses its
  neighbours. Line ends are eroded by `max(r_min, gap)` so turns stay inside.
- **Steep layer ramps** (Corner-Step, short seams): `append_layer(min_run=...)`
  moves the head cut until the ramp has the horizontal run the transition angle
  needs; reversal seams (turn > 90°) use a straight ramp instead of a hairpin.
- **Validator: self-crossing check was blind and noisy.** It counted repeated points
  only when `is_simple` failed (false positives on seam overlaps, missed real
  X-crossings). It now counts segment pairs crossing at ≥ 20° (shallower overlap is
  reported by the coverage metric).
- **Validator: curvature audit was blind at corners after long segments.** The
  three-point circumradius grows with leg length (a 90° corner after a scan line
  read ~25 mm). Now `local_bend_radii`: ½·min(leg) / sin(θ/2) on the de-slivered
  path, 1% slack for arcs at exactly r_min.
- New constraint note for line patterns with gap < 2·r_min (U-turns are at most
  gap/2 radius — physics, not a defect).

After: 0 Fail; real crossings 999 → 60 (degenerate 135×25 triangle corners, Fermat
centre); steep ramps 252 → 33; curvature warnings now appear where they are real
(serpentine/hatch U-turns, Fermat centre, Corner-Step corners, wall joins).

Golden changes: paths changed for all `*_fermat`, `*_serp`, `layup_*_serp`,
`triangle_arch` (seam/entry/U-turn fixes) and `square_tube_arch` (corner fix:
9963 → 12739 points, full 3 rings through the corners). Archimedean and revolve
G-code is otherwise byte-identical; their validation messages changed only through
the stricter audits (1–6 tight points each; `rectangle_arch_boundary` 38 at the wall
join; `conical_dome` loses a false "crosses itself").

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
