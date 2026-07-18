# Capacitance actual-artifact tick-centering worklist

**Status:** isolated implementation and full-corpus acceptance in progress. Do
not alter the frozen dependency packets, commit, push, or set `human_verified`.

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
- Re-run the mandatory microscopic intersection gate on PSMN2R4-30YLD,
  PSMNR70-30YLH, PSMN5R3-25MLD, and PSMN6R1-25MLD. Any moved human-reviewed
  crossing artifact requires Fab re-verification; agent GREEN is insufficient.
