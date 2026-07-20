# ST STL52DN4LF7AG vector-outline breakdown axis

Status: page-7 Figure 11 item, bounded OCR mechanism, determinism, two
text-backed controls, focused tests, and annotate embedding are independently
AGENT-GREEN. Full authoritative breakdown corpus A/B, other STL52 chart
families, and human review remain held. `human_verified=false`; no commit or
push.

## Defect and trigger

STL52DN4LF7AG paints chart tick labels and axis glyphs as vector outlines on a
zero-image PDF page. The plot frame and response curve are ordinary vectors,
but the machine-readable panel text is empty, so the breakdown extractor saw
zero Tj ticks and refused Figure 11.

The fallback is deliberately narrower than a generic OCR retry. It requires
zero native numeric words in both owned axis gutters, zero page images, an
owned closed vector frame with regular X/Y grid families, and exactly one
full-span source curve. OCR is limited to the two axis gutters at 400 DPI.
Each accepted axis must have a unique largest subset of at least four source
labels, distinct observed grid rails, at least 60% grid span, and a linear fit
within one raster pixel. Values are never interpolated from grid geometry;
ambiguous subsets and short runs still refuse.

## Frozen evidence

Packet: `/private/tmp/dsdig-stl52-vector-outline-breakdown-v1`.

- Baseline: `X axis (Tj): only 0 tick labels, need >=4`.
- Candidate consumes clean X labels -25/25/125/175 °C and Y labels
  1.08/1.04/1.00/0.96/0.92 normalized. The two OCR errors (-75→5 and 75→15)
  are rejected rather than repaired or fabricated.
- Exact source-grid fit is at most 0.037 px on X and 0.078 px on Y.
- All 351 served trace pixels are source-seated. Tj spans -55.0 to 174.9 °C;
  normalized scaling is anchored to the source-owned 40 V table minimum;
  V@25 °C is 40.004 V. Status is `verified`, with no warnings.
- Candidate/repeat charts, canonical result, CSV, and overlay are identical.
  BSZ018N04LS6 and IPA60R125CFD7 remain native-text controls: their candidate
  and repeat artifacts are identical and neither gains `axis_text_source`.
- The focused suite passes 7 tests; the combined breakdown suite passes 86.
  End-to-end annotate embeds the verified Figure-11 overlay. Its nonzero exit
  is caused only by four separately scoped STL52 outlined-axis plugins.

## Independent review

Review:
`/private/tmp/dsdig-stl52-vector-outline-breakdown-v1/reviews/independent-review.json`
(SHA-256
`6fb01240b8c14eba43b360e893fdda53a3cb9e7b7e785ce1153b8ab81625be09`).
Verdict: `AGENT_GREEN_SCOPED_FULL_CORPUS_HELD`. The reviewer independently
reran both test gates, recomputed determinism and control hashes, verified all
351 source pixels and the embedded annotation, and kept the full-corpus,
dirty-tree, other-plugin, and human holds explicit.
