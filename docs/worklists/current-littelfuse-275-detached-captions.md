# Littelfuse 275-101N30A-00 detached-caption finder recovery

**Status:** implementation in progress; no commit/push and `human_verified=false`.

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
