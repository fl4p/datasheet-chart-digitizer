# Qoss (table-reference) condition-as-value parser worklist — bounded

**Status:** proposed / scope-challenge stage. Read-only evidence by codex-ee-8ae6; worklist by
opus. box-v3 (6f0c7bf4), vpl-range-v1 (e667696f), recovery-v2 (6ed7b8a9) remain immutable. No
commit/push; agents never set `human_verified`.

## 1. Bug & evidence

`capacitance_refs.parse_output_charge_reference` (via
`_first_number_after_symbol_before_unit`) strips only `@<V>` conditions, so on multi-condition
rows it consumes a **condition number as the value**. PSMNR70 example:
- Table row: `Qoss output charge | VGS=0V; VDS=15V; f=1MHz | typ=62 | nC`.
- Parser returns `qoss_pc = 15000` (the VDS **15**), not `62`. (`vint_v=15` is correctly the VDS.)
- Graph integration = **60338 pC ≈ 60.3 nC**, which agrees with the true table **62 nC** (~2.7 %),
  NOT the false 15 nC. So the graph is right; the parsed table-reference is wrong.

**Scope (codex read-only scan):** the misparse hits **390 unique parts / 1,152 duplicated CSV
rows** (AO 173, TI 119, NXP 81, Infineon 10, onsemi 4, panjit 2, Toshiba 1). Detection
criterion: `parsed qoss_pc == row VDS-condition × 1000` while a **distinct numeric value column
exists before the unit**. This is a high-leverage shared-reference repair, not a one-off.

## 2. Fix direction (agreed) + my challenges

Parse the output-charge value from the **value/typ column**, stripping ALL VDS/VGS/frequency/
temperature conditions — column-aware, not "skip N condition numbers."

- **CHALLENGE-1 (column-aware, format-robust).** The fix must select the value from the table's
  **value column by structure**, not by "strip conditions then take the next number." Value
  formats vary (typ only, min/typ/max, spelled units); a position/skip heuristic re-introduces
  the same class of bug. Prove it on rows with different column layouts.
- **CHALLENGE-2 (fail-closed on ambiguity).** If the value column cannot be reliably identified
  (no distinct value column; only conditions; unparseable), **refuse / keep
  `graph_table_inconsistent`** — never serve a condition number AND never serve a guessed value.
  Prefer a null/flagged reference over a plausible-wrong scalar. (Guard checklist: absence of a
  parseable value must not encode "value = the condition.")
- **CHALLENGE-3 (predicate is itself a guard — calibrate it).** codex's detection predicate
  `parsed == VDS×1000 AND distinct value column exists` is the over-fire-safe rewrite gate. It
  must be (a) MONOTONE / non-flapping, (b) seen to FIRE on a known misparse (PSMNR70), and (c)
  seen to NOT fire on a correct row (parsed already == the real value column). A predicate never
  observed to both fire and hold is unproven.
- **CHALLENGE-4 (audit the shared helper).** CORRECTED per codex read-only scan: the helper
  `_first_number_after_symbol_before_unit` is called ONLY by **Qoss, Co(er), Co(tr)**; Qg/Qgd/Qgs
  have **no call site**. Ciss/Coss/Crss use a **separate** helper `_first_number_before_unit`.
  Audit **both** helpers, report per-symbol misparse counts; do not claim nonexistent Qg coverage,
  and fix-in-pass or scope-out-with-counts for the real call sites (no silent partial fix).

- **CHALLENGE-6 (fix the root, not the VDS symptom) — adopted from codex's response.** The runtime
  must ALWAYS select the evidenced value/typ column, which uniformly fixes EVERY condition-type
  misparse (VGS/VDD/frequency/current/temp — not just VDS). The `parsed == VDS×1000` predicate is
  demoted to an **audit/calibration guard**, NOT a conditional rewrite (a conditional rewrite would
  preserve every non-VDS latent misparse). The **§9 expected-delta set is a pre-change
  condition-token audit across ALL supported labels**; the 390 VDS-equality set must be a SUBSET,
  and any additional moved rows must be source/column-proven, not mislabeled over-fire.
- **CHALLENGE-5 (graph is the arbiter, keep it honest).** The graph-integration cross-check
  (60.3 nC ⇒ 62 ✓, 15 ✗) is the independent truth. Keep the `graph_table_inconsistent` guard
  live: after the fix, PSMNR70 goes inconsistent→pass, but the guard must STILL fire on a part
  with a genuine graph-vs-table disagreement (don't mute the detector by "fixing the number").

## 3. §9 blast radius

Shared reference parser → full **§9** reference-parser corpus A/B (not just the 390):
- Full-corpus same-host sequential-solo A/B, baseline = current head reference output.
- **Expected delta set = the 390 predicate-matching parts** (each: condition → real value, OR →
  fail-closed if no value column). **ANY row OUTSIDE the predicate that moves = over-fire**, gets
  an item verdict.
- **Trace/curve bytes MUST be unchanged** — this is a table-reference parser change, not a
  C(V) trace/integrator change. Assert curve_px/integration bytes identical corpus-wide.
- Report per-vendor before/after counts, exception + no_result counts.

## 4. Required fixtures (acceptance gated on ALL)

**Positive:**
1. PSMNR70 Qoss: 15 → 62 nC; `graph_table_inconsistent → pass`; curve bytes unchanged.
2. ≥1 part from EACH affected vendor family (AO/TI/NXP/Infineon/onsemi/panjit/Toshiba) corrected.

**Negatives (load-bearing — the over-fire traps):**
3. **Coincidence negative (generalized to ANY condition token):** a part where the REAL value ≈
   any condition number (VDS/VGS/freq/current/temp). Always-column-parse resolves it to the true
   value regardless (no-op if value == condition); the calibration predicate must never trigger a
   wrong rewrite.
4. **No-value-column row:** only conditions present → fail-closed (refuse), not condition-as-value.
5. **Already-correct row:** parsed already equals the real value column → predicate does NOT fire
   (no change).
6. **Genuine graph-vs-table inconsistency:** a part where graph and true table really disagree →
   `graph_table_inconsistent` STILL fires after the fix (guard not muted).

## 5. Acceptance gate

Lands only when: §9 A/B shows exactly the predicate-matched set moved (condition→value or
fail-closed), zero out-of-predicate rows moved, all trace/curve bytes unchanged; all 6 fixtures
pass; CHALLENGE-4 shared-helper audit reported (fixed or scoped-out with counts); dual-lane
agent-GREEN; Fab human gate. `human_verified` never set by agents. Prior fixes immutable.
