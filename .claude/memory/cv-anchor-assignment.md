---
name: cv-anchor-assignment
description: "datasheet-chart-digitizer issue #2 complete: table-anchor residual scoring stabilizes Ciss/Coss/Crss identity without force-fitting inconsistent graphs"
metadata:
  type: project
---

`datasheet-chart-digitizer` issue #2 was implemented in commit `8e978f6` and
closed on 2026-07-14. After trusted C(V) axis calibration, the shared vector/
raster pipeline scores all six Ciss/Coss/Crss assignments in log-capacitance
space against parsed datasheet table anchors, plus right-edge order, Ciss
flatness, and Crss-bottom priors. Relabeling requires at least two agreeing
anchors, a clear score improvement, and bounded RMS/per-anchor residuals;
otherwise the table disagreement is diagnostic only. The manifest records
selected per-anchor sampled values, signed log10 and relative residuals, all
candidate scores, and the selection reason. Verification: 121 unit tests and
the combined local regressions passed; all 39 reviewed C(V) charts remained
green across vector/raster paths, with known graph/table inconsistencies
unchanged and zero spurious reviewed relabels.

Post-review hardening landed in `b6d30bb`: anchor sampling refuses internal
trace gaps wider than 3% of plot width (minimum 4 px) with an explicit
`anchor_inside_trace_gap` reason; such samples cannot count toward relabeling.
The regression contract now pins zero reviewed relabels and the two known
graph/table-inconsistent parts (`BSC016N06NS`, `IPF009N10NM8`), validates six
finite candidate scores, and parses manifests as strict JSON. Full verification
rose to 123 unit tests and all combined local regressions pass.
