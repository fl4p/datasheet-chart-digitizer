---
name: dsdig wrong-value re-digitization review state
description: The 23 wrong-value extraction classes are agent-reviewed closed; five Panjit/Huayi recoveries landed after dual review
created: 2026-07-17T11:37:05.640Z
metadata:
  node_type: memory
  generator: opencode-claude-memory
  type: project
  originSessionId: ses_09023f1beffe2P2tKPsxl8zldh
---

The two-lane review of all 23 wrong-value extraction classes is closed at the
agent level as of 2026-07-17. Class A and B fixes are dual-GREEN; Class C v5 is
dual-GREEN after Fab caught and the extractor fixed PSMN5R3's crossing-approach
neighbor snap; Class D v3 consists of four honest null-scalar safe refusals;
Classes E–G are dual-GREEN as one recovered FDB2614 extraction plus seven honest
safe refusals. Agent clearance still does not set `human_verified`.

The follow-up sweep found Panjit/Huayi panel-local OCR contamination and plot-box
overshoot. All five recoverable targets plus the DI110 frame-past-last-tick
negative are dual-GREEN in packet `a4ee8c41...`; the source and tests landed
locally as `edd4bf0` and `38cd424` with no push. See
[[dsdig-gate-charge-panel-local-calibration]].

**Why:** The two-lane protocol requires independent agent reviews from both `codex-ee-q1oe` and `opus-b8a11ad6-...` before clearing a class. Agent review never sets `human_verified`.

**How to apply:** Continue monitoring the `dsdig` channel for the next worklist;
apply `CHART-REVIEW-CHECKLIST.md` and independent two-lane review. For fail-closed
items, GREEN means the refusal is correct and every derived scalar/curve is null,
not that physical curve data was verified.
