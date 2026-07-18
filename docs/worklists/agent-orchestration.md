> **STATUS: SHELVED — explored, not pursued.** Multi-agent orchestration (CV + diverse-LLM lanes +
> consensus aggregation) is scoped to the auto-*acceptance* pipeline, which was shelved (see
> `auto-acceptance-oracle.md`). The actual goal — a display-queue filter that hides low-risk
> near-duplicates (`review-risk-triage.md`) — needs none of this fleet machinery. Kept for the
> orchestration guard-lessons (dead-lane-≠-pass, run-must-terminate, calibration-pinned-to-
> extractor-version) which remain valid if a fleet is ever run.

# Agent orchestration — worklist (proposal)

**Scope.** `review-risk-triage.md` and `auto-acceptance-oracle.md` define *what a correct review
decides*. This file defines *how a fleet of agents (CV + diverse multi-modal LLMs + human)
produces that decision reliably, cheaply, and terminably.* The orchestration layer has its **own**
anti-monotone traps — a dead lane defaulting to "pass," a run that stops before coverage is
complete, an aggregation that lets one lane force accept.

**Motivation — this session exposed every gap below:** the review loop had **no stopping
condition** and recursed indefinitely (a human had to pause it); the **official second lane never
posted** yet work still progressed; verdicts were aggregated **ad hoc** on a channel; a large
fraction of wall-clock was coordination overhead, not review; and stored baselines went **stale**
across environments with no re-validation trigger.

---

## 1. Lanes and independence

Lanes: **CV/programmatic** (geometry — authoritative for tick centers, box-owns-frame, crossing
coordinates, monotonicity/ordering), **≥2 different multi-modal LLMs** (identity / curve-binding /
plausibility), **human** (residual). Per `auto-acceptance-oracle.md` §11: geometry is *measured*,
not eyeballed.

- **Blind independence.** Each lane produces its verdict from the frozen artifacts **without seeing
  any other lane's verdict**. Verdicts are sealed + timestamped before aggregation. (This
  formalizes the ad-hoc "inspect before reading the other lane" protocol.)
- **Provenance.** Every verdict records: lane id, **model/agent + version**, and the SHA-256 of
  every input artifact it consumed (the hash-lock discipline). A verdict whose artifact hashes
  don't match the frozen packet is void.

---

## 2. Verdict aggregation — fail-closed, monotone

The decision is a **monotone** function of lane verdicts in which "human/refuse" dominates:

```
auto_accept  ⟺  CV lane = PASS
             ∧  ≥2 independent LLM lanes AGREE = accept
             ∧  no lane = RED
             ∧  no required lane MISSING / timeout / error
             ∧  oracle grounding met (auto-acceptance-oracle.md)
otherwise    →  human_review (or served_unverified per the oracle)
```

- **No lane can UPGRADE the decision; any lane can DOWNGRADE it.** One lane cannot force
  auto-accept; any single RED, disagreement, or missing lane routes to human.
- CV is authoritative for geometry (can veto on measurement); LLMs are authoritative for identity/
  plausibility; human for the residual. A domain's authoritative lane failing is a veto in that
  domain.
- **Disagreement between lanes is a first-class signal → human**, never resolved by majority-vote
  auto-accept (two lanes can share a blind spot; the third disagreeing is the tell).

---

## 3. Liveness / failure — absence of a verdict is NOT a pass (the orchestration anti-monotone)

- A **required lane that is missing / timed-out / errored counts as NOT-PASS**, routing to human —
  never treated as pass or silently skipped. (This session: the second lane never posted; the item
  correctly stayed BLOCKED — formalize that as the rule, not luck.)
- Bounded retries + timeouts. **Degraded mode (fewer live lanes) is strictly MORE conservative**
  (more → human), never less. Losing a lane can only shrink what auto-accepts, never grow it.

---

## 4. Termination and budget — the run MUST end (the failure that forced the pause)

- Every orchestration run has an explicit **completion condition**: work-list drained **OR**
  token/time budget hit **OR** N consecutive rounds with no new findings. Open-ended loops are
  forbidden.
- **Scope-creep control.** A run reviews a **frozen work-list**. Discovering a new issue mid-run
  spawns a **separate tracked item** for a future run — it does **not** extend the current run.
  (This session recursed box-v3 → vpl-range → recovery → cap-anchor → … in one continuous loop;
  bound the run, queue the spawn.)
