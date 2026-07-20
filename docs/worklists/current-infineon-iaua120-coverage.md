# Infineon IAUA120N04S5N014 chart coverage

**Status:** diagnosis complete; implementation pending; no commit/push and
`human_verified=false`.

## Baseline

- Six candidates are detected, but transfer, capacitance, and breakdown crops
  span neighboring panels and refuse.
- Figure 11 body-diode and Figure 8 normalized RDS(on)-temperature are not
  annotated although both are supported families.
- Figure 15 gate charge serves; the page-5 table graphic is safely unresolved.

## Required direction

1. Bind Infineon numbered captions to their own stroked panel rectangle before
   any axis-gutter expansion.
2. Preserve the panel's local formula and axis labels; never borrow a neighbor's
   title, legend, ticks, or curve strokes.
3. Route an RDS(on) panel with owned `RDS(on)=f(Tj)` formula and Tj axis to the
   temperature plugin even when the short title omits `temperature`.
4. Detect the local forward-diode panel from its explicit IF/VSD/Tj semantics.
5. Gate-charge waveform definitions and avalanche families remain unsupported
   and unpainted.

## Acceptance

- Figures 7, 8, 10, 11, 14, and 15 produce bounded, scale-reviewable artifacts
  or explicit fail-closed diagnostics; no cross-panel crop is accepted.
- Figure 16 and unsupported output/avalanche/threshold panels remain unpainted.
- Byte-repeat, focused and affected-corpus A/B, `qpdf --check`, and independent
  review are mandatory; never set `human_verified`.
