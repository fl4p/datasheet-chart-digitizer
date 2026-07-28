---
name: dsdig-trace-fidelity-visual-gate
description: auditing a dsdig extraction slice REQUIRES viewing the source-curve-vs-extracted-point overlay; guards + crosshair-tick centering passing does NOT prove trace fidelity
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 4c68a9ec-3038-4335-bc6d-ee0407780920
---

When reviewing a dsdig extraction slice (Zth, capacitance, transfer, body-diode, …), the mandatory
visual gate is a **source-curve-vs-extracted-point overlay** — the extracted points drawn ON the
source chart, confirming they track the printed strokes. Passing named guards, exact tick pins, and
**axis-tick crosshair-centering** does NOT prove the trace follows the source.

**Why:** On Zth Thermal-B I gave a thorough automated GREEN (boundary, SHAs, tests, collapse-span
fix verified by construction, JC/JA refusal, DPI, crosshair centering) but only viewed the axis-tick
crosshair contact sheet — NOT a source-vs-trace overlay. Fab's human review RED'd all 3 overlays:
"points are sprinkled over the charts," not tracking the source strokes. Automated dual-GREEN was
authoritative-looking but wrong; visual source-stroke fidelity is the real authority (issue #8).

**How to apply:** For any extraction-quality audit, open the full overlay (source curves + extracted
points superimposed) at high zoom and confirm the points lie on the printed traces before GREEN.
Treat the 8× crosshair-tick sheet as calibration evidence only (AI-internal), never as trace-fidelity
proof. Related: [[dsdig-human-verify-backlog]] (arrays never prove fidelity),
[[chart-overlay-tick-labels]] (8× crosshair crops are AI-internal), [[scope-readings-human-verification]].
