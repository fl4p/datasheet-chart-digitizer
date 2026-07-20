# Qoss inline min/typ/max recovery v2 worklist

**Status:** design/audit only. Qoss reference-parser v1 is now independently
GREEN in both agent lanes but remains unlanded/Fab-gated; treat its frozen packet
as immutable. Do not implement, commit, push, or set `human_verified` yet.

## 1. Bounded coverage gap

V1 correctly replaces 13 NXP condition-derived Qoss values with null. Each affected source row
also contains a recoverable inline typical value in a merged cell:

`Qoss ... VGS = 0 V; VDS = 15 V; f = 1 MHz; - 3.8 - nC`

The v1 refusal is safe and remains GREEN; v2 is a separate coverage enhancement. Exact affected
parts and expected typical values (nC):

- PSMN011-30YLC 3.8; PSMN013-30YLC 3.3; PSMN1R1-25YLC 22.6;
- PSMN1R2-30YLC 33; PSMN2R2-30YLC 18.4; PSMN2R6-30YLC 17;
- PSMN2R9-30MLC 15.1; PSMN3R0-30MLC 14.2; PSMN4R4-30MLC 9.8;
- PSMN6R0-30YLB 7.2; PSMN6R5-25YLC 5.7; PSMN7R0-30MLC 24.7;
- PSMN9R5-30YLC 4.4.

The dash-slot glyph shape alone is broader than the 13-output delta and therefore cannot itself
be the rewrite predicate. A broad glyph scan finds 1,347 rows across 459 tables. Applying the
proposed exact symbol + VDS + VGS + frequency ownership gate leaves 274 rows across 96 tables:
83 already serve the same value through v1's stronger column-aware path and 13 are the intended
null recoveries. Inline parsing must therefore be a **fallback only when v1 produced no Qoss
candidate**. It must never compete with, replace, or null an existing evidenced candidate.

Frozen audit: `/private/tmp/qoss-inline-source-audit-v1.json`, SHA-256
`d5c7d91e4b2c48e43f7c28cb84330dcbfc8604e355f0fb09485a3f0f5ed15146`; audit tool
`dsdig-verify-backlog/tools/audit_qoss_inline_recovery.py`, SHA-256
`7654e9a6fe5a6a9ff3d1c1bdcfb15e6d5b7183133d64c6189826b80f02623c34`. An independent
sequential repeat is byte-identical to the audit SHA.

Baseline is the frozen v1 candidate manifest SHA-256
`37e0fa451f501a3991be0b5c7a4ab395ecf69dc38a5d6680ce521e0cbb4cb9f1`.

## 2. Required parser direction

1. Run only on a structured Qoss row whose logical cell contains the Qoss symbol and nC unit.
2. Remove exact condition-token spans (VGS, VDS, frequency, temperature/current if present) from
   consideration; never identify a value merely because it differs numerically from a condition.
3. Parse an explicit inline min/typ/max slot pattern. For these rows the pattern is exactly one
   positive typical value bracketed by missing-value dashes (`- value -`) before `nC`.
4. Require all repeated occurrences for a part to agree. Conflicting inline values fail closed
   with a visible diagnostic.
5. Any missing, malformed, or multi-candidate row remains null. Never fall back to "last number
   before nC" or another skip-N heuristic.
6. Keep the existing column-aware candidate path strictly higher priority. Evaluate inline
   recovery only when no v1 Qoss candidate exists for the part; repeated inline conflicts in a
   table that already has a v1 candidate cannot erase that candidate.

## 3. Load-bearing challenges

- **C1 — exact span ownership:** prove the recovered number occupies the inline typ slot and is
  not VDS, VGS, frequency, temperature, a figure number, or row ID.
- **C2 — v1 is the baseline:** only the 13 v1-null Qoss references may recover. Any other Qoss,
  Vint, Co(er), Co(tr), capacitance anchor, CSV hash, trace, or curve change is over-fire.
- **C3 — ambiguity stays null:** condition-only, multiple residual numbers, conflicting repeated
  rows, or missing unit/slot structure must remain refused with explicit provenance.
- **C4 — graph arbiter remains independent:** compare each recovered table value with the
  integrated Coss graph where available. A genuine mismatch remains
  `graph_table_inconsistent`; do not weaken the guard to make the 13 pass.
- **C5 — coincidence negative:** retain v1 fixtures where a true table value equals VDS; inline
  recovery must not reinterpret or erase an already evidenced value-column result.
- **C6 — symbol context is mandatory:** a different-symbol/non-Qoss row containing the same
  `- number - nC` glyph pattern must not trigger recovery. Require the structured Qoss symbol,
  condition-span evidence, dash-slot shape, and unit together.
- **C7 — multi-value slots stay conservative:** the 13 targets are clean single-typ
  `- value -` rows. A row containing min/typ/max all present may select typ only when an explicit
  header/layout proves that position; without that evidence it stays null rather than guessing
  min, typ, or max.
- **C8 — fallback precedence is monotone:** adding an inline recovery path may turn a v1 null into
  an evidenced value, but it may never turn an existing v1 value into another value or null. The
  six observed multi-inline-value Infineon tables are load-bearing negatives for this rule.

## 4. Fixtures and acceptance

- Positive: at least the 3.8, 33, 15.1, and 5.7 nC shapes, plus duplicate-row agreement.
- Real non-Qoss negative: `panjit/PSMB050N10NS2_R2_00601.pdf.nop.csv` row 47
  (`Gate-Source Charge`, `Qgs`, `VDS=50 V,ID=50 A`, `- 15 - nC`). The Qoss fallback
  must not consume the identical dash-slot shape when the structured symbol is Qgs.
- Real multi-value negatives: `infineon/BSC028N06NSSC.pdf.nop.csv` rows 98/108
  (`Qoss,32,43,54,nC`) and `infineon/BSC016N06NSSC.pdf.nop.csv` rows 98/108
  (`Qoss,60,81,102,nC`). V1 already serves their evidenced typ values; fallback precedence
  must leave them byte-identical. A synthetic equivalent without an explicit min/typ/max header
  must stay null rather than guessing the middle number.
- Additional negatives: condition-only Qoss row, two non-condition numbers, malformed dash slots,
  conflicting duplicate rows, already-correct value-column row, and value-equals-VDS row.
- Full 10,225-table same-environment sequential A/B using the authoritative audit harness.
- Expected delta: exactly 13 `qoss_pc` fields from null to the values listed above; 0 exceptions;
  every other selected field and CSV hash byte-identical.
- Freeze raw source rows and graph/table validation where available. The current 13 have no
  digitized Coss graph in the review backlog, so acceptance rests on exact pattern ownership,
  duplicate agreement, and the full-corpus zero-over-fire proof. Dual independent agent GREEN,
  then Fab's human gate. Agents never set `human_verified`.
