---
name: chart-overlay-tick-labels
description: "Human-review chart overlays must always show every consumed tick label, value, and unit so axis calibration can be verified visually"
metadata:
  node_type: memory
  created: 2026-07-16
  type: project
---

For every datasheet-chart human-review overlay, always render **all axis ticks consumed by calibration**, including their parsed numeric values and units. Keep the labels readable and outside the source data when necessary; also mark the corresponding physical tick/grid positions on the plot. A traced curve looking correct is not enough for acceptance—the human reviewer must be able to verify the linear/log scale and tick-to-grid alignment directly from the overlay.

The authoritative full review and overlay-generator contract is
`/Users/fab/dev/pv/ee/dsdig-verify-backlog/CHART-REVIEW-CHECKLIST.md`.
Apply it top-to-bottom; this note only expands its crosshair-specific requirements.

This applies to all chart plugins, not only diode forward-voltage extraction. Treat missing or illegible consumed-tick labels as a human-gate failure.

For the AI/code review only, inspect an **8× upscaled local crop around the
crosshairs** in temporary scratch output. This is an internal vision aid because
a prior native-scale review missed a 4 px offset. Do **not** generate, enqueue, or
present 8× crops as human-review artifacts; Fab reviews the normal overlay. The
internal crop should cover representative interior ticks plus shared-axis
corners/origins, where a small inset is easy to miss.

For logarithmic axes, make the internal crop wide/tall enough to show adjacent
major and minor gridlines. Verify both that the crosshair is centered on a
physical line and that the parsed value is associated with the **correct** log
line. Exact centering on the wrong adjacent gridline is still a calibration
failure; a tight crop that hides neighboring lines cannot detect this shift.

For every linear or logarithmic axis, also compare at least three separated
tick/grid intersections (near both ends and an interior tick). Checking only
the origin or one midpoint can miss an affine scale error whose markers drift
progressively away from the physical gridlines across the axis.

Treat the diagnostic crop itself as a gated artifact: it must visibly contain
the intended tick label, crosshair, physical gridline, and adjacent gridlines.
If a crop shows a title/header or the wrong plot region, the coordinate
selection is invalid and the entire crosshair audit must be rerun; never infer
GREEN from a malformed contact sheet.

The exact-center assertion must cover the **same pixel/value mapping that serves
the extracted data**. Moving only the overlay marker to an observed grid center
is a mute-button fix when the least-squares calibration still misses that center;
re-fit the mapping or fail closed. On log axes, match the labeled major-tick
sequence so a nearby minor line cannot satisfy the assertion. A consumed tick
outside the detected plot box is a separate box/axis-ownership failure even when
the label and fitted marker agree; bind it only if it belongs to the panel's own
evidenced frame.
