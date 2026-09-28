---
name: rds-vgs-chart-class
description: dsdig digitize-rds-vgs (RDS(on) vs VGS) on branch feat/rds-vgs, unmerged; batch-15 from the seeded solar-charger order awaits independent Codex review and Fab's overlay pass
metadata:
  type: project
---

Built 2026-09-28 in worktree `~/dev/ee/dsdig-rds-vgs`, branch `feat/rds-vgs`
(unmerged, unpushed). Modules `rdson_gate_voltage*.py` + `rdson_spec_table.py`,
CLI `dsdig digitize-rds-vgs charts.json|--pdf ... --out DIR`, tests
`tests/test_rdson_gate_voltage.py` (real PDFs in `~/dev/ee/solar-charger-eval/ds`).
Not wired into `annotate` on purpose (unvalidated class; annotate output is served).

Batch 15 (Fab's explicit request, seed 20260928 order): deliverables in
`~/dev/ee/solar-charger-eval/rds-vgs/` (`batch15_manifest.json`, `skipped.json`,
`REVIEW-GUIDE.md`). 8 panels ok+verified (TI x5, IR x2, Vishay; TI charts equal
their table typ to <1 %), AO3400A (UMW) `inconsistent` = a real table/chart
disagreement, 6 review_required (Rohm x3, BRCS, WSR3090 raster; FDP8870 filled
outlines with unbound ID labels).

**Why:** a later session will find the branch and the batch and must not treat
`ok` as human-verified or batch the class further.
**How to apply:** no further batch use before the Codex review and Fab's overlay
verdicts. Known weak spots: raster column tracking (thin steep near-threshold
curves, merged curves, leader remnants), raster label OCR/binding. Tesseract must
run with OMP_THREAD_LIMIT=1 (set by the CLI) -- see
`~/dev/kb/tooling/tesseract-lstm-readings-vary-between-runs-unless-omp-thread-limit-is-1.md`.
Related: [[viz-review-own-overlay-pass-first]], [[dsdig-trace-fidelity-visual-gate]].
