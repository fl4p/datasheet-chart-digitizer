# Capacitance actual-artifact tick-centering worklist

**Status:** isolated implementation reviewed, but final freeze is blocked on
reproducibility and the prerequisite point contract.  Two solo full-corpus runs
both produced 455 selected results / 345 byte-identical errors, zero raw-trace-shape
changes, and 1,840 passing tick assertions, but their selected manifests were not
byte-identical.  Four rows moved: served `2N7002-13P` consumed a different x-endpoint
tick (while status/physical availability and point artifact stayed stable), and three
already-refused rows changed OCR failure provenance.  Treat this as a reproducibility
failure until the OCR evidence/selector is deterministic; do not freeze whichever run
looks preferable.  Before final freeze, rebase the candidate on
`cap-fail-closed-contract.md`: its current
baseline has 102 pre-existing unverified-point leaks, and calibration changes
incidentally seal only 38.  Do not hide that separate contract defect inside
this data-moving slice.  Do not alter frozen dependency packets, commit, push,
or set `human_verified`.

## Defect

Capacitance overlays render tick crosshairs by inverting the same least-squares
axis fit used for served pixel-to-value conversion. The current fit consumes
text-label centers, not asserted printed grid/tick intersections. On
FDP039N08B-F102, FDPF190N15A, and FDPF390N15A, fit-vs-label residuals are about
0.25–1.9 px and preliminary fit-vs-grid residuals are about 1–3.2 px. Moving
only the rendered marker would therefore hide a real data-calibration residual.

A second, distinct defect class exists: on HSCTW40N120G2VAG the label and fit
agree within 0.04 px at the 200 V tick, but the tick/grid is around x=868–871
while `plot.x1=840`. Its roughly 31 px overflow is plot-box/tick-run ownership,
not least-squares residual. These classes must not share a cosmetic fix.

The first full run also exposes safe fail-closed outcomes that are part of this
patch's causal review, not automatic regressions: 35 formerly served charts become
unverified (32 endpoint-undercoverage, one foreign/out-of-box tick, one conflicting
duplicate semantic label, and one wrong-panel/missing-X-label case).  Every one must
receive source review and emit blank calibrated point cells.  A refusal that merely
changes status while leaving a physical curve ingestible is RED.

## Required direction

1. Preserve the observed tick evidence (value, label position, and evidenced
   gridline center) in `AxisCalibration` instead of retaining values only.
2. Match gridline centers with a sequence-constrained detector. On log axes,
   the labeled major-tick sequence must disambiguate nearby minor gridlines;
   nearest-dark-line selection is forbidden.
3. Re-fit the served pixel-to-value mapping to the evidenced tick/grid centers,
   or fail closed when the mapping misses tolerance. Render crosshairs from the
   same accepted calibration; never move only the marker.
4. Emit a machine-checkable per-tick record containing observed center,
   rendered center, delta, axis ownership, and pass/fail. Missing evidence or
   an out-of-frame marker fails closed.
5. Do not snap to an arbitrary dark line. A candidate gridline must belong to
   the panel's own continuous grid and be locally consistent with the
   neighboring tick sequence.
6. Treat an evidenced consumed tick outside the plot box as a separate
   box/axis-ownership failure. Extend only to the panel's own continuous frame;
   otherwise reject the foreign tick/panel binding.
7. Bind semantic values to nearby label/fit evidence before grid regularity.
   Exact centering alone must not snap a non-decade endpoint to an interior
   minor gridline or mistake a wider unlabeled frame for the last tick.
8. Collapse duplicate semantic labels only when their candidate centers agree
   spatially. Conflicting duplicates fail closed; their median is not evidence.
9. Permit at most one unlabeled endpoint interval (with bounded raster
   tolerance). Two or more unconsumed intervals require semantic tick recovery
   or an untrusted axis; long extrapolation from centered interior ticks is not
   accepted.
10. Delegate the linearized least-squares fit to the shared public
    `numeric_axis.fit_axis_ticks` core. Raster evidence selection and the
    piecewise served mapping may remain capacitance-specific; do not add another
    hand-rolled axis fitter.

## Fixtures and acceptance

- Positives: the three FDP/FDPF charts above, with exact-center assertions on
  every x/y marker and microscopic right-edge crops.
- Ownership positive/negative: HSCTW40N120G2VAG must either bind its own 200 V
  tick and frame consistently or fail closed; it may not clip/snap that tick to
  x=840.
- Known-bad: a marker 2 px beyond the own frame; an adjacent-panel gridline;
  an interior minor log line close to a major tick; and a missing gridline.
- Negative: a genuine frame extending beyond the last labeled tick. The last
  tick stays on its own gridline and must not be snapped to the wider frame.
- Negative: two spatially distinct occurrences of the same semantic tick value
  must refuse; agreeing duplicates may collapse.
- Coverage pair: one unlabeled endpoint interval remains accepted, while two
  missing endpoint intervals fail closed.
- Full frozen 800-chart same-environment A/B. This is data-moving: compare every
  served C(V) point/value, calibration, tick-evidence, overlay, and physical
  scalar. Prove each movement reduces residual to printed grid truth.
- Freeze and review the complete downgrade set.  Current pre-freeze counts are 35
  `ok -> unverified`, 73 trusted-axis downgrades, 257 selected calibrations moving,
  zero raw-pixel trace/shape changes, and 184 trusted rows carrying 1,840 passing
  observed/mapping/render/ownership assertions.  Treat these as expected only after
  byte-identical repetition and per-item source review.
- Require two sequential solo authoritative runs to match byte-for-byte, including
  accepted tick evidence and refusal provenance.  The current v8/v9 mismatch on
  `2N7002-13P`, `IXTA18P10T`, `NTMFS5C426NLT1G`, and
  `SUD50N03-06AP-E3-HXY` is a blocker, not an allowed OCR exception.
- Re-run the mandatory microscopic intersection gate on PSMN2R4-30YLD,
  PSMNR70-30YLH, PSMN5R3-25MLD, and PSMN6R1-25MLD. Any moved human-reviewed
  crossing artifact requires Fab re-verification; agent GREEN is insufficient.
- Keep the patch verdict separate from item verdicts.  The tick-center patch may
  resolve the exact-center blocker on FDP039/FDPF190/FDPF390 while those items remain
  UNVERIFIED for near-axis-top or other independent blockers.
