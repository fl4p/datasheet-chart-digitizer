# Finder panel-ownership follow-ups

**Status:** the HXY Figure 4 candidate landed on `main` in `8fb1bba`. The
materialized Toshiba source proves diagram 811 is a genuine gate-charge chart;
its deterministic bounded extraction candidate and controls are independently
GREEN. Full-corpus collateral remains held. `human_verified=false`.

## Confirmed defect candidates

1. `hxy/NTMD4840NR2G-HXY.pdf`, page 3: the `Gate-Charge Characteristics` caption is bound to
   the later Figure 6 normalized `RDS(on)` versus junction-temperature plot. The gate-charge
   digitizer currently refuses, so this is a finder/provenance defect without a served scalar.
2. `toshiba/TK110U65Z.pdf`, page 6, diagram 811: source adjudication proves
   `Dynamic Input/Output Characteristics` is the real Figure 8.11 dual-Y gate-charge chart,
   not a duplicate or false classification. The remaining defect was its silent physical-output
   loss in the diagram-810-specific OCR path; the bounded candidate below resolves it.

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

## Toshiba bounded candidate

TK110U65Z's canonical 498,922-byte, 10-page PDF is materialized (SHA-256
`18e7aa7fec29b07700ee00a9d7c7c1b27ad71a41f9520172354cbb23c0c9ff6c`).
The finder already binds Figure 8.11 correctly. Baseline extraction remained
`unresolved` because the bounded Toshiba dual-Y OCR retry was hard-coded to
diagram 810, and Figure 8.11's `nC` unit sits just below the finder-owned frame.

The candidate applies the retry to numbered `Dynamic Input/Output
Characteristics` figures and expands the bottom OCR band only after its first,
unchanged bounded read fails to find `nC`. It recovers the right `VGS` scale
0/5/10/15 V, the `Qg` scale 0..60 nC, and a source-seated Vpl of 5.963 V. The
finder crop and all non-gate failures are unchanged.

Frozen packet: `/private/tmp/dsdig-tk110-ownership-v1/`.

Independent review: `reviews/independent-review.json`, SHA-256
`0491dfdde8c21a032257196d263f04f927c4d2af032fdf6547243817bbffc303`.

- baseline gate JSON SHA-256:
  `022b56794d3d6b8b956360a9a2b7035b01d2b34b9a8cf8da7d6baa74ffd77e3d`;
- candidate/repeat gate JSON SHA-256:
  `0fab45317f5f305d98ae78c062b508b579719ab4f926899d542d38df07cff10a`;
- candidate/repeat overlay SHA-256:
  `61e280eecf984b86dbfca8ea801ffd349eb7ab1d5bce03a99540d7e7821024f4`;
- candidate/repeat annotated PDF SHA-256:
  `f3a7d790b26ae477fadeaf808ecf9b4972b863c07212cf7bb0a2c760ca0e2775`;
- candidate/repeat finder JSON SHA-256:
  `58e3866881220aa69f4cafed5d4f9930022566a70071b4ba5e7a1cf2728e123d`.

The five earlier Toshiba diagram-810 physical manifests are byte-identical
baseline-to-candidate: TK25S06N1L (`b22dbf21...`), TJ40S04M3L
(`44d0a96e...`), TPH3R70APL1LQ (`a0f56765...`), TPN2R903PL
(`705f3298...`), and TPHR8504PL1 (`38584017...`). Synthetic diagram IDs remain
ineligible for this OCR retry.

The materialized XPQR8308QB source is a second positive, not a unitless
negative: Figure 8.14 visibly owns `Total gate charge Qg (nC)` and the right
0..10 V `VGS` axis. Its stale unresolved regression expectation is corrected;
baseline SHA-256 `6f0250de...` becomes deterministic candidate/repeat
`ddb4acdb...`, status `ok`, Vpl 5.484 V. Its page-3 spec-table false panel is no
longer emitted by the finder.
