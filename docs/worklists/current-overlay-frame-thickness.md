# Normalize detected-chart review frames

**Status:** implementation in progress; no commit/push and `human_verified=false`.

## Problem

Detected-chart overlays use unrelated plot-frame widths from 1 to 3 pixels.
After the raster overlay is placed back into the PDF, thin frames are difficult
to see at normal page zoom and chart-family differences look like status cues.

## Required direction

1. Define one shared review-frame thickness and use it for every embeddable
   detected-chart family: transfer, gate charge, capacitance, breakdown voltage,
   body diode, RDS(on)-versus-current, and RDS(on)-versus-temperature.
2. Keep existing family colors, curve pixels, tick evidence, calibration,
   acceptance status, crop ownership, and embedding behavior unchanged.
3. Diagnostic boxes that are not plot ownership frames may retain their own
   widths; a plot frame must not silently use a private thickness.

## Acceptance

- Every embedded plot frame is 6 pixels at the authoritative 220 DPI annotation
  run, approximately 2 PDF points after placement.
- CSD13201W10 Figure 3 remains plainly visible at normal page zoom.
- A representative PDF containing transfer, gate-charge, capacitance, RDS(T),
  and body-diode panels is byte-reproducible and passes `qpdf --check`.
- A/B comparison permits frame pixels only. Crop boxes, digitized points, curve
  identity, tick/value labels, statuses, and embedded panel count are invariant.
- Independent agent review remains agent-level only; never set
  `human_verified`.
