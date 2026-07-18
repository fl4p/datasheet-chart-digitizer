> **STATUS: SHELVED — explored, not pursued.** Two adversarial reviews established that
> auto-*accepting* digitizations (certifying correctness without a human) automates only a
> low-consequence minority while every high-value chart (Coss(V), Qrr, C(V) crossings) stays
> human, and carries a structural, unmeasurable false-accept rate (the "3/30" no-signal defect
> class, against a contaminated ground-truth corpus). The actual goal is a **display-queue
> filter** (`review-risk-triage.md`), not a trust oracle. Kept here for the analysis of *why* this
> path fails — do not build from it.

# Automated acceptance oracle — worklist (proposal)

**Goal.** Enable **auto-acceptance of a digitization without human review** when the extraction
is positively certified by *independent sources of truth* — so human review shrinks to
disagreements, novelty, the unverifiable residual, and a sampled audit. This is the half of
"mostly automated, minimal human review" that the review-risk triage does **not** cover: triage
decides *who looks*; the oracle decides *what can be trusted without a human*.

**Hard invariant.** Auto-acceptance is a **distinct** state (`auto_accepted_cross_validated`),
provenance-tagged with exactly which independent checks grounded it. It MAY substitute for human
review on the auto-accept lane, but it is **not** `human_verified` and never launders into it.

**The inversion this requires.** Today the pipeline uses table/physics/consistency signals to
**refuse** (fail-closed), never to **accept**. This oracle adds the accept path — but only on
*positive multi-signal agreement*, never on "no problem found."

---

## 1. Independent grounding checks (the "truth sources")

A check is *grounding* only if its truth source is **independent of the graph pixels** the
extraction read. Independence is the whole point — coincidental agreement across ≥2 independent
sources is exponentially unlikely for a wrong extraction.

**Class T — datasheet TABLE cross-check** (independent: printed spec-table values):
- C(V): table `Ciss/Coss/Crss @VDS` vs digitized curve value at that VDS (the anchor).
- gate-charge: table `Qg_total / Qgs / Qgd` vs digitized curve landmarks; `Qoss` vs ∫Coss dV.
- transfer: table `Vth(typ)`, `gfs` vs Id-onset / slope.
- RDS(on)-Tj / BV-Tj: table value at the reference Tj (25 °C) vs curve.

**Class P — physics / ordering / monotonicity** (independent: device physics):
- C(V): `Ciss ≥ Coss ≥ Crss` at every V; all monotone-decreasing in VDS; `Coss = Cds+Cgd ≥ Crss`.
- gate-charge: monotone-increasing Qg; exactly one Miller plateau, inside the VGS axis; plateau
  length ≈ Qgd.
- transfer: monotone Id above Vth; temperature ordering (Vth lower hot below ZTC; crossover at ZTC).
- values inside physical bands (Vpl∈[1,12] V, etc.).

**Class X — cross-chart / integral consistency** (independent: a *different* chart of same part):
- `Qoss = ∫ Coss dV` vs table/graph Qoss reference.
- `Qgd = ∫ Crss dV` over the plateau swing vs gate-charge Qgd.
- `Qg = ∫ Ciss` region vs gate-charge Qg.
- duplicate variants (`.gs` / `.cups` / raster) agree within tolerance.

**Class E — ensemble** (independent: a *different algorithm* on the same pixels):
- vector vs raster extraction agree; or a second tracer agrees. (Weaker independence than T/P/X —
  same pixels — so E alone never grounds; it only reinforces.)

---

## 2. Composition → confidence tiers

Per chart, run every applicable check; each returns `pass | fail | not_applicable` with a residual.

- **`auto_accept`** (no human): **≥2 checks from *distinct* independent classes (T/P/X) pass**
  within calibrated tolerance, **AND** zero physics (P) violations, **AND** no fail-closed
  diagnostic, **AND** (if E available) ensemble agrees. Record the grounding set.
- **`served_unverified`** (no human, flagged): extraction succeeds and passes all *applicable* P
  checks, but **< 2 independent groundings exist** (e.g. no table value and no cross-chart). Served
  with an explicit `unverified` confidence so downstream never mistakes it for certified. This is
  the honest residual — it can't be auto-accepted, and forcing it to human defeats "minimal
  review"; flag it instead.
- **`human_review`** (escalate): **any grounding check DISAGREES** (T/X/P conflict — the highest-value
  defect signal), or crossing/near-axis ambiguity, or novelty. Disagreement ≠ refusal: a conflict
  between two truth sources is precisely what a human must adjudicate.
