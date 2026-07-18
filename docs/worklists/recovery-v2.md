# Recovery-v2 worklist — DI280 real-Vpl recovery (bounded)

**Status:** proposed / scope-challenge stage. v1 (vpl-range, e667696f) is IMMUTABLE and remains
the baseline. Authors: opus (this worklist) + codex-ee-8ae6 (probe evidence). No commit/push;
agent review never sets `human_verified`.

## 1. Goal & hard scope boundary

Recover the **real** middle-panel gate-charge Vpl for the two vector DI280 variants that v1
correctly nulled:
- native → Vpl ≈ **4.382 V** (codex read-only probe), box right = **536** at the solid frame.
- .gs → Vpl ≈ **4.426 V**, box right = 536.

**Out of scope / must stay unchanged:**
- **DI280.r600 stays REFUSED** (rejected_non_gate/null). v2 must NOT un-refuse the raster variant.
- **box-v3's 5 deltas** (AGM056N10C/FDB120N10/2N7002K/XR10G04S/PSMN102) unchanged.
- **vpl-range-v1's contract**: every part v1 nulled that is NOT native/.gs stays nulled; no
  new served value anywhere except native/.gs.
- Baseline for the v2 A/B is the **v1 candidate (e667696f)**, not 38cd or box-v3 — v2 changes
  native/.gs from null → 4.38/4.43 on top of v1.

## 2. Root-cause decomposition (two distinct defects)

**D1 — caption binding.** The unnumbered caption "Typische Gate-Ladekurve / Typical gate charge
characteristic" currently binds the nearest grid **below** it (the transient-thermal panel);
it must bind the plot **above** it (the real VGS-vs-Qg curve).

**D2 — right-edge frame bleed (§3).** Even with D1's correct plot, the box right edge bleeds
~76 px into the neighbor **capacitance** panel because:
- the aligned-frame error of the true solid frame is **0.204**, just over the **0.18** accept
  threshold, so the evidenced frame is rejected and the box extends; and
- the last labeled tick (**120 nC**) sits ~**18 px inboard** of the actual solid frame line
  (missing intermediate 20/100 labels defeat the one-unlabeled-interval heuristic).

## 3. Proposed mechanisms — and my scope challenges

codex's probe used **evidenced-frame acceptance + a 5 % edge snap** → native 4.382 / .gs 4.426,
box right 536. Challenges to settle BEFORE implementing:

- **CHALLENGE-A (do NOT globally loosen 0.18).** Raising the aligned-frame error threshold
  0.18 → ≥0.204 is a global, data-dependent change: every part whose frame error lands in
  (0.18, 0.204] would newly accept a frame it currently rejects — unbounded box-extension blast
  radius. **Prefer positive *evidenced-frame acceptance*** (accept the frame because a *solid
  continuous vertical frame line* is detected at that x, independent of the error metric) over
  moving the threshold. If a threshold move is unavoidable, it must be gated on the solid-line
  evidence, not applied unconditionally.
- **CHALLENGE-B (5 % edge-snap is itself corpus-wide).** Snapping the box edge to a nearby solid
  line when the edge label is inboard can wrongly pull boxes on parts where the label is
  legitimately inboard of a *wider real frame*. The snap must fire only toward **detected solid
  frame evidence within the 5 % window**, never as an unconditional "extend to nearest line."
- **CHALLENGE-C (monotonic + calibrated).** Both mechanisms are guards that decide "is this the
  frame?" — per the guard checklist they must be calibrated against a **known-bad** frame (a
  spurious inner gridline that must NOT be accepted as the frame) and shown to reject it.

## 4. Blast radius / §9 requirement

D1 and D2 are **shared, data-dependent extractor changes** → full **§9** applies:
- Full 304-corpus same-host **sequential-solo** A/B (OMP_THREAD_LIMIT=1), baseline = v1
  candidate manifest (6f5fdab4), candidate = v2.
- Expected delta set = **exactly native + .gs** (null → 4.38/4.43 with box right 536). ANY other
  changed row (box edge, tick, status, provenance, curve) is over-fire and must get an
  item-specific §0–§7 verdict. Freeze baseline/candidate manifest SHAs + per-delta overlays.
- Explicit exception + no_result counts; both must be unchanged from v1 (0 / 2).

## 5. Required fixtures (acceptance is gated on ALL)

**Positive (the fix works):**
1. native: Vpl 4.38±, box right = 536 at the solid frame, §3 own-frame (NO capacitance-panel
   bleed), caption bound to the plot ABOVE, curve on the real middle VGS-vs-Qg trace.
2. .gs: Vpl 4.43±, same geometry.

**Invariant (nothing else moves):**
3. DI280.r600 still rejected_non_gate/null.
4. A sample of box-v3's 5 parts + ≥3 other capacitance-neighbor gate-charge parts (e.g.
   AGM056N10C, 2N7002K) unchanged — proves the edge-snap/frame-evidence doesn't perturb the
   already-correct own-frame boxes.

**Negatives (guards don't over-fire) — the load-bearing ones:**
5. A **genuine legit-frame-past-last-tick** part (real solid frame extends one interval past the
   last labeled tick) — the evidenced-frame/edge-snap must NOT pull the box inboard of the real
   frame. (Same negative box-v2/v3 needed; reuse or synthesize.)
6. A part with **missing intermediate labels** where the one-unlabeled-interval heuristic is
   currently CORRECT — must not be broken by the D2 change.
7. A **known-bad inner gridline** calibration: a spurious inner vertical line that must NOT be
   accepted as the frame (proves evidenced-frame acceptance is evidence-gated, not permissive).

## 6. Acceptance gate

v2 lands only when: §9 full-corpus A/B shows **exactly native/.gs recovered, zero other row
moved**; all 7 fixtures pass; both guards calibrated against known-bad; dual-lane agent-GREEN;
and Fab's human overlay gate. `human_verified` never set by agents. v1 stays immutable
regardless of v2 outcome.
