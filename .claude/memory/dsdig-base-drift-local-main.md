---
name: dsdig-base-drift-local-main
description: "dsdig agent slices built on origin/main b409eec while Fab's local main is 6 unpushed commits ahead (incl. toshiba raster stratum)"
metadata: 
  node_type: memory
  type: project
  originSessionId: 4c68a9ec-3038-4335-bc6d-ee0407780920
---

datasheet-chart-digitizer: the multi-agent chart-plugin slices (RDS(on)-vs-T, Zth, etc.) were
built in worktrees off **origin/main = b409eec** (the pushed base), but Fab's **local main
(595918d)** is 6 unpushed Fabian-authored commits ahead, overlapping the agents' work:
`6074a0a` Toshiba raster stratum (`_page_image_rects` + `_caption_image_panel_bbox` in
find_charts.py + OCR raster extraction, capacitance-caption-scoped), `6c6c988` capacitance colored
vector traces, `07c6e7a`+`5d720be` axis calibration (decimal/log/decade/dual 1-2-5), `5b13529`
finder capacitance/TI/Toshiba captions.

**Why it matters:** (1) don't rebuild what 6074a0a already has — extend the capacitance-scoped
raster binding to `Fig N.M` characteristic curves instead. (2) The dsdig `.venv` is an editable
install @local-main, so the verify-backlog gap findings ran against live code and are valid.
(3) Frozen slices import `_chain_vector_components`/`_vector_curve_edges` from capacitance_vector.py
which CHANGED in 6c6c988 → their pinned corpus outputs must be re-validated when rebased onto local
main before landing (axis-calib imports like `_snap_axis_to_grid` are unchanged, so those are safe).

Related: [[multi-agent-workflow]], [[dsdig-toshiba-raster-ocr]].
