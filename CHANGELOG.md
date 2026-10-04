# Changelog

All behaviour changes are listed here. Refactors that keep the golden
outputs identical are listed under "Internal".

## [Unreleased]

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
