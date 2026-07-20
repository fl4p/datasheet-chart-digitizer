# Finder panel-ownership follow-ups

**Status:** the HXY Figure 4 candidate is implemented with deterministic
source-backed finder and digitizer A/B and independent review GREEN. The
Toshiba source is now materialized and ready for a separate ownership slice.
`human_verified=false`.

## Confirmed defect candidates

1. `hxy/NTMD4840NR2G-HXY.pdf`, page 3: the `Gate-Charge Characteristics` caption is bound to
   the later Figure 6 normalized `RDS(on)` versus junction-temperature plot. The gate-charge
   digitizer currently refuses, so this is a finder/provenance defect without a served scalar.
2. `toshiba/TK110U65Z.pdf`, page 6, diagram 811: `Dynamic Input/Output Characteristics` is
   emitted as a second `gate_charge` panel. Source ownership and whether this is a measured Qg
   chart require explicit adjudication; title similarity alone is insufficient.

The AGM012 body/SOA and capacitance/power-caption fusion moved into the active SPD03
finder acceptance scope after the full-corpus review proved it candidate-relevant. It is
not duplicated here as future work.

The NCE SOA/capacitance family is tracked only in
[NCE SOA panel misbinding](nce-soa-panel-misbinding.md); do not duplicate its implementation here.

## HXY bounded candidate

The materialized 328,990-byte HXY source is available. Figure 4's Qg and VGS
axis glyphs are not native PDF text, so the finder sees the numbered caption
but chooses the nearer Figure 6 grid below it. A bounded OCR retry reads only
the already-rendered finder page: the 120 dpi gate-digitizer page reads the
x-axis as `Qs, Total Gate Charge (nC)` and proves that the owned plot is above
the caption. Alternate 180 dpi renderers read the same source glyph as `Qg` or
`Qe`; those OCR-tolerant repairs are accepted only beside both gate-charge
wording and a charge unit.

The candidate rebinds page 3 diagram 4 from Figure 6
`[328.594, 554.712, 541.714, 728.678]` to Figure 4
`[272.127, 330.155, 578.082, 536.392]`. A separate axis-ownership guard rejects
a native Y-tick run whose endpoints overhang the finder panel by more than 45%
of its height; this prevents the gate extractor from expanding the corrected
panel back across remote chart rows.

Frozen packet: `/private/tmp/dsdig-hxy-ownership-v1/`.

Independent review: `reviews/independent-review.json`, SHA-256
`b2425287f6db63546aa83e44d4379db1adf20a72acc94056fa3bf237f724cceb`.

- source PDF SHA-256:
  `505c86f6219c3fa2db062cbfe91c0cd01bd7c2fd0b8a6f1c81b911ee2890736d`;
- baseline finder SHA-256: `17efc217399e5f6f24cc5d71a8d6bfc01269ba0bb204e1678b587cdf62688602`;
- candidate/repeat finder SHA-256:
  `7f4cb4dcafa98cc7dad652497531dfe5f6423a5c7e3c28a6a0fdf478913a5681`;
- candidate/repeat canonical gate result SHA-256:
  `97bad403d89f4abcb11cdad2c637d084fbff30e2102b68063ea871ed5b93af91`;
- candidate/repeat overlay SHA-256:
  `acf9232fa414b5f7b08c8c7cf5b527b05c45d85630164af3abd5ef86eeabf46b`;
- Figure 7 capacitance control crop is byte-identical across baseline,
  candidate, and repeat.
- Four materialized source controls are byte-identical finder-to-result A/B:
  BSZ018N04LS6 (`19bd0b55...`), IPA60R125CFD7 (`30ff7538...`),
  CSD16401Q5 (`c9d64adb...`), and IRF6644 (`6e61a752...`). The remote-Y
  overhang veto runs only when no coherent owned x-axis anchors that Y column.

The baseline is `rejected_non_gate:on_resistance`. The candidate owns the real
Miller plot and recovers a diagnostic Vpl near 3.26 V, but remains
`axis_grid_inferred` / `low_trace_confidence`: its serialized physical output,
curve, and Vpl stay withheld. Recovering the incomplete upper branch is a
separate extraction item and is not laundered by this finder fix.

## Required fix boundaries

- Finder classification must prove title, axes, and crop all belong to one printed panel.
- Caption direction cannot cross an inter-panel gap or bind a later chart merely because it has
  a grid.
- A capacitance panel requires owned `Ciss`/`Coss`/`Crss` plus capacitance and `VDS` axis evidence;
  SOA pulse labels and power-limit geometry are explicit vetoes.
- Defense in depth belongs in the capacitance validator: significantly rising or SOA-shaped
  candidate traces must fail closed even if the finder misclassifies the crop. Calibrate this
  guard on real noisy/non-monotone C(V) negatives so it does not over-fire. Once refused,
  [the capacitance point contract](cap-fail-closed-contract.md) must prevent calibrated
  points from remaining consumable.

## Acceptance

The HXY slice requires the exact source PDF, candidate/repeat finder equality,
source ownership of Figure 4, unchanged Figure 7, and a non-consumable
review-required gate result. The remote-axis negative must stay rejected while
an owned local tick run remains accepted.

TK110U65Z remains a separate positive. Its canonical 498,922-byte, 10-page PDF
is now materialized (SHA-256
`18e7aa7fec29b07700ee00a9d7c7c1b27ad71a41f9520172354cbb23c0c9ff6c`), so
diagram 811 can be adjudicated in a source-backed follow-up. A classifier fix
and a validator refusal guard are independently landable, but neither may hide
the other's diagnostic provenance.