- Budget is a **hard ceiling**. If hit, **log exactly what was left unreviewed** — never silently
  truncate ("no silent caps": a run that dropped work must say so, not read as "covered
  everything").

---

## 5. Cheap-first sequencing (cost)

Order so expensive lanes see the fewest charts:
1. **Fail-closed refusals + CV/programmatic checks** (cheap) — resolve `refused`, `auto_clear`
   candidates, and geometry-veto early.
2. **LLM identity/plausibility lanes** only on the CV-passing remainder.
3. **Human** only on the disagreement / ungrounded residual, **risk-sorted** (triage).
Do not spend model tokens on obvious accept/refuse cases.

---

## 6. Drift / re-validation — calibration is pinned to the extractor version

- The exemplar corpus + tolerances are **pinned to an extractor version/hash**. When the extractor
  changes, that calibration is **stale**; **auto-accept for the affected chart types is DISABLED
  until re-validated** on the new version. (Guard-checklist #4: the signature must cover the CODE
  that derives the value, not just the data. This session's cross-env baseline drift is the same
  failure one level down — stale calibration serving a plausible-but-wrong verdict.)
- A re-validation trigger fires on extractor-hash change; until it clears, affected types fall back
  to human/served_unverified.

---

## 7. Human-loop feedback — vetted, because the corpus is trust-critical

- Human verdicts feed the exemplar corpus, but **vetted**: a human-GREEN becomes a *candidate*
  exemplar, admitted only after a second confirmation / provenance check (the corpus-contamination
  lesson — the 3/30, the "do NOT fit from the 20260715 packet"). Human-RED → negative/must-catch
  exemplar immediately.
- Never let an agent verdict enter the exemplar/ground-truth set. Only human verdicts, vetted.

---

## 8. Audit, telemetry, kill-switch

- Per-lane **false-accept / false-RED** tracking; **risk-stratified** audit sampling (oversample
  first-N of each new fingerprint + near-threshold accepts, not uniform 7%).
- **Kill-switch keyed on the failure's actual dimension** (e.g. "all log-log C(V)"), not only the
  fingerprint tuple; a confirmed false-auto-accept disables the affected dimension until
  re-calibrated.
- **Cost/throughput telemetry** per run (this session had zero budget visibility) — tokens, wall
  clock, human-charts-avoided, so the loop's *effectiveness* is measurable, not assumed.

---

## 9. Guard-review self-check (answer in the PR)

1. **Un-evaluatable lane (missing/timeout)?** → NOT-PASS → human, never pass (§3). ✔
2. **Monotone?** → any concern only moves toward human; a lane can only downgrade; degraded mode is
   more conservative (§2–§3). Test far tail: all lanes dead → human.
3. **Precondition checked?** → auto_accept requires all required lanes PRESENT + agreeing; missing
   required lane fails the precondition, not the default.
4. **Signature vs proxy?** → calibration pinned to extractor version/hash; re-validate on change
   (§6); verdicts hash-locked to input artifacts (§1).
5. **Persist a false verdict?** → a verdict whose artifact hashes mismatch is void; stale
   calibration disables auto-accept rather than serving.
6. **Provenance honest?** → each verdict records model/version + artifact hashes; a degraded/
   budget-truncated run says so and logs the dropped set.
7. **Calibrated against known-bad?** → orchestration tested with a deliberately DEAD lane (must →
   human), a deliberately DISAGREEING lane (must → human), and budget exhaustion (must terminate +
   log dropped).
8. **Fixing check or number?** → orchestration only routes/aggregates; it never edits an extraction
   or a verdict to force a pass.

---

## 10. Acceptance gate

Simulated harness proves: dead required lane → human (not pass); lane disagreement → human;
budget-hit → terminate + logged unreviewed set; extractor-version bump → auto-accept disabled for
affected types until re-validated; all verdicts carry model/version + artifact-hash provenance and
void on mismatch. Dual-agent (cross-family where possible) review + Fab sign-off before it drives
any real queue. Until then, shadow/manual-approve only.

Pairs with `review-risk-triage.md` (volume) and `auto-acceptance-oracle.md` (trust); this file is
the runtime that makes them safe to run unattended.
