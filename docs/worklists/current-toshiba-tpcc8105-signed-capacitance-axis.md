# Toshiba TPCC8105 signed capacitance axis

Status: V1 item RED is superseded by independently GREEN V2 item and bounded
mechanism verdicts. Full authoritative capacitance-corpus A/B and human review
remain held. `human_verified=false`; no commit or push.

## Defect and contract

TPCC8105 page 6 diagram 8.8 is a raster log-log capacitance chart whose source
X ticks are `-0.1/-1/-10/-100 V`. Serving correctly uses positive `|VDS|`, but
V1 fitted the scale at OCR glyph centers rather than the vertical source-grid
rails. The guides were 2.6--4.6 pixels right of the rails and physical voltage
was under-read by roughly 3.8--6.6% at those source ticks.

The OCR signed-axis path now preserves both coordinate sets and re-fits the
served log scale at the actual source rails. It fires only for an explicitly
all-negative source ladder transformed to `abs_source_negative_vds`, with at
least three ticks, regular logarithmic spacing, exactly one unused vertical
rail within six pixels of every glyph center, monotone rails, and a maximum
inverse fit error of one pixel. Positive/text axes remain on their established
path; missing, duplicated, or ambiguous rails refuse.

## Frozen evidence

Superseding packet:
`/private/tmp/dsdig-toshiba-tpcc8105-signed-axis-v2`.

- Source rails are x=125.001/279.001/433.500/587.497 px. Fitted guide error is
  at most 0.653 px and rendered guide error at most 0.503 px.
- The source remains `-0.1/-1/-10/-100 V`; served `|VDS|` at the actual rails
  is 0.09985/0.99333/9.95534/99.0304 V.
- V1 and V2 have exactly the same 1,115 trace pixel rows. Ciss/Coss/Crss remain
  source-seated through both diagonal crossings of the thick 1000-pF rail,
  with no branch swap, grid ride, or endpoint extrapolation.
- Candidate/repeat result, points, overlay, and axis-debug overlay are
  byte-identical. The focused suite passes 133 tests and 15 subtests.

V1 is retained as the causal item RED. This bounded packet does not replace the
authoritative full capacitance-corpus A/B required before landing.

## Independent review

Review:
`/private/tmp/dsdig-toshiba-tpcc8105-signed-axis-v2/reviews/tpcc8105-v2-independent-review.json`
(SHA-256
`66a759d86f84d2532f2d90f1ab1afa65cc25c78b96ad6fe813d334b9641a45c9`).
Verdict: item `AGENT-GREEN`, mechanism `GREEN_BOUNDED`, V1 X-centering RED
explicitly superseded. Full corpus was not run and remains held;
`human_verified=false`.
