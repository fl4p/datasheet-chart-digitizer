---
name: dsdig XR100N02F low_confidence scoring bug
description: XR100N02F gate-charge -1e9 low_confidence sentinel was caused by scoring using crop coordinates but normalizing by context-crop dimensions; fixed by plot-local normalization.
created: 2026-07-17T10:24:59.072Z
metadata:
  node_type: memory
  generator: opencode-claude-memory
  type: project
  originSessionId: ses_09065981effeTPfGGzFwg1Q5E2
---

In the dsdig wrong-value re-digitization work (Class-B), XR100N02F gate_charge_vpl initially self-reported `status=low_confidence`, `score=-1e9`, and `diagnostic=low_trace_confidence` even though the overlay looked visually plausible. The root cause was the curve-density scorer: it fed crop-coordinate points into the scoring function but normalized by the larger context-crop width/height, so 85 valid samples fell below the minimum-density sentinel.

**Fix:** Translate the extracted points to the calibrated **plot box** coordinates and normalize by the plot width/height. After the fix XR100N02F reports `status=ok`, `score=+6.03`, `trace_source=raster`, no diagnostics, and Vpl=2.168V on the correct 0..4.5V OCR-anchored axis.

**Why:** A visually plausible overlay is not enough when the extractor self-flags low confidence; the actual score bug had to be found and fixed before the item could clear agent review.

**How to apply:** If another dsdig extraction shows a sudden -1e9 score with a visually reasonable overlay, suspect a coordinate-system mismatch in the scoring/normalization path rather than a genuine trace-density failure. Verify by checking whether the scorer uses plot-local coordinates and plot-local normalization.
