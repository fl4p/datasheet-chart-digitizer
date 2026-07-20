# Review-queue de-prioritization filter — worklist

**Status:** optional future display-queue work. It is not part of the current extractor closure,
does not authorize auto-acceptance, and must not block a frozen fix packet.

**Goal.** Stop putting **very-low-risk charts that are near-duplicates of ones a human already
verified** in front of the reviewer. This is a **display/queue filter only**: a hidden chart is NOT
verified, NOT consumable, never gets `human_verified` — it is simply de-prioritized to a lower-
attention position, still in the same *unreviewed* state.

**Safety claim (honest, corrected).** For an *independent per-chart* defect, hiding is no worse than
today (the reviewer can't look at everything anyway). For a *correlated* defect shared across a
similar family, systematic similarity-hiding is **worse** than ad-hoc human skipping (which catches
some family members by chance) — so the mitigations below (per-class sample floor + toolchain-closure
re-surface) exist specifically to bound that case, not to pretend it away.

**Explicitly NOT in scope** (demolished by adversarial review — see the shelved
`auto-acceptance-oracle.md` / `agent-orchestration.md`): auto-*accepting*, certifying correctness,
or letting downstream *consume* an unverified value. This filter certifies nothing; it prioritizes
attention.

---

## 1. Hide rule

Hide chart C **iff ALL**:
- **(low risk)** `status == ok`, no diagnostic flag (`vpl_outside_expected_range`, `near_axis_top`,
  `clipped`, `shared_collapse_spans`, `graph_table_inconsistent`, any refusal reason), axis residual
  under threshold, and **not a C(V) crossing** (crossing = the chart is a Ciss/Coss/Crss triple,
  *structural*, never hidden);
- **(familiar template)** C matches **≥ k human-GREEN exemplars (k ≥ 8)** of the same **fingerprint**
  (§2) at the same **toolchain-closure** (§2);
- **(duplicate output, not just template)** C's *extracted-curve shape* falls inside the exemplar
  cluster's output envelope (not merely the same source layout). This is what makes "near-duplicate"
  mean duplicate *extraction*, not duplicate *appearance* — two charts can share a template yet
  extract differently (legend over trace, low-contrast/JPEG scan, occlusion, skew).

Anything else → shown, risk-sorted. Hiding requires all positive signals; absence of any shows it.

## 2. Fingerprint + toolchain-closure

- **Fingerprint** (source appearance): `(vendor, chart_type, panel_layout_signature, axis_model
  [log|lin + decade span + tick count/axis], curve_count, unit_set, scan_quality_bucket)`.
  `panel_layout_signature` must derive from **source geometry**, never from the extraction (else a
  bug corrupts the very key meant to isolate it). `scan_quality_bucket` (contrast / artifact /
  resolution) is required so a hard scan is not grouped with clean exemplars.
- **Toolchain-closure hash** (the extraction *path*, not a proxy for it): `sha(dsdig code version +
  pinned dep versions [pymupdf, opencv, tesseract, pillow, poppler] + render DPI + OCR model id)`.
  A change to *any* of these — not just the dsdig code — re-keys the class (§5). This is the direct
  fix for the fact that raster extraction is dep/DPI/OCR-sensitive **without** a code change (the
  `dsdig-gate-collateral-env-drift` finding). `dsdig code version` means the content hashes of the
  extractor and every loaded local dependency, not a branch name, package version, or commit that
  omits dirty working-tree bytes; the dependency-lock content hash is part of the closure too.
- **Exact item identity:** key records by source path + chart type + page + diagram/panel identity.
  A manufacturer/part key is insufficient because one part can contain several independently
  reviewed charts and even several candidate panels of the same chart type.

## 3. Exemplar set = human-verified GREENs only

Keyed by `(fingerprint, toolchain_closure_hash)`. **Never agent-GREEN.** A human GREEN becomes an
exemplar for its fingerprint at that closure; each exemplar stores its extracted-curve shape (for §1
envelope test).

## 4. Fail-safe rules

1. **Absence of a match → show.** Novel / under-populated fingerprint (`< k`), unparseable features,
   or out-of-envelope output → show. Hiding needs a *positive* match, never "nothing looked wrong."
2. **Any flag / crossing / non-ok → show**, regardless of familiarity.
3. **Monotone:** more risk only moves toward *show*.
4. **Hidden ≠ verified ≠ consumable.** A hidden chart keeps the **identical `pending`/unverified
   state** as an un-reviewed chart — never a distinct "handled" state. Provenance tag is
   `deprioritized_unreviewed` (NOT a positive/endorsing name; NO consumable field). `human_verified`
   stays false. The queue is a **pure view/ordering**, not the resolution contract: "absent from
   this view" must not be readable as "reviewed."

## 5. Persistent hidden set + audit (the corrected safety valves)

- **Persistent, with an eventual-show guarantee.** The hidden set is durable; every hidden chart has
  a bounded **max-hidden age** after which it is surfaced regardless. No chart is hidden forever on a
  single lottery.
- **Stratified sample-back with a per-class floor:** ≥ 1 hidden chart sampled into the queue **per
  fingerprint-class per round** (not a flat global 5% that starves small classes), plus a global
  rate on top. Marginal classes (just over k) get eyeball coverage. Persist the PRNG algorithm,
  seed, exact eligible set, sampled-back set, and still-hidden set for each round so an audit can
  reproduce what the reviewer did and did not see.
- **Toolchain-closure re-surface (load-bearing):** on any change to the closure hash (§2) touching a
  class — dsdig code, a dep bump, a DPI change, an OCR swap — **un-hide that class** until the
  reviewer re-verifies a few exemplars at the new closure.
- **Exemplar revocation:** if a GREEN exemplar is later revoked/found wrong, **re-surface every chart
  hidden against it** (a bad verdict must not persist durably as hidden).

## 6. Honest limits

- "Similar to verified" is only as good as the verified charts and the human-GREEN corpus (which this
  project's history shows can be contaminated); a bad exemplar propagates until revoked (§5) or
  sampled (§5). Bounded because this is prioritization, not certification.
- Correlated (shared-family) defects are the residual risk; the per-class sample floor + closure
  re-surface bound but do not eliminate it. State this plainly to reviewers.
- Yield concentrates on high-volume families; the long tail always shows.

## 7. Output

The queue is a **view** that orders/omits by priority; hidden charts remain `pending/unverified` and
are spot-openable. Per batch it reports `deprioritized: N (sampled back: M; classes: C)` so the
reviewer sees the scope. The report also records the exact item identities and sampling provenance
from §5. Nothing is deleted, marked verified, or moved to a "done" state.

## 8. Build / acceptance

- **Step 0 — measure yield before writing code.** Shadow the filter over the real human-GREEN log:
  does `k ≥ 8 + the 7-field fingerprint` hide a non-trivial number of charts at all, or does it
  fragment the corpus so finely it never fires? If yield ≈ 0, the feature isn't worth its safety
  surface — stop here.
- **Shadow correctness:** confirm it hides only charts the reviewer agrees are low-risk near-
  duplicates, and hides **zero** flagged / crossing / novel / out-of-envelope charts.
- **Unit tests:** novel fingerprint → shown; any flag → shown; crossing (C(V) triple) → shown;
  `< k` exemplars → shown; out-of-envelope output → shown; closure-hash change → class re-surfaced;
  exemplar revocation → dependents re-surfaced; max-hidden age → surfaced; hidden chart never carries
  `human_verified` or a consumable tag.
- It is a small filter over already-emitted signals + a persistent human-GREEN/hidden store — **no
  new extraction, no grounding, no oracle.** Feasible as a dsdig helper, contingent on Step 0 yield.
