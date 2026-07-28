---
name: bv-chart-digitizer-plugin
description: "datasheet-chart-digitizer breakdown_voltage plugin (V(BR)DSS vs Tj, Infineon Diagram 15) — unblocks dcdc-tools#19 item 1 avalanche BV(Tj); samples verified against manual reads, min-anchor auto-checked"
metadata: 
  node_type: memory
  type: project
  originSessionId: 0f045b37-646e-40ca-9bd4-db9e7ccf7fee
---

Built 2026-07-14 (user: "start with 1" of the digitizer work). New plugin
`datasheet_chart_digitizer/breakdown_voltage.py`, CLI `dsdig
digitize-breakdown-voltage charts.json --out DIR` (consumes `dsdig find`
output; the finder already classified these panels as kind
`breakdown_voltage`). Vector-exact single stroked line, position-based
linear/linear tick calibration (handles NEGATIVE Tj ticks, which the C(V)
calibrator cannot), exactly-one-curve refusal, tri-state spec anchor:
"verified" = chart V(25 °C) equals the parameter-table V(BR)DSS MINIMUM
(the Infineon chart is the spec floor over temperature — checked per part,
not assumed), "FAIL" = contradicts it, "unverified" = table row not found
(absence never passes). Guard checklist answered in writing; known-bad
calibration tests (C(V) panel through the plugin, corrupted/missing anchor)
in tests/test_breakdown_voltage.py (17 tests; full suite 109 OK).

First samples (match the manual reads on fl4p/dcdc-tools#19 and vendor S5
`ab`): IPP040N08NF2S + IPP024N08NF2S (identical charts) 80.00 V @25 °C,
+40.0 mV/K; IPP022N12NM6 120.00 V, +75.0 mV/K; line-fit RMS ≤15 mV.
VERIFIED by Fab 2026-07-14 ("ok looks good") after the CropTransform fix;
old-layout IPP040N06N overlay ALSO verified ("overlay green") — both layout
pipelines human-checked.
Batch for the loss-curated parts at /Users/fab/dev/pv/ee/out/bv_digitization
(all 6 anchor-verified): IPP040N08NF2S/IPP024N08NF2S/IPP055N08NF2S/
IPP019N08NF2S 80.00 V +40.0 mV/K; IPP022N12NM6 120.00 V +75.0 mV/K;
IPP040N06N (OLD numbered-caption layout) 60.00 V +30.0 mV/K. Old-layout
support needed: caption-finder fix (drain-source rule defers to a preceding
caption number), spec parse across conditions-between-value rows, and a
vector uniform-pitch-gridline plot frame (raster find_plot_box's 8% edge
margin silently CLIPPED the curve at an interior gridline on tight caption
crops — clipping now warns when the trace touches the frame). New-layout
pages have no long vector grid strokes (only the outer border) → uniformity
test rejects → raster fallback. Full-corpus Infineon sweep (5264 PDFs,
multi-hour find render) NOT run — opt-in. Pre-existing env issue: poppler
pdftotext SIGABRTs on datasheets/st/STL70N4LLF5.pdf ("Unknown character
collection PDFAUTOCAD-Indentity0") which crashes the vpl_finder_parity
stage of tools/run_local_regression.py on HEAD too (verified via stash).
Fab's overlay round 1 caught tick crosses a few px off the gridlines →
transform bug: consumers mapped PDF pts through bbox_pt but the finder crops
with a 2 pt margin + pixel truncation. Fix: find_charts.crop_panel now
returns/records the EFFECTIVE crop region (`crop_box_pt` in charts.json,
additive field) and the BV plugin maps through it (CropTransform). Values
were unaffected (labels+trace shared the wrong transform → cancelled in data
space). The C(V) pipeline (capacitance_axis.py:58) still uses the no-margin
transform — values cancel the same way but its overlays likely carry the
same few-px edge offset; PROPOSED to Fab, deliberately not changed
(human-verified corpus).
Human overlay verification by Fab: PENDING as of 2026-07-14 — do NOT batch
or consume downstream before that. Downstream: [[dcdc-loss-derived-channel]]
item 1 (deck avalanche BV(Tj), currently fixed `BV={vds_max+5}` in the lm/tt
branches of loss/lib/models.py); transfer-characteristic plugin is the next
digitizer target (arbiter for IPP022's p, item 3 temp-co).

CONSUMER PLUMBING (dslib, 2026-07-14): dslib/bv_specs.py curates the 6
verified parts; store.py attaches as specs.bv_tj (fill-if-absent). The
startswith fallback overreach (IPP040N06N served to IPP040N06NF2S, a
DIFFERENT die) led to dslib/mpn_match.py — the SINGLE shared
orderable-suffix matcher, migrated into coss_curves/qrr_conditions/
qrr_points/gate_specs/bv_specs; acceptance = zero attach diffs over all
10377 pickle parts vs the loose baseline. Infineon source-down CG/SC/CGSC
are allowlisted same-die layout codes (identical Qrr rows verified on
IQD016N08NM5 vs CG/SC); F2S/L family variants refused. models.py item-1
avalanche wiring still queued behind the original session's diode-Tj +
Vsd-anchor iteration.

CONSUMER PROVENANCE (fetmodel channel consensus 2026-07-14): the chart is
"min-anchored, typ slope" — intercept = spec MINIMUM (80/120 V @ 1 mA),
slope = typ-die tempco (matches vendor S5 `ab` to 4 sig figs). The vendor S5
`UB` (85/127 V) is an internal exponential-KNEE parameter, NOT the 1 mA
onset: Iav = exp(lB + (Vds − UB − ab·(T−298))/UT), lB=−23, UT=0.1 V → vendor
typ-die onset ≈ 86.61/128.61 V @ 1 mA, ~87.8/129.8 V @ 100 A (current-
dependent clamp). Item-1 consumer must carry the TWO intercept models
separately (datasheet min-onset curve vs vendor typical behavioral knee) and
report which it uses; the digitized SLOPE is consumable independently, but
avalanche watts stay dependent on intercept + UT/dynamic-resistance/current
law — Diagram 15 is breakdown onset/tempco only (no high-current clamp, no
UIS), per codex-ee-phys.
