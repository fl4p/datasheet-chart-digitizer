# Changing, extending and benchmarking dsdig

Read this before changing dsdig code, adding a chart class, or benchmarking dsdig or
vision/LLM models on charts. Paths are relative to the dsdig repo
(`/Users/fab/dev/pv/ee/datasheet-chart-digitizer`) unless absolute.

## 1. Where to work

- **Branch from `main`, in a worktree.** The main checkout is shared, and its `.venv` is an
  editable install that other sessions use. Don't switch its branch for your work.
- **Tests.** Run from the worktree with the main checkout's venv, by absolute path; a
  worktree has no `.venv` of its own:
  `DSDIG_OCR_CACHE=<dir> /Users/fab/dev/pv/ee/datasheet-chart-digitizer/.venv/bin/python -m pytest -n auto --dist loadgroup`.
  `pyproject.toml` puts the worktree's `src/` first, so pytest tests the worktree's code;
  plain `python -c` does not. Compare the *failure sets* before and after, not the counts.
- **Commits.** Use one focused commit per root cause, with before/after evidence in the body.
  Never add a Claude `Co-Authored-By` trailer. Don't push unless asked.
- **Guards.** Any check, guard, validator or cache gets the written guard-review checklist
  in its commit body: unevaluable is not OK, monotonicity, preconditions, source of truth,
  persistence, provenance, known-bad calibration, and fix vs mute. A fix that turns a
  refusal into a *wrong served chart* is a regression. Prefer refusing or flagging over
  serving a trace you cannot verify.
- **Calibration.** Serve every axis from the rules its labels name: gridlines, or tick marks
  where there is no solid grid. Never serve it from label-glyph centres.
  `gridline_anchor.check_served_on_grid` checks the served mapping. Keep it on the path
  and tri-state (verified, failed, unverified).

## 2. Regression over Fab's verified charts: `tools/review_ab/`

Run it before merging any trace, calibration or finder change.
- `hv_ab.py` runs one side; `hv_cmp.py A B out.json` compares them chart by chart.
- A part or chart present on only one side is INCOMPLETE and exits 2. That is the only
  missing evidence `hv_cmp` catches:
  - Missing axes compare as `None`.
  - Curves with fewer than two samples are skipped.
  - A missing curve label shows up only in `curve_set_diff`, not in the failures.
  - Two empty trees compare as complete.

  So check that both sides are non-empty and comparable, and read `curve_set_diff`.
  Exit 0 does not mean the acceptance criteria below passed; apply them to the report.
- **Acceptance:** no status regressions, and no served value moving by more than 0.5 % of
  span. Any newly served real chart is checked against its PDF render and listed for Fab.

## 3. Synthetic charts with exact truth: `tools/synth_charts/`

A seeded generator of datasheet-style charts: vector PDF pages plus PNG crops, 12 chart
classes, four tiers. Every style and degradation factor is recorded per case. The GT is
the drawn geometry, with vector p95 ≤ 0.0002 % of span.
- **Uses:** a regression test for dsdig refusals and wrong serves. `baseline run` and
  `baseline score` break results down by class and factor.
- **Anti-overfitting:** check fixes on a fresh seed, not only the seed you tuned on.
- See `tools/synth_charts/README.md`.

## 4. Benchmark sets and the model harness (local: `out/vlm-chart2table/`, gitignored)

| set | GT | notes |
|---|---|---|
| `cases.frozen37.json`, `cases.json` | human-verified dsdig output | ≈0.5–1 px GT noise |
| `cases_vendor.json` | Infineon IPS, Taiyo Yuden and Murata vendor data | each chart gated at 0.1 % of span (Murata 0.35 %) |
| `cases_rdsvgs.json` | the RDS(on)-vs-VGS goldens | human-verified |
| `cases_synth*.json` | `tools/synth_charts` | exact |
| `cases_themes.json` | vector paths of uncovered vendor/era styles | gated at 0.5 px |
| `cases_themes_candidates.json` | raster tracings | pending Fab's review, not verified |

- **Scorer:** `score_cases.py`. It measures the forward-distance p95 as a % of axis span
  (log10 on log axes). A swap means strictly closer to another curve; ties are not swaps.
  - `frontier/inrange.py` restricts scoring to the GT x-range.
  - Every new set must pass three checks: a perfect answer scores 0 with no swaps; a 1 %
    shift scores about 1; swapped labels are flagged.
  - `score_cases_<tag>.csv` is overwritten per run, so copy it before scoring another set.
- **Model runs:** `frontier/run_frontier.py <model> <img|agent> --cases <file>`. The models
  are Astra via codex; Claude via `claude -p`; pi/Fireworks/Antigravity/Hugging Face via pi.
  - Each case runs in an empty `/tmp/chartbench/...` folder holding only `chart.png`.
  - `frontier/stats.py` gives time, tool calls and cost.
- **Safety, all mandatory:**
  - `frontier/audit.py` on every tag. Any tool call in image mode, or any other path,
    network access or package install in Python mode, disqualifies the chart.
  - The runner blocks package installs (`PIP_NO_INDEX`, `PIP_REQUIRE_VIRTUALENV`,
    `UV_OFFLINE`), after a model pip-installed into Homebrew's Python.
  - Every route has a 30-minute wall timeout per case. Only the pi routes are also capped at
    60 tool calls (200 for DeepSeek) and $0.75, because only their live logs expose calls and
    cost. Codex and Claude runs have the timeout alone. Capped or timed-out cases count as
    failures.
  - The pi-based Python mode has no network sandbox, so the audit is the only net check.
- **Results and caveats:** `docs/model-benchmark-2026-09.md`; per-run detail in
  `out/vlm-chart2table/frontier/README.md`.

## 5. Adding a chart class

1. Build it in dsdig with the output contract, status vocabulary and calibration check of
   the existing classes.
2. Produce a few human-verified overlay and value samples, with tick labels visible, and
   get Fab's approval before any batch use.
3. Add synthetic cases to `tools/synth_charts` and, where vendor data exists, gated vendor
   cases.
4. Freeze Fab-approved panels as golden fixtures. Re-bless changes explicitly.
