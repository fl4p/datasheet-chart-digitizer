---
name: dsdig-gate-charge-panel-local-calibration
description: Gate-charge OCR retries must be panel-local; refine plot boxes against the panel's evidenced closed frame, not the last labeled tick
metadata:
  node_type: memory
  type: project
---

Gate-charge extraction once mutated the shared `page_text` dictionary during an
OCR retry. Text recovered for one bad candidate then contaminated every later
panel's primary extraction. On side-by-side Panjit charts this mixed the gate
charge x-axis with a neighboring normalized-BVDSS y-axis and produced plausible
but wrong ~1 V Vpl values. Keep retry OCR in a separate cache; a panel's primary
native text must remain immutable and panel-local.

Plot-box overshoot is not safely repaired by clamping to the final labeled tick:
real frames may extend one unlabeled interval beyond that tick. Detect the
panel's own closed raster frame from corroborating horizontal/vertical borders,
accept it only when both calibrated axes improve, and preserve evidenced
unlabeled terminal intervals. Regress both directions: adjacent-panel/divider
ink must be rejected, while DI110N15PQ's genuine frame beyond its 60 nC label
must remain intact. The Panjit/Huayi v2 packet `a4ee8c41...` dual-reviewed these
cases; local commits are `edd4bf0` (source) and `38cd424` (tests).

Related: [[dsdig-sweep-green-axis-integrity-retro]],
[[chart-review-checklist]], [[dsdig-fail-closed-null-scalars]].