- **`refused`**: fail-closed `status`.

Key: **a single passing check never auto-accepts.** One anchor hit can be coincidentally satisfied
by a wrong curve; two *independent-class* agreements cannot (cheaply).

---

## 3. Fail-safe rules (this is a GUARD — build it monotone)

1. **Absence of grounding → NOT accept.** No applicable T/X check, or checks `not_applicable`,
   → `served_unverified` or `human_review`, never `auto_accept`. Absence of evidence is not
   evidence of correctness.
2. **Disagreement outranks agreement.** If any independent check *fails* (conflicts), the chart
   goes to `human_review` even if others pass — a wrong extraction can pass one and fail another;
   the failure is the signal.
3. **Monotone.** More/worse violations only move toward human/refuse; no region where added
   evidence flips back to accept. Test the far tail.
4. **Ensemble never grounds alone** (same pixels). Physics-only can ground *refusal* but for
   *acceptance* requires pairing with T or X (physics can be satisfied by a plausibly-wrong curve).
5. **Crossings**: **every C(V) triple is crossing-class by construction** — never gate on a
   self-computed `crossing` flag (the approach-snap rides along without a clean intersection, so a
   flag can miss it, which is why both agent lanes did). A C(V) triple can NEVER `auto_accept`; it
   requires the microscopic intersection test. Note (correcting an earlier draft): a ride-along
   snap makes digitized `Coss ≈ Ciss`, so `Coss ≥ Crss` still holds and `Ciss ≥ Coss` is violated
   only on *overshoot*, not ride-along — **P does not catch a ride-along snap**, and a graph-only
   Qoss reference is not independent, so X may also miss it. Hence: crossings → mandatory
   microscopic/human, not oracle-accept. (`crossing-approach-snap-check`.)
6. **Auto-accept ≠ human_verified**; distinct provenance state; round-trip the distinction.

---

## 4. Calibration + must-catch validation (guard-checklist #7)

- **Tolerances come from human-verified ground truth**, not guessed: on the human-GREEN corpus,
  fit the T/X residual distribution for *correct* extractions; set the accept tolerance where a
  known-wrong extraction reliably exceeds it. A tolerance not derived from labelled correct/wrong
  pairs is unfounded.
- **Must route to human/refuse (zero auto-accepts):** the Coss-snap (PSMN5R3), the 3/30 sweep-GREENs
  Fab caught, DI280 multi-panel (Vpl off-axis → P violation), FDPF near-axis/clipped, and a
  crafted "wrong curve that hits one table anchor" (proves single-check acceptance is impossible).
  If the oracle auto-accepts any of these, it is broken.

---

## 5. Rollout — shadow first, trust never-until-proven

1. **Shadow.** Compute the oracle on the human-verified corpus. Confusion matrix: of human-GREEN,
   what % `auto_accept` (yield); of human-RED/defect, what % `auto_accept` (**must be 0**). Report
   per-chart-type yield and the `served_unverified` fraction (the honest "can't automate" share).
2. Auto-accept substitutes for human review only after **0 false-auto-accepts** on the validation
   set + historical human-RED set, per chart type. Roll out **per chart type** (C(V) with strong
   table anchors first; Vpl-only charts last / maybe never).
3. Keep a **sampled human audit** of `auto_accept` (~5-7%) + a **per-chart-type kill-switch**: any
   confirmed false-auto-accept disables auto-accept for that type until re-calibrated.

---

## 6. Output schema (per chart)

```
acceptance: {
  tier: "auto_accept" | "served_unverified" | "human_review" | "refused",
  grounding_checks: [ {class:"T|P|X|E", name, result:"pass|fail|n/a", residual, tolerance} ... ],
  independent_groundings_passed: int,     # count of distinct T/X classes that passed
  physics_ok: bool,
  disagreements: [ ... ],                  # non-empty => human_review
  confidence: float,                       # bounded, monotone
  reason: str,                             # explicit, never silent
}
human_verified: NOT written here; never set by the oracle.
```

---

## 7. Guard-review self-check (answer in the PR)

1. **Un-evaluatable input?** → `served_unverified`/`human_review`, never `auto_accept`. ✔ rule 1.
2. **Monotone?** → yes (rule 3); far-tail tested.
3. **Precondition checked?** → auto_accept gated on ≥2 *independent-class* passes actually existing.
4. **Signature vs proxy?** → groundings must be independent of the read pixels (table/physics/
   cross-chart); ensemble alone (same pixels) cannot ground acceptance.
