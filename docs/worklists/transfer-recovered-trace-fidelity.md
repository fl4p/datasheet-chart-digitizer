# Recovered transfer-chart trace fidelity

**Status:** future bounded repair after the SPD03 random-PDF iteration. No production edit,
commit, or push is authorized here. Agents must not set `human_verified`.

## Frozen defect set

The transfer ownership patch recovers 141 previously refused panels. Microscopic review classifies
113 as agent-GREEN candidates and 28 as RED item outputs. This worklist repairs those 28 without
reopening the panel-recovery patch:

- 22 NXP two-temperature charts have reversed curve identities. Printed labels place the hot curve
  on the left, while the current ordering prior binds it as 25 C.
- 5 charts capture grids, text fragments, or temperature leaders:
  `MCA03N10-TP`, `MCAC38N10YA-TP`, `MCT04N10B-TP`, `FDMS8320LDC`, and `FDS8447`.
- `2N7002T` switches branches and becomes jagged through a crossing.

## Required design

1. Bind temperature from source-owned printed labels and leaders before using geometric ordering.
   Ordering is a checked prior, never the sole identity oracle.
2. Preserve a genuine single crossing. Each output trace must remain seated on one source branch on
   both sides of every intersection.
3. Fail closed on ambiguous label binding, disconnected nonzero fragments, comb-like grid capture,
   large drain-current reversals, and annotation-leader capture.
4. Emit explicit temperature-assignment provenance; a plausible colored overlay is insufficient.

## Load-bearing fixtures

- NXP positives: `PSMN011-30YLC`, `BUK7A1R3-100L`, and `PXN010-30QL`.
- Already-correct identity negative: `XP10N3R5XT`.
- Trace negatives: the six non-identity REDs listed above.
- Acceptance negatives: `MCU01N60A-TP` and `MCU05N20A-TP` retain genuine high-VGS plateaus;
  `MCU12P06Y-TP` retains P-channel orientation; the SPD03 `ba387b56...` overlay remains
  source-faithful at both intersections.

## Acceptance

Run the authoritative same-host full transfer-corpus A/B with frozen crop hashes and the production
selector. Every identity, point, status, box, tick, and provenance delta needs review. Every
intersection gets a 5x source|overlay inspection. The 113 current agent-GREEN artifacts remain
byte-identical unless a source-proven improvement is independently reviewed. Zero new
success-to-error, branch, leader, or grid captures are allowed.
