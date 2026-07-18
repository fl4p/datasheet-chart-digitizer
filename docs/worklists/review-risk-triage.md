# Review-queue de-prioritization filter — worklist

**Goal.** Stop putting **very-low-risk charts that are near-duplicates of ones a human already
verified** in front of the reviewer. This is a **display/queue filter only**: a hidden chart is NOT
verified, NOT consumable, and never gets `human_verified` — it is simply not shown this round.
Failure cost is bounded — a hidden wrong chart is no worse than today, where the reviewer can't look
at everything anyway.

**Explicitly NOT in scope** (and demolished by two adversarial reviews — see the shelved
`auto-acceptance-oracle.md` / `agent-orchestration.md`): auto-*accepting* charts, certifying
correctness, or letting downstream *consume* a value that no human verified. This filter certifies
nothing; it only prioritizes attention.

---

## 1. Hide rule

Hide chart C from the reviewer's queue **iff BOTH**:

- **(low risk)** `status == ok` AND no diagnostic flag (`vpl_outside_expected_range`,
  `near_axis_top`, `clipped`, `shared_collapse_spans`, `graph_table_inconsistent`, any refusal
  reason) AND axis residual under threshold AND **not a C(V) crossing** (crossing = the chart is a
  Ciss/Coss/Crss triple — *structural*, never hidden); **AND**
- **(familiar)** C matches **≥ k human-GREEN exemplars (k ≥ 8)** of the same **fingerprint**,
  produced by the **same extractor version**.

Everything else → **shown** (human queue, risk-sorted). Hiding requires BOTH positive signals;
absence of either shows the chart.

## 2. Fingerprint — what "very similar" means

`(vendor, chart_type, panel_layout_signature, axis_model [log|lin + decade span + tick count/axis],
curve_count, unit_set, extractor_version)`. Same fingerprint ⇒ same datasheet family + same
extraction path. All fields are already derivable from the chart record + build metadata.

## 3. Exemplar set = human-verified GREENs only

Keyed by `(fingerprint, extractor_version)`. **Never agent-GREEN.** When the human GREENs a chart it
becomes an exemplar for its fingerprint at that extractor version.

## 4. Fail-safe rules (cheap — and the reviews confirmed this is the sound part)

1. **Absence of a match → show.** Novel or under-populated fingerprint (`< k` exemplars), or
   unparseable features → show. Hiding needs a *positive* exemplar match, never "nothing looked
   wrong."
2. **Any flag / crossing / non-ok status → show**, regardless of familiarity.
3. **Monotone:** more risk only ever moves toward *show*; nothing flips a flagged chart to hidden.
4. **Hidden ≠ verified ≠ consumable.** Provenance: `hidden_familiar_low_risk` + matched exemplar
   ids. `human_verified` stays false; downstream must never read "hidden" as usable.

## 5. Two safety valves (the honest residual-risk mitigations)

- **Sample-back:** re-inject a random ~5 % of the hidden set into the queue, so a *per-chart* defect
  the exemplars didn't have still reaches the reviewer occasionally.
- **Extractor-version re-surface (the load-bearing one):** on any extractor change touching a chart
  type, **un-hide that type's familiar class** until the reviewer re-verifies a few new-version
  exemplars. A *new systematic bug* from an extractor fix won't resemble old-version verified charts
  — but only if the match is version-keyed. This is the guard that stops a fresh shared bug from
  being silently hidden.

## 6. Honest limits

- "Similar to verified" is only as good as those verified charts; a contaminated exemplar
  propagates. Bounded because this is *prioritization, not certification*, and the sample-back +
  version-resurface catch the two ways it degrades.
- Yield concentrates on high-volume vendor/layout families (where ≥ k exemplars accumulate); the
  long tail always shows. That's correct — the tail is where novelty risk lives.

## 7. Output

The queue omits hidden charts and reports `hidden: N (M sampled back)` per batch, so the reviewer
sees the count and can spot-open the hidden set on demand. Nothing is deleted or marked verified.

## 8. Build / acceptance

- **Shadow first:** run the filter over a labelled batch; confirm it would hide only charts the
  reviewer agrees are low-risk near-duplicates, and hides **zero** flagged / crossing / novel
  charts.
- **Unit tests:** novel fingerprint → shown; any flag → shown; crossing (C(V) triple) → shown;
  `< k` exemplars → shown; extractor-version bump → familiar class re-surfaced; hidden chart never
  carries `human_verified` or a consumable flag.
- It is a small filter over already-emitted signals + the human-GREEN log — **no new extraction, no
  grounding, no oracle.** Feasible as a dsdig helper.
