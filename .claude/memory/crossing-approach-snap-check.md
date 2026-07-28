---
name: crossing-approach-snap-check
description: dsdig capacitance crossing charts — mandatory microscopic intersection point-fidelity check for GREEN; Coss can snap onto Ciss through the APPROACH to a crossing
metadata:
  node_type: memory
  type: feedback
  originSessionId: b8a11ad6-b8c5-4093-9f87-a79c119074e1
---

On dsdig Ciss/Coss capacitance overlays with a single crossing, the descending Coss extraction can SNAP DOWN onto the flatter Ciss stroke as the two curves APPROACH the crossing, then ride it with a wavy/non-monotone notch instead of holding its own descending branch. Fab caught this on PSMN5R3-25MLD (crop 8caf0a1d) after BOTH agent review lanes (opus + kimi) had passed it GREEN — we inspected overall tracking and the far tail, but not a hard zoom of the crossing APPROACH on both sides.

**Why:** A neighbor-branch snap is invisible at whole-chart zoom and at the crossing point itself; it only shows in the approach region where the two strokes get close but are still distinct. It is a §5 trace-fidelity / branch-capture defect that produces wrong Coss values through the crossing.

**How to apply:** For ANY chart with intersecting curves, the checklist (`dsdig-verify-backlog/CHART-REVIEW-CHECKLIST.md` §3, updated 2026-07-17) now makes it MANDATORY: crop the crossing + its approach on both sides, upscale >=5x (color-separate the centerlines), and confirm each extraction stays on its OWN source stroke — monotone, no plateau at the neighbor's level, no ride. Missing this inspection = UNVERIFIED, never GREEN. Calibrate against PSMN5R3 (known snap: blue Coss plateaus at red Ciss level x174-238, non-monotone). PSMN6R1/PSMNR70 passed (monotone straight through). Also watch for the adjacent failure: a coverage DROPOUT of one curve through the overlap zone (points missing, not misattributed) — a §5 completeness gap, distinct from the snap. Related: [[viz-review-autopilot-contract]], [[dsdig-trace-fidelity-visual-gate]], [[chart-review-checklist]].
