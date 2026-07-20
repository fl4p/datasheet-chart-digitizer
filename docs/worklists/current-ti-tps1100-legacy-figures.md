# TI TPS1100 legacy two-column chart coverage

**Status:** diagnosis complete; implementation in progress; no commit/push and
`human_verified=false`.

## Baseline

- The source contains supported Figures 4 (transfer), 5 (RDS(on) vs ID),
  6 (capacitance), 7 (normalized RDS(on) vs Tj), 8 (body diode), and 11
  (gate charge).
- Only Figure 6 and a gate-axis fallback for Figure 11 are detected.
- Figure 6 then refuses because its crop/trace extraction yields only 71 Coss
  columns.
- Unsupported output characteristics, RDS(on) vs VGS, threshold vs Tj, SOA,
  thermal impedance, and application figures must remain unpainted.

## Required direction

1. Support the legacy layout where a semantic multi-line header is above a
   closed plot and the number-only `Figure N` caption is below it.
2. Bind by same-column closed-frame plus owned x/y axis semantics. A nearby
   number-only caption or a header alone is insufficient.
3. Preserve signed P-channel axes and temperatures; do not turn magnitudes
   positive in the source overlay or infer identity from unsigned shape.
4. Keep each 2×2 page cell isolated; no neighboring axis/title/curve bleed.

## Acceptance

- The six supported figures are detected and either served or explicitly
  review/refusal annotated without wrong values.
- Curve identity, signed tick labels, and scale guides are visible.
- Unsupported figures remain unpainted.
- Full affected finder A/B, byte-repeat PDF, `qpdf --check`, and independent
  agent review are required; never set `human_verified`.