5. **Persist a false verdict?** → auto_accept is distinct from human_verified; a wrong auto-accept
   is caught by the 5-7% audit + kill-switch, not written as verified.
6. **Provenance honest?** → tier + grounding set + residuals emitted; `served_unverified` explicitly
   says so; never claims certification it didn't earn.
7. **Calibrated against known-bad?** → §4 must-catch set; zero false-auto-accepts required.
8. **Fixing the check or the number?** → the oracle only *classifies*; it never edits an extraction
   to make a check pass. (If a future step auto-corrects, that is a separate change with its own gate.)

---

## 8. What this does and does not buy

- **Buys:** auto-acceptance of the well-grounded majority (C(V)/gate-charge charts with table
  anchors + physics + cross-chart integrals) → human review collapses to disagreements + novelty +
  the unverifiable residual + a small audit.
- **Does not buy:** zero human review. Charts with **no independent grounding** (a lone Vpl, no
  table, no cross-chart) can only be `served_unverified` — honestly flagged, not certified. And a
  seed of human ground truth + ongoing audit remain mandatory to calibrate and detect drift.

Pairs with `review-risk-triage.md` (volume) — this file is the trust mechanism (what's certifiable
without a human). Ship as a dsdig library module consuming already-emitted signals plus the new
cross-chart integral checks; propose for inclusion, don't hand-roll.

---

## 9. Acceptance gate

Shadow confusion matrix per chart type with **0 auto-accepts of any validation-set / historical
human-RED chart**; tolerances derived from labelled correct/wrong pairs; all 8 guard-review answers
written and passing; crossing/disagreement/independence rules unit-tested incl. the crafted
single-anchor-wrong-curve and Coss-snap far-tail; dual-agent review + Fab sign-off before it
substitutes for any human review. Until then, shadow-only.

---

## 10. Corrections from adversarial review (SUPERSEDE §1–§9 before build)

An adversarial pass broke the naive design. The through-line: guarding *independence of
truth-source* does NOT imply *joint coverage of the extraction* or *independence of the underlying
measurement*, and the human-GREEN corpus is not clean ground truth. Required tightenings:

- **C1 — coverage, not a point + a shape.** The ≥2 groundings must jointly constrain the curve
  **across its domain** (a multi-point / integral residual over the whole trace), not "one table
  anchor (T) + monotonicity (P)". Two curves through one anchor, both monotone and correctly
  ordered, diverge arbitrarily in the body. **The "coincidental agreement is exponentially
  unlikely" premise holds only for independent RANDOM errors; a systematic Y-scale/decade error
  shifts T, P, and X together.** Accept requires a bounded residual over the sampled domain, and at
  least one grounding must be a *distributed* (not single-point) constraint.

- **C2 — a check that is expected-applicable but returns `not_applicable` = disagreement, never
  neutral.** A wrong-enough extraction *degrades* a check (can't locate the plateau → P goes
  `n/a`) rather than failing it; §2's escalate-on-fail then silently drops it. Worsening the input
  must never flip `fail → n/a → accept`. Each chart type declares its *expected-applicable* checks;
  any expected check that returns `n/a` routes to `human_review`.

- **C3 — groundings must depend on DISJOINT extracted quantities.** Source-label independence is
  insufficient: gate-charge T (`table Qgd` vs plateau) and P (`plateau length ≈ Qgd`) both read the
  same plateau landmark; transfer T (`Vth` vs Id-onset) and P (monotone above Vth) both hinge on
  Id-onset. Mis-locate the landmark → both move together = one measurement counted twice. Require
  the two groundings to rest on disjoint measured features.

- **C4 — X grounds only against a TABLE scalar, never a co-digitized graph** (a graph Qoss is
  pixel-derived by the same extractor = correlated error). And integrals are area-dominated
  (low-VDS region) so tail/shape errors barely move them — pair any integral with a tail/multi-point
  residual.

- **C5 — the must-catch set is a FLOOR, and each entry needs a named emitted signal first.** For
  each must-catch defect, enumerate the *concrete emitted feature* the oracle trips on **before**
  claiming it as an acceptance criterion. The **3/30 sweep-GREEN** axis/box/tick defects passed
  `status=ok` + low residuals + both agent lanes + the checklist — they have **no distinguishing
  emitted signal**, so they CANNOT be the acceptance bar and their class stays **mandatory-human**
  until a real signal exists (do not tune the gate to overfit five examples). Also validate on a
  **held-out** human-RED set never used to set tolerances; report its false-accept rate.

