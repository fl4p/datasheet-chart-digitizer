# Review-risk triage — worklist (proposal)

**Problem.** Human verification is the throughput bottleneck. Current batches are "everything
that changed / the whole pool," so charts *structurally identical to many previously
human-verified GREENs* (low risk) consume the same human attention as the few novel/high-risk
ones. Goal: route human eyes to the top of the risk distribution and **de-prioritize** familiar
low-risk charts — **without** recreating the anti-monotone false-PASS.

**Non-goal / hard invariant.** Triage NEVER sets `human_verified`. Auto-clear is a *distinct*
state (`auto_cleared_low_risk`), meaning "not shown to a human this round," not "verified." The
`human_verified` invariant is untouched.

---

## 1. Two components

**(A) Intrinsic risk** — from signals already emitted per extraction, no new detection:
- `trace_source` (raster/OCR ≫ vector), `status` (any non-`ok`), `score`, `curve_px` point count,
  `ytick_count`
- axis residuals `x_resid`/`y_resid`, `axis_calibration_trusted`
- diagnostics: `vpl_outside_expected_range`, `near_axis_top`, `clipped`,
  `shared_collapse_spans`, `graph_table_inconsistent`, any refusal reason
- **crossing-ness** (Ciss/Coss/Crss that intersect) — inherently high risk
- primary value outside its expected physical band (e.g. Vpl vs VGS axis)

**(B) Novelty / familiarity** — the new part. A **fingerprint** per chart:
`(vendor, chart_type, panel_layout_signature, axis_model[log|lin + decade span + tick count per
axis], curve_count, unit_set)`. Familiarity = count of **human-verified GREEN exemplars** with a
matching fingerprint AND matching extraction shape (same curve topology, same monotonicity class,
Vpl/anchor in the same band). High familiarity (≥ `k`, start `k=8`) = low novelty.

The exemplar set is the corpus of charts a **human** marked GREEN — never agent-GREEN (agent-GREEN
is not ground truth; §checklist).

---

## 2. Triage lanes

- **REFUSED** — `status` is a fail-closed refusal (unresolved / rejected_non_gate / axis_assumed /
  null-served). No per-instance human needed to confirm a null, but each refusal **class** gets a
  periodic once-per-class human spot-check (is the refusal itself correct?).
- **AUTO-CLEAR (low-risk)** — ALL of: `status=ok`; zero diagnostics; residuals under threshold;
  NOT a crossing/shared-span chart; primary value in-band; **AND** familiarity ≥ `k` positive
  exemplar matches. Recorded `auto_cleared_low_risk` + the matched exemplar ids. **~7% sampled
  into HUMAN-REVIEW** anyway (drift detection).
- **HUMAN-REVIEW (risk-sorted)** — everything else, highest intrinsic risk first. Batches become
  "here are the N that need you," not "here are 46, 40 of which you've seen."

---

## 3. Fail-safe rules (this triage is a GUARD — build it monotone)

1. **Absence of evidence → HUMAN-REVIEW, never auto-clear.** Unknown/under-populated fingerprint
   (novel vendor/layout/type, `< k` exemplars) → HUMAN. Missing/unparseable features (can't
   compute intrinsic or fingerprint) → HUMAN. A chart is auto-cleared only by a **positive**
   exemplar match, never by "no defect found."
2. **Monotone.** As any risk feature worsens, the lane moves monotonically toward HUMAN-REVIEW;
   there is no region where more risk flips back to auto-clear. Test the far tail explicitly.
3. **Crossing / shared-collapse charts NEVER auto-clear.** The Coss-snaps-onto-Ciss defect looked
   fine at normal scale and both agent lanes missed it — structural similarity is insufficient
   for crossings; they stay microscopic/human. (`[[crossing-approach-snap-check]]`)
4. **Any fail-closed diagnostic disqualifies auto-clear.**
5. **Auto-clear ≠ human_verified.** Distinct state; provenance says `auto_cleared_low_risk` with
   matched exemplar ids, never launders into `human_verified`.
6. **Sampled audit + drift kill-switch.** Audit ~7% of auto-clears against a human. If any
   false-auto-clear surfaces for a fingerprint class, that class **loses auto-clear eligibility**
   until re-calibrated. A guard never seen to catch a real defect is not trusted.

---

## 4. Calibration set — the known-bad it MUST route to HUMAN (guard-checklist #7)

Construct from this project's actual misses; if the triage would AUTO-CLEAR any of these, it is
broken and must not gate:
- the **3/30 sweep-GREEN** charts Fab caught by eye that both agent lanes passed
- **PSMN5R3** Coss-onto-Ciss approach-snap crossing
- **DI280** multi-panel Vpl (16.96 V off the avalanche panel)
- **FDPF190** `near_axis_top` / clipped-Qoss
- an axis-residual outlier (the tick-center ~1-3 px / +31 px cases)

Acceptance requires **zero false-auto-clears** across this set.

---

## 5. Rollout — shadow first, gate never-until-calibrated

1. **Shadow mode.** Compute `review_risk` + lane on the existing human-verified corpus. Measure:
   of human-GREEN charts, what % would auto-clear (the yield); of human-RED / defect charts, what
   % would auto-clear (**must be 0**). Report the confusion matrix.
2. Only after **0 false-auto-clears on the calibration set AND on the historical human-RED set**
   does auto-clear reduce a real human queue. Even then, keep the 7% audit + kill-switch.
3. Ship as a **dsdig library extension** (`review_risk` module) that consumes the already-emitted
   signals; the only new detection is the fingerprint + exemplar match. Per project rule, propose
   for inclusion, don't hand-roll one-off.

---

## 6. Output schema (per chart)

```
review_risk: {
  lane: "refused" | "auto_clear" | "human_review",
  intrinsic_score: float,                 # bounded, monotone in risk
  intrinsic_flags: [ ... ],               # which signals fired
  fingerprint: "<vendor|type|layout|axis|curves|units>",
  familiarity_matches: int,               # human-GREEN exemplars matched
  matched_exemplar_ids: [ ... ],
  auto_clear_reason | human_review_reason: str,   # explicit, never silent
  crossing: bool,
}
```
`human_verified` is NOT part of this and is never written by the triage.

---

## 7. Guard-review self-check (answer in the PR)

1. **Returns on un-evaluatable input?** → `human_review` (not auto_clear). ✔ by rule 1.
2. **Monotone?** → yes by rule 2; test the far tail (max residual, raster, crossing all set →
   must be human_review).
3. **Precondition checked?** → auto_clear gated on `familiarity ≥ k` *existing*, not assumed.
4. **Signature vs proxy?** → fingerprint covers extraction SHAPE + human-verified exemplars, not
   metadata alone or agent-GREEN.
5. **Persist a false verdict?** → no; auto_clear is a distinct de-prioritization state, not
   `human_verified`; round-trip the distinction.
6. **Provenance honest?** → `auto_cleared_low_risk` + exemplar ids; never claims human verified.
7. **Calibrated against known-bad?** → §4 set; zero false-auto-clears required.
8. **Fixing the check or the number?** → N/A; triage reorders attention, never alters extractions.

---

## 8. Acceptance gate

- Shadow confusion matrix on the human-verified corpus: **0 auto-clears of any historical
  human-RED / calibration-set chart**; report auto-clear yield on human-GREENs.
- All 8 guard-review answers written and passing.
- Crossing/refused/novelty rules unit-tested incl. far-tail.
- Dual-agent review + Fab sign-off before it gates any real queue. Until then, shadow-only.
