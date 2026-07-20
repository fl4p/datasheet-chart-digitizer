# ST MDmesh M9 thin vector strokes and reversed body-diode axes

Status: ST extraction/overlay and shared-capacitance source closure independently
AGENT-GREEN. `human_verified=false`; no commit or push.

## Defect

`st/ST8L65N044M9.pdf` draws the data curves in Figure 11 (normalized
breakdown voltage) and Figure 12 (reverse-diode forward characteristics) as
0.510 pt black vector strokes. The shared vector helper rejected every stroke
below 0.8 pt, so breakdown found zero of one curve and body diode found zero
of three curves.

Figure 12 also uses the less common source orientation: current `ISD` is the
X axis (0..50 A) and voltage `VSD` is the Y axis (0.4..1.1 V). The body-diode
extractor only accepted curves spanning Y and always interpreted X as voltage,
so merely admitting the thin strokes was insufficient.

## Bounded contract

- Keep the shared `_vector_curve_edges` default minimum at 0.8 pt.
- Expose the minimum as a keyword-only parameter and opt only breakdown and
  body diode into 0.4 pt. Capacitance, transfer, and RDS-temperature retain the
  reviewed 0.8 pt behavior.
- Continue rejecting non-curve colors, plot-edge orthogonal strokes, and
  strokes wider than 2.2 pt.
- Recognize current-X/voltage-Y only when the numeric axes prove a large,
  nonnegative current-like X range and a compact positive linear voltage-like
  Y range. Ambiguous small-signal axes keep the legacy orientation.
- For the reversed orientation, require a curve to span at least 75% of plot
  width with material Y response, resample by X, and map the final physical
  columns back to `[VSD, current]`.
- Preserve the existing voltage-X/current-Y output byte-for-byte on bounded
  controls.

## Frozen evidence

Packet: `/private/tmp/dsdig-st8l65-thin-strokes-v1`.

- The pre-slice decision harness refuses Figure 12 with zero curves and Figure
  11 with zero of one full-span curve.
- Candidate/repeat bounded summaries are identical after excluding their mode
  tag (canonical SHA-256
  `fb1b20aa5f745d518c5ce4c68ae0ab08493b7168d6c7f73934870cad1fc25bb7`).
- The five controls are identical baseline/candidate/repeat (canonical SHA-256
  `cbacad27195c0d63e498dcf2e570f62b6231dd7fccd54003bcfa27d45e78c125`):
  body diode on `IPP024N08NF2S` and `FDA032N08`, and breakdown on `IRF1018E`,
  `STF7N60M2`, and `STD14NM50NAG`.
- Figure 12 is `ok`, with source-axis diagnostic
  `source_axes_current_x_voltage_y`, temperatures -55/25/150 °C, and
  340/337/340 points. All curves cover about 5..49 A without a temperature
  swap.
- Figure 11 is `verified`, vector-framed, 375 points over -54.9..149.8 °C,
  `V(BR)DSS(25 °C)=649.8 V`, and slope 653.29 mV/K.
- Candidate/repeat annotated PDFs are byte-identical, SHA-256
  `5d12426c7654f9ae4a6ca3cdbd29f5d5b736ff3dd68262eecd99248595014f1a`.
  The body and breakdown overlays are byte-identical with SHA-256
  `684e9c8dec9c133cce4642cc7e4e187199bdc26731b734bab044e16d6bbc18cf`
  and `bc9286c1ccd90c9ba2281b66909e13f90eb05c96fea4d1b914532d0b7fc15656`;
  the breakdown CSV SHA-256 is
  `cbc006cc3a7d63b961ce49d4cf3228e09da4e96cf944fb56e6484fcc6c547c5f`.
  `qpdf --check` passes.
- Eleven focused tests and three subtests pass. The broader touched test files
  have 95 passing tests; eleven existing onsemi calibration assertions still
  fail because unrelated dirty finder geometry changed their pinned crops.
- The full annotate command exits nonzero only for the separate Figure 4
  transfer axis-ownership error. Its manifest is still written, and Figures
  11 and 12 are embedded. Other refused/unverified panels remain outside this
  slice and are not upgraded.

Changing the shared helper changes the source-closure hash for capacitance even
though its default call path is unchanged. The authoritative 800-panel
capacitance packet was therefore re-frozen under
`/private/tmp/dsdig-cap-source-path-v4`. Independent review confirmed zero
differences across all 1,170 non-metadata artifacts in reviewed v3, v4
candidate, and v4 repeat. The only source-closure change is the helper file;
all capacitance callers keep the 0.8 pt default. Review:
`/private/tmp/dsdig-cap-source-path-v4/reviews/agent-cap-source-path-v4-001.hxy-transfer-ab-review.json`
(SHA-256
`a704b1e81ae16596964b9ea027e7636b6deec508af63affa171366328fee8867`).

The first independent review correctly found that the reversed overlay still
used legacy tick-unit suffixes (`A` on voltage Y and `V` on current X). That
review is retained as superseded evidence, not upgraded:
`/private/tmp/dsdig-st8l65-thin-strokes-v1/reviews/opus-st8l65-thin-stroke-review.json`.
The overlay is now orientation-aware: its header says `VSD (V) versus IF/IS
(A)`, Y ticks use volts, and X ticks use amperes. A new no-carry-forward review
verified that the defect is resolved, the physical arrays are unchanged, all
three traces remain source-seated and ordered, and candidate/repeat remain
deterministic:
`/private/tmp/dsdig-st8l65-thin-strokes-v1/reviews/opus-st8l65-thin-stroke-review-v2.json`
(SHA-256
`dcb87602e0e9d48dddc561e7252b3c8be867794345aa1c1ea321b867c2a57680`).
The ST extraction and overlay item are AGENT-GREEN; `human_verified` remains
false.
