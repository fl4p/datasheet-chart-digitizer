---
name: dsdig-capacitance-closed-bottom-frame
description: Dense C(V) grids can make median vertical endpoints truncate the plot; extend only with top-aligned side rails plus a shared solid bottom frame
metadata:
  node_type: memory
  type: project
---

`capacitance_traces.find_plot_box` historically used the median endpoint of
long vertical strokes. On dense log C(V) panels, many internal verticals stop
early to leave space for notes, so their majority endpoint can truncate the
plot above the real bottom. FDPF190N15A was cut at y=418 instead of its solid
frame at y=513; this shortened Crss below the vector full-span gate and forced a
gridline-riding raster fallback.

The safe repair is positive closed-frame evidence, never `max(y_end)`: both
side rails must begin at the detected plot top, terminate at the same deeper y,
and share a near-full-width horizontal bottom. Requiring the rail start as well
as its end is load-bearing. NCE SOA crops exposed an outer crop border that
started above the true plot top and closed below it in whitespace; accepting
only the rail endpoint caused three box regressions even though they remained
fail-closed.

Any shared-frame change needs a full frozen-corpus A/B. Inspect every taller
box for own-frame versus whitespace/neighbor capture, and review downstream
vector/raster selection, identities, raw points, shared spans, and references.
Build this frame repair on top of the cap-anchor parser: recovering a chart can
otherwise expose a legacy condition token as a plausible table reference.

The same completeness check applies horizontally. Fab human-flagged Infineon
`IPD50N10S3L-16` p6d10 because its detected right edge stopped near 85 V while
the owned source frame, 100 V tick, all three curve tails, and series labels
continued outside the box. An overlay can look source-seated inside a truncated
box and still be RED. Authoritative worklist:
`current-infineon-ipd50-cap-right-frame.md`.

Related: [[dsdig-full-corpus-authoritative-harness]],
[[dsdig-trace-fidelity-visual-gate]], [[chart-review-checklist]].
