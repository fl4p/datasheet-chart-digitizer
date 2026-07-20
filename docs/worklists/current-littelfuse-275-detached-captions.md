# Littelfuse 275-101N30A-00 detached-caption finder recovery

**Status:** terminal agent-GREEN on current `main`. The detached-caption finder
and authoritative-DPI annotation path landed in `ba7a2a1`; fresh source-backed
artifacts, focused tests, and independent review are GREEN. Full-corpus and
human review remain held; `human_verified=false`.

## Defect

Page 3 prints `Fig. N` on one row and the chart title on the next, with two
independent columns sharing each row. The finder sees only an unnumbered
`Typical Transfer Characteristics` title, binds a frame-only crop that omits
its semantic axes, and misses the supported gate-charge and capacitance panels.

## Required direction

1. Join a number-only `Fig. N` segment only to alphabetic text on the immediately
   following line in the same evidenced half-page column.
2. The joined title must independently classify as an already supported chart
   family. Do not promote `Typical Output Characteristics` or other unsupported
   neighboring charts into a digitizer.
3. Preserve the numbered figure identity and bind below only when own-axis/grid
   evidence establishes that direction.
4. Keep each crop inside its column and include the owned axis-label gutters.
5. Fail closed on distant prose, missing titles, cross-column titles, and ambiguous
   ownership.
6. Annotation must reuse its authoritative finder DPI for the gate-charge pass;
   a hidden 120 dpi rediscovery must not discard a panel found at 220 dpi.

## Acceptance

- Page 3 recovers Figure 1 transfer provenance, Figure 3 gate charge, and Figure 5
  Ciss/Coss/Crss capacitance; Figures 2/4 output charts remain unsupported and
  unpainted.
- Gate-charge and capacitance curves track their own source strokes and embed at
  the exact crop rectangles without neighbor bleed.
- The single-curve transfer chart may remain an explicit digitizer refusal: the
  current transfer model requires a 2..6-temperature family and must not invent
  temperature identity.
- Run the authoritative finder corpus A/B. Every newly joined detached caption
  must have same-column title and own-axis evidence; any ordinary prose or
  cross-column promotion is RED.
- Freeze a byte-reproducible annotated PDF, run `qpdf --check`, obtain independent
  agent review, and require Fab's human verdict. Agents never set
  `human_verified`.

## Current implementation evidence

The exact 147,435-byte, five-page source has SHA-256
`a2f81bee77e8cbbfed27b2411644257a0f62b746c53729d170e98a882f87b72f`.
Fresh current-main runs find page-3 Figures 1, 3, and 5; Figures 2 and 4 remain
unsupported. Annotation embeds Figure 3 gate charge (`ok`, Vpl 6.647 V) and
Figure 5 capacitance (`ok`) at their owned rectangles, while Figure 1 remains
an explicit `expected 2..6 temperature labels, found []` refusal.

Frozen packet: `/private/tmp/dsdig-littelfuse-275-v1/`.

Independent review: `reviews/independent-review.json`, SHA-256
`dc1595d9b6c34b68fa253dc5ddd02897a7a3f79298bfc2cc56b7e228db0c87dd`.

- candidate/repeat annotated PDF SHA-256:
  `e26f35abbc81c22a338186e21eda06007c1749d07ea69d5aaf97466a75c824cf`;
- candidate/repeat finder JSON SHA-256:
  `00f0ec3f73c28bde5a9f1b24dc64435a07c29ba2b57aa85eef9c077896276f4f`;
- candidate/repeat gate overlay SHA-256:
  `8c9cbb69525e3507db6ec7f1d5f159101b48a22c7fbf439e2209df4e1ceef2ab`;
- candidate/repeat capacitance overlay SHA-256:
  `61099d85f2c3903178c450fb00bce8edb3fe38166c42ab523f97871f68d17c87`;
- candidate/repeat capacitance CSV SHA-256:
  `6000f31db18c07217448aa6dab68bd5f18a056979d3016ae8556cec5cf52e881`.

The focused detached-caption and annotation suites pass 4/4 tests. The
annotated PDF passes `qpdf --check`; the full rendered page shows neither
supported overlay overwrites its unsupported right-column neighbor. This is a
current-state reconciliation of the already-landed implementation, not a new
detector change or a full-corpus claim.
