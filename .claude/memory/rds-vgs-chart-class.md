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

Review round 1 (2026-09-28, Codex xhigh + Opus visual) found the clipping,
leader, gap, coverage, temperature-kind and validation-scope defects; fixed in
429806d..4bbf9ad with known-bad tests in tests/test_rdson_gate_voltage_review.py.
v2 output: `batch15_v2/`, `batch15_v2_manifest.json`, `CHANGELOG-v1-v2.md`
(v1 kept: the reviews cite it). The review loop continues until a round has no
new findings. `test_annotate_pdf::test_csd13385...` asserts the extractor tree is
DIRTY -- it fails on any clean checkout, unrelated to this class.

Review round 2 (Codex + Opus on v2): R2-1..R2-8 fixed in f3f991e/c09bcf2;
v3 output `batch15_v3/`, `batch15_v3_manifest.json`, `CHANGELOG-v2-v3.md`.
`tools/rds_vgs_mutation_check.py` disables each review fix in turn (23
mutants, all killed; log in out/rds_vgs_mutation_check.log) -- rerun it after
any change to the class: Codex showed round-1 tests passed with fixes off.

Review round 3 (Codex + Opus on v3, plus Fab's own v3 overlay pass: R3-1..R3-14)
fixed in 7c2160e..9905d9d; v4 output `batch15_v4/`, `CHANGELOG-v3-v4.md`.
Fab human-verified 11 v3 panels (all but RQ3E110AJ, BRCS020N03RA, FDP8870,
RQ3E180AJ); they are frozen goldens in `tests/fixtures/rds_vgs_golden/`
(PROVENANCE.md; SHA-256 per PDF; skip loudly, never pass, on a missing PDF).
Change a golden only via REBLESSED.json with a note -- never edit the fixture.
The mutation harness (105 mutants + 2 documented equivalents) runs the goldens
with every mutant. Tick labels of the Rohm image charts are OCR, not text layer;
FDP8870's tails are coincident in the source PDF (drawings 716/719).

Round 4 (Fab's v4 review, F4-1..F4-5, 2026-09-29): 46a0309..dff9261; v5 in
`batch15_v5/`, `CHANGELOG-v4-v5.md`. Raster steep heads are row-traced to the
frame, branches take over their shared tail (never served alone), touching
line pairs split into halves only on clear evidence, arrows/leaders followed
straight to their tip (tip in touching lines names neither). Overlay: v3 tick
style (Fab rejected the R3-10 bands -- do not re-add unrequested rendering),
nested solid widths. BRCS020N03RA added as golden; WSR3090, RQ6E080AJ and
BRCS020N03RA sit in `PENDING.json` awaiting Fab's re-bless -- never clear it
yourself.

Round 5 (Fab's v5 review, F5-1..F5-3, 2026-09-29): aaa58fc..6c23a9a; v6 in
`batch15_v6/`, `CHANGELOG-v5-v6.md`. Fab rejected honest "unknown" where the
page answers it: IDs/temperatures are now bound by physical RDS order
(`id_order_rule` / `temperature_order_rule`, guarded: label count = curve count,
none bound otherwise, other parameter shared, >= 3 px over >= 5 columns, no ID
crossing). Rohm "Ta=25°C" boxes are read as units (subscript read alone);
labels crossed by a grid rule via a rule-erased OCR pass. Traces are tubes whose
5 px core shows the print (F5-2: "i dont see the original curves"). Raster right
ends are traced to the frame (the tracker keeps 3 px clear; frame stroke bridged only
to ink seen past it). Open: WSR3090 kind/c0 end/state wording (CHANGELOG "Refusals
left in v6"); 5 repo-wide test failures in other classes predate round 5.

**Why:** a later session will find the branch and the batch and must not treat
`ok` as human-verified or batch the class further.
**How to apply:** no further batch use before the Codex review and Fab's overlay
verdicts. Known weak spots: raster column tracking (thin steep near-threshold
curves, merged curves, leader remnants), raster label OCR/binding. Tesseract must
run with OMP_THREAD_LIMIT=1 (set by the CLI) -- see
`~/dev/kb/tooling/tesseract-lstm-readings-vary-between-runs-unless-omp-thread-limit-is-1.md`.
Related: [[viz-review-own-overlay-pass-first]], [[dsdig-trace-fidelity-visual-gate]].
