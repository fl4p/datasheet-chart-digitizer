# Finder panel-ownership follow-ups

**Status:** queued after the frozen SPD03 random-PDF iteration. No production edit, commit, or
push is authorized by this document. Agents must not set `human_verified`.

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

Use both remaining defects as positives. Freeze the
exact finder and capacitance corpora, run the authoritative same-environment A/B, and inspect every
panel/crop/status/point delta. A classifier fix and a validator refusal guard are independently
landable, but neither may hide the other's diagnostic provenance.
