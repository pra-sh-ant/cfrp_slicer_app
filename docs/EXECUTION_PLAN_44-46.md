# Execution plan: close Phase 44, then Phases 45 and 46

How the next three steps of `UPGRADE_PLAN_43-49.md` get built. The plan says
*what* each step delivers; this file covers the order, the checks that must pass
before moving on, and how to keep each slice cheap. Phase 47 comes after these
three because it needs hardware facts H2–H5 (UV spot, IR bulb, bed limits,
material temperatures). Those can be gathered while this work runs.

Owner: agent working autonomously (all permissions granted). One commit per
milestone on `refactor/phase-1`; nothing is pushed.

## Rules for every milestone

1. **Test first.** Write a failing test or sweep case before the code that
   fixes it. Property tests (hypothesis) get each shrunk failure pinned as an
   `@example`.
2. **Gate before commit.** A milestone is committed only when all of these pass:
   - `pytest -q -x` (the full suite, offscreen Qt included);
   - goldens unchanged (`tests/test_golden.py`). A golden changes only through a
     decision recorded in the CHANGELOG, followed by
     `python -m tests.golden.regenerate`;
   - `tools/` audits on the cases the milestone touches; the full sweep at the
     end of each phase;
   - `tests/test_core_isolation.py`, so `cfrp_core` stays free of Qt.
3. **The CLAUDE.md invariants hold.** One stroke, r_min enforced in all three
   places plus `StrictBends.repair`/`strict_climb`, the trajectory keeps its
   shape, the kinematics loops stay sequential.
4. **Run-time budget.** A new path step may not make any sweep case more than
   1.5× slower. Time it with the 60 × 40 four-hole plate as the reference. Use
   numpy and STRtree before GEOS loops, and cache per layer.
5. **Opt-in.** New behaviour is off by default until its sweep is clean, so
   fibre-only output stays byte-identical.
6. **Docs in the same commit.** That means the CHANGELOG, the plan's status
   table and the GUI help text.

## Step A: close Phase 44 (about 5 milestones)

| # | Milestone | Done when |
|---|---|---|
| A1 | **Recipe property test + fixes.** `tests/test_property_recipe_phase44.py` (written) found a self-crossing: plain 89 × 48 rectangle, 45° hatch, 1 edge ring, 2 layers, gap 1.5, r_min 1.0. Find the segment pair and fix it in `strict.smooth_join` or the ring/core cut. Then run 200 examples locally. | 200 local examples clean; failures pinned; CI keeps 10 |
| A2 | **Every bolt hole wrapped.** With layers ≥ 2 × holes, schedule the wraps so each bolt hole gets an into/out pair. Holes that can't be wrapped (diameter < 2 × r_min, or the only way in crosses fibre) are named with the reason. | Test on the 60 × 40 plate, 8 layers, 4 bolt holes: all wrapped, ≥ 540° each |
| A3 | **Fill slivers.** Lines into leftover slivers between zones, wherever a way in keeps r_min (`zones.py`). Reuses the Fill Pockets entry search. | The cavity check lists only spots in the ring centre or next to a hole (the restated target) |
| A4 | **Auto tries recipes.** `layers/auto.py` scores edge rings 0–2 × core × wraps by coverage, with crossings and r_min as hard rejects. Results are memoised per layer shape; the search stops early when coverage stops improving. | Auto ≥ the best manual recipe on 3 reference parts; slice time ≤ 3× a single recipe |
| A5 | **Per-hole canvas settings.** Ring count, bolt and drill per hole on the canvas, stored in `custom_holes` metadata. The comma lists stay as a fallback. | Qt test: set it on the canvas, it round-trips through the project, and the slice uses it |

Close-out: sweep with all recipe cases, CHANGELOG, status row → done.

## Step B: Phase 45, STL with mixed patterns per slice (about 6 milestones)

Ordered so that the cheap, high-value parts land first. Every milestone goes
through the Phase 44 recipe code, so no new path generator skips the r_min
checks.

| # | Milestone | Done when |
|---|---|---|
| B1 | **Recipe per slice and island.** `layers/stl.py` calls the recipe per island. Narrow walls (< 2 r_min + gap) get edge rings only. Extend the per-slice cache key with the recipe. | 25 meshes: no new Fail or crossing, coverage ≥ before |
| B2 | **Bores.** Holes that line up across ≥ N slices become bores. Hole rings go on them, and the wraps take turns between bores. | New bore test mesh passes |
| B3 | **Height ranges + load direction.** Config ranges (z0, z1 → recipe, layup, solid). The load angle sets the core angle. Config, capabilities and GUI go in one commit. | Flange-at-bottom mesh test; capability greying test |
| B4 | **Overhang map.** `mesh_slicer.py` support map. Fibre crosses an overhang only in straight anchored spans at overhang speed. Spans that are too long are dropped and reported. | Overhang test mesh: no unsupported curves; spans listed |
| B5 | **UV dose speed cap.** A per-point `v` cap applied *before* the speed planner, leaving its loops untouched. Off until dose, irradiance and spot are set. | Off: goldens identical. On: dose ≥ required at every point |
| B6 | **Material limits + property test.** Recipe follows the material's r_min, gap and cure speed. Property test on random extruded holed shapes. | Property test is clean; slowest mesh ≤ 2× the old time |

Risk: run time on big meshes. Mitigation: B1 sets a timing baseline in CI, and
each later milestone is checked against it.

## Step C: Phase 46, three workspaces (about 5 milestones)

GUI-heavy, so the rule is that **logic goes to `cfrp_core` first, the GUI
second**. Each GUI milestone is backed by core tests that run without Qt.

| # | Milestone | Done when |
|---|---|---|
| C1 | **Capability rules in core.** Move the ink greying out of `pcb_editor.py` into `capabilities.py` and add the workspace rules. | Capability tests for every rule, no Qt needed |
| C2 | **Project workspace field + migration.** `project.py` stores the workspace. Older files map to a workspace (structural/embedded → Structural PCB, and so on). Machine profile per printer. | Old project fixtures open in the right workspace |
| C3 | **Workspace shell.** `gui/workspaces.py`: shared layout (settings left, view centre, checks right, toolbar). The single 3D view is reparented to the active workspace. Fibre Printing first. | Offscreen Qt: only fibre sections; slicing works |
| C4 | **PCB Printing + Structural PCB.** Split `pcb_editor.py` into canvas and ink panel, with ink settings in sections. Laminate section for Structural PCB. | Offscreen Qt per workspace: sections, greying, slice |
| C5 | **Machine & Tools dialog + examples.** Shared machine and materials windows (filled in by Phase 47). A ready example per workspace. | Qt test opens each example and slices it |

Risk: `main_window.py` regressions. Mitigation: C3 keeps the old tabs reachable
behind a flag until C4 passes, then removes them in the same commit as C4.

## Order and parallel work

A1 → A2 → A3 → A4 → A5 → B1 … B6 → C1 … C5. C1 and C2 have no dependency on
Phase 45, so they can be built in parallel (a separate worktree) once Step A is
done. Meanwhile, the H2–H5 answers needed for Phase 47 should be collected.

## Stop conditions (when to come back to you)

- A golden would have to change.
- A plan target turns out not to be reachable as stated, as the strict-on
  cavity target was in Phase 44. The target is then restated, with numbers.
- A run-time budget can't be met without breaking an invariant.
