# Changelog

All behaviour changes are listed here. Refactors that keep the golden
outputs identical are listed under "Internal".

## [Unreleased]

### Internal
- Phase 0: git repository, `pyproject.toml` (ruff, pytest, hypothesis
  config), `requirements.lock` pinned for Python 3.12, CI workflow for
  `windows-latest`, 28 golden characterization cases, `docs/ARCHITECTURE.md`,
  `docs/SPEC_current.md`. Lint-only code fixes (import order, explicit
  `zip(strict=False)`, one statement per line); no behaviour change.
