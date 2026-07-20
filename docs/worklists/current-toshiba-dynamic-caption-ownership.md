# Toshiba numbered dynamic-caption plot ownership

**Status:** v3 focused causal A/B is independently agent-GREEN. Authoritative
full-corpus finder A/B and human scale review remain. No commit/push and
`human_verified=false`.

## Defect

On TPH3R70APL1LQ and TPN2R903PL page 6, the numbered `Fig. 8.10 Dynamic
Input/Output Characteristics` caption is printed below its middle-right
dual-Y gate-charge plot. The Qg axis label is stored as vector outlines, so
the text-only direction gate sees no own-axis evidence. Generic nearest-grid
selection then binds diagram 810 to the nearly equidistant lower-right
`Fig. 8.12 Qoss - VDS` plot.

This is a finder ownership defect. Gate-charge OCR must not run on the Qoss
crop and present it as a valid gate-charge extraction.

## Guarded design

- Treat the exact numbered `Dynamic Input/Output Characteristics` title as a
  below-plot caption: when a same-column grid exists above, require that grid.
- Preserve the existing refusal when the required above plot is absent; do not
  fall through to a neighboring grid below.
- Keep the rule title- and numbering-bound. A generic unnumbered gate-charge
  heading may still precede its plot and is not covered by this rule.
- For narrow numbered diagram 810 panels, include both side-axis gutters and
  the bottom Qg label row. Do not widen SSM3K76FS diagram 711: it is single-Y,
  and its right neighbor owns the nearby power-dissipation axis.

## Acceptance

- TPH3R70APL1LQ and TPN2R903PL page 6 diagram 810 move from the lower-right
  Qoss frame (`y ~= 476..629`) to the middle-right dual-Y Qg frame
  (`y ~= 274..426`).
- TJ40S04M3L and TPHR8504PL1 retain the same owned grid and gain only their
  clipped VDS-left, VGS-right, and Qg-bottom scale gutters. TPH3R70APL1LQ and
  TPN2R903PL both rebind and gain those scale gutters.
- TK25S06N1L, SSM3K76FS, TW048U65C, and every capacitance peer retain their
  existing panel rows and crop bytes.
- Candidate/repeat are byte-identical. All four changed crops must show
  VDS-left, VGS-right, Qg-bottom, and the gate-charge curves; the two rebound
  items must not show a single Qoss-vs-VDS curve.
- Run the authoritative full finder A/B and obtain human scale review before
  shared-finder acceptance.

## Focused evidence

- v1 packet `/private/tmp/dsdig-toshiba-dynamic-caption-v1` was independently
  RED because its two corrected crops still clipped both Y scales and the Qg
  row. Review SHA-256:
  `3cd0d8e9c78a3fa9af79e7be95c7679d8fac81b13e7b35fa456fb14a39679f7f`.
- Superseding packet: `/private/tmp/dsdig-toshiba-dynamic-caption-v3`.
- Same-source baseline disables only the exact-title above requirement and its
  diagram-810 scale-gutter expansion.
- Baseline/candidate differ in exactly four panel rows and four crop PNGs:
  TPH3R70APL1LQ, TPN2R903PL, TJ40S04M3L, and TPHR8504PL1 page 6 diagram 810.
  There are no additions, removals, or other peer changes.
- Candidate/repeat directory trees are byte-identical. Candidate charts SHA-256:
  `83292c0f2b1678c53878a9170477f58b7b08426e49791f1a587b69bd9d18c832`.
- Focused finder blockers: 28 passed, 4 skipped, 11 subtests passed.
- Independent focused review is GREEN; shared-finder landing remains held:
  `/private/tmp/dsdig-toshiba-dynamic-caption-v3/reviews/agent-toshiba-dynamic-caption-v3-001.codex-hxy-finder-review.json`,
  SHA-256
  `6d25f00067c407a6d552d906cd1a88087fc3fac2e1ee55fa9f7c969425c9062f`.
