---
name: cv-crop-transform
description: dsdig issue #7 completed with a shared exact crop_box_pt transform and human-verified C(V) axes
metadata:
  type: fact
---

# C(V) CropTransform migration

`datasheet-chart-digitizer` issue #7 is complete in commit `d4773ce`.
Fresh finder indexes persist the effective `crop_box_pt`; capacitance vector
extraction, position-axis calibration, and breakdown-voltage extraction share
one `CropTransform`. Legacy indexes fall back to `bbox_pt` plus the historical
2 pt crop margin. Fab visually verified labeled axes and curve overlays on
2026-07-14 for BSC014N04LS (raster/position), BSC016N06NS
(vector/position), and IAUCN08S5L160T (vector/grid). The 39-chart regression
pins 11 graph/table inconsistencies; those are validation results, not transform
failures.