- **C6 — the exemplar/tolerance corpus is unvetted and self-widening.** Tolerances fit from a
  human-GREEN corpus that this project's own history shows is contaminated
  (`dsdig-sweep-green-axis-integrity-retro`; `transfer-review25-verified`: "do NOT fit from the
  20260715 packet"). A wrong-but-GREEN chart has a *larger* residual, so a raw fit **widens** the
  accept tolerance (monotone the wrong way). Pin an **authoritative, versioned** exemplar corpus;
  **audit the exemplar base itself** (not just auto-accepts); use a robust/quarantined tolerance
  fit.

- **C7 — most chart types have NO grounding; `served_unverified` must be hard-non-consumable.**
  No T/P/X exists for body-diode VSD, Qrr (feeds N_TAU fits), Zth (known human-RED, issue #8),
  gfs/gm (#9), SOA/avalanche. Zth/gfs → **`refused`**, not served. `served_unverified` gets an
  explicit **downstream hard-gate contract**: fits/consumers MUST refuse it (the whole honesty of
  the flag depends on this, and it is a required companion change, not optional). Gate-charge note:
  a curve-switch can leave the Vpl scalar correct yet the curve RED — P-only is known-insufficient
  there.

- **C8 — audit power + kill-switch dimension.** 7% uniform sampling has poor power for a rare
  systematic error (a 1/500 defect needs ~7000 accepts before detection). Risk-stratify: oversample
  first-N of every new fingerprint and near-threshold accepts. The kill-switch must key on the
  **failure's actual dimension** (e.g. "all log-log C(V)"), not only the fingerprint tuple, or
  sibling classes stay live.

- **C9 — precedence vs triage.** `auto_cleared_low_risk` (triage) measures *typicality, not
  correctness* and has zero independent grounding. It is **NOT a consumability signal**. Nothing
  skips a human on the triage lane unless it ALSO clears this oracle's grounding bar. Document that
  contract in both files.

These corrections gate the build: §1–§9 as originally written are unsafe without them.

---

## 11. Geometry is measured, not eyeballed; the artifact must show the discrepancy; route lanes by modality

- **Geometric groundings are CV MEASUREMENTS, not LLM/human visual judgments.** Tick-center vs
  printed tick, box-owns-its-frame, exact crossing coordinate, monotonicity/ordering — compute
  these from the image (pixel positions, curve geometry) and compare to source with an explicit
  tolerance. LLMs of **any** family at **any** zoom *estimate* geometry — they answer "looks
  aligned," never "within N px." The pass/fail is the measurement; visual review is corroboration
  only. Evidence: FDPF190 was missed **with the 5× crop present** — the fix was a measured per-tick
  exact-center assertion (`cap-axis-tick-centers`), not more zoom.

- **The review artifact must expose SOURCE vs EXTRACTED as two separate marks.** Never render the
  extracted marker at its own inverse-fit *predicted* position — that draws the extraction on
  itself, so upscaling shows perfect self-agreement (the mute-button trap: the reviewer, LLM or
  human, becomes MORE confident in a wrong calibration). The crop overlays the **source printed
  tick/curve AND the extracted position**; the visible gap (and the measured residual) is what's
  judged.

- **5×/8× upscaling helps resolution-limited defects ONLY** — a sub-perceptual offset becomes
  visible (this is why the crossing microscopic check catches ride-alongs). It does NOT catch:
  attention/rigor failures (a reviewer who doesn't check — see FDPF), estimation-vs-measurement
  gaps (still "looks close"), or semantic misreads (an internally-consistent axis mis-scale, e.g.
  `10²`→102, renders a perfectly-aligned tick crop while the number is wrong). Treat the crop as
  corroboration, never the gate.

- **Route review lanes by MODALITY, not just vendor.** Geometry → CV/measurement (authoritative).
  Identity / plausibility / curve-binding ("right chart? right temperature bound? sane values?") →
  **≥2 DIFFERENT capable multi-modal LLMs**; cross-family consensus is meaningful and decorrelates
  blind spots (this session: GPT/codex caught Claude's FDPF over-call; Claude caught the over-null
  risk). Ungrounded or CV-vs-LLM disagreement → human. Caveat: model diversity reduces
  *uncorrelated* misses but NOT the class all current models share (the 3/30 fine axis/tick) — that
  stays CV-or-human. Vet each LLM lane on a labelled set (a weak-vision lane adds false-REDs); keep
  the count small (2–3).
