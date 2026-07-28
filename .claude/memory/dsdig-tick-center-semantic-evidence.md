---
name: dsdig tick-center semantic evidence
description: exact crosshair centering is insufficient; semantic identity, duplicate agreement, and endpoint coverage are fail-closed gates
metadata:
  type: project
---

For dsdig axis calibration, a crosshair centered on a dark raster line does not
prove that the line belongs to the consumed semantic tick. Bind the numeric
value to nearby label/fit evidence before using grid regularity. In particular:

- spatially distinct duplicate labels for the same semantic value must not be
  median-collapsed; agreement within tolerance is required, otherwise refuse;
- a printed frame may extend about one unlabeled interval beyond the consumed
  sequence, but serving by extrapolating across two or more unseen endpoint
  intervals is unverified (often this exposes missed `1K`/`10K` labels);
- programmatic assertions must cover both the served mapping and the actual
  integer-rounded rendered marker, plus own-axis/plot-box ownership;
- observed-center least-squares fitting delegates to the shared public
  `numeric_axis.fit_axis_ticks` core; raster candidate selection and a
  capacitance-specific piecewise mapping do not justify another fitter.

These rules are also recorded in `dsdig-verify-backlog/CHART-REVIEW-CHECKLIST.md`
§3 and the capacitance tick-center worklist.
