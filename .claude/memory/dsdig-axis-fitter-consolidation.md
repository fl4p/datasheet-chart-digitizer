---
name: dsdig-axis-fitter-consolidation
description: dsdig has ONE public axis-fit core now (numeric_axis.fit_axis_ticks); task
metadata: 
  node_type: memory
  type: project
  originSessionId: e33f161d-dbea-43e9-bab4-6103300d8c5f
---

datasheet-chart-digitizer had the linear/log tick→(pixel,value) fitter reimplemented
5–6× (numeric_axis, axis_calibration, capacitance_axis, breakdown_voltage `_fit_axis`,
reverse_recovery `_fit_linear`, gate_charge). Cleanup review 2026-07-16 (prompt: "any
duplicated code? are we overfitting?") mapped this as the top duplication cluster.

**Landed (main `c2d6bdf`, local only, NO PUSH):** Slice-A salvaged from the deferred
issue-#8 Zth worktree (codex-ee-root) and rebased onto aebca03. `numeric_axis` now exposes
the SUPPORTED PUBLIC core **`fit_axis_ticks(ticks: Sequence[AxisTick], name="axis")`**
(renamed from private `_fit_axis_ticks`); `fit_numeric_axis` is the text/context-parsing
wrapper that calls it. The core owns min-count/underdetermined refusal, strict monotonicity,
linear/log selection, candidate residuals, strict residual rejection. Consumers must use the
public name, never `_fit_axis_ticks`.

**Task #1 (partial):** fold the duplicate fitters onto `fit_axis_ticks`, preserving the
strictest gate, fail-closed on underdetermined ticks.
- DONE: `reverse_recovery._fit_linear` now delegates the least-squares to `fit_axis_ticks`
  (dropped its hand-rolled normal equations); keeps RR's own policy (>=3 min-count, linear-only
  via `model=="linear"` check, value-space 3% residual gate) + its `Axis` type so the manifest
  stays equivalent. Had to add exact-duplicate tick dedup (`dict.fromkeys`) — AO doubled text
  layers emit a tick twice, which the shared core's strict-monotone gate rejects (the old lenient
  fit absorbed it). Guarded by the AOT414 human-verified pins (all pass). Uncommitted.
- DONE: `breakdown_voltage._fit_axis` folded onto `fit_axis_ticks` (commit 4b782b3); transfer
  inherits it transitively via the shared `_calibrate`. Same pattern: >=4 min-count, post-hoc
  `model=="linear"`, 2% value-space gate, `LinearAxis` kept, exact-dup dedup. Two known-bad tests
  re-pointed at the fail-closed OUTCOME (capacitance chart now refuses at axis-type mismatch, one
  guard earlier than the curve-count guard); the curve-count guard's lost coverage RESTORED with a
  direct unit test (test_breakdown_voltage.CurveCountGuardUnit, 0/1/2 synthetic curves).
- PREREQ: Fab reassigned orphaned breakdown/transfer ownership to me (owner ee-ea41 left);
  ee-ea41's transfer-characteristics digitizer WIP salvaged as-is in commit ba01164 (green, 56
  tests, not deep-reviewed). `scripts/` (transfer analysis) + `uv.lock` left untracked, out of scope.
- DONE + GREEN-audited: `fit_axis_ticks` gained keyword-only `model: AxisModel` (=
  `Literal["auto","linear","log10"]`); auto = byte-identical incl legacy "no valid linear/log
  calibration" string; forced fits ONE candidate (skips the ambiguity gate) + keeps
  residual/min-count/monotonicity, fail-closed forced-log-nonpositive. RR + breakdown now call
  `model="linear"` (post-hoc checks dropped). This FIXED a real regression codex-ee-root caught:
  auto false-refuses a valid narrow-positive linear axis (values ~100-103) as ambiguous; the old
  np.polyfit accepted it. Pinned in tests/test_numeric_axis.py + breakdown [100..103] known-good.
- MULTI-AGENT HAZARD hit + recovered: while editing numeric_axis I collided with codex-ee-8ae6's
  concurrent uncommitted body-diode edits (log-aware _nearby_edge/_OUTER) in the SAME file. Fix:
  surgical text-revert of ONLY my lines (never git restore/checkout — would nuke its work), let it
  land (568cff0), then rebased my model param on the clean HEAD. [[multi-agent-workflow]]
- Follow-up still OPEN for codex-ee-root (separate scope, not urgent): exempt the auto ambiguity
  gate when the best candidate has ~0px residual (unambiguously that model).
- Final session stack on local main (NO PUSH, all GREEN-audited), on top of c2d6bdf: 75ec321
  overlay, 858f82a #3 refusal, 4888a9a #1-RR, ba01164 transfer-salvage, 4b782b3 #1-breakdown,
  (568cff0 = codex-ee-8ae6 body-diode), 2662e63 model-param, 06868f1 consumers-forced-linear.

**Also done same session (independent, uncommitted):** #3 = reverse_recovery dual-axis
refusal (see channel work); overlay dedup slice = new `overlay.py` (`draw_plot_frame`/
`draw_axis_ticks`) with `rdson_temperature` migrated byte-identical; breakdown/transfer/diode
adoption pending those files freeing up.

**Coordination:** other dsdig agents run as codex-ee-root (tile codex-7682, owns Zth #8),
codex-ee-8ae6 (tile codex-7683, body-diode), ee-review-4402 (tile host-7691, auditor). Direct
1:1 channel with codex-ee-root = `ax-eeroot`. Landing discipline: exact source/diff GREEN audit
before fast-forward, local-only, NO PUSH.
