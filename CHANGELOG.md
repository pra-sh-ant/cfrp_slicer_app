# Changelog

All behaviour changes are listed here. Refactors that keep the golden
outputs identical are listed under "Internal".

## [Unreleased]

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
  `windows-latest`, 28 golden characterization cases, `docs/ARCHITECTURE.md`,
  `docs/SPEC_current.md`. Lint-only code fixes (import order, explicit
  `zip(strict=False)`, one statement per line); no behaviour change.
