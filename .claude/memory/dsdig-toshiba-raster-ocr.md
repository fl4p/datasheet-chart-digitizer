---
name: dsdig-toshiba-raster-ocr
description: "dsdig Toshiba whole-figure raster stratum — image-rect panels + tesseract OCR position calibration + 2x2 stroke opening for black grids; DPI-sensitive (180 OK, 200 suspect)"
metadata: 
  node_type: memory
  type: project
  originSessionId: 1aad6a00-fef7-4441-961f-9bfe66d71cd1
---

dsdig can now digitize Toshiba-style whole-figure raster charts (TK100E10N1 Fig 8.8): the
figure is ONE embedded image with zero PDF text, so all text-based calibration tiers fail
by construction. Landed 2026-07-16 as dsdig 6074a0a + 5d720be:

- finder binds a capacitance caption to the embedded image rect above it (label-complete,
  vs the tick-clipping grid bbox);
- `infer_ocr_position_axis_calibration` (source `position_ocr`) OCRs the tick bands with
  tesseract `--psm 11` TSV at 400 dpi and feeds the SAME position fit + residual gates;
  last fallback only, errors go to `axis_ocr_error`;
- black-grid rasters (dark fraction >10%) separated by STROKE THICKNESS: 2x2 opening kills
  the 1-px grid, frame margin blanked (was tracking as flat phantom traces).

**Gotchas:** the stroke-thickness trick is render-DPI-sensitive — at the CLI-default 180 dpi
it works; at 200 dpi the grid renders 2 px, survives the opening, and validation correctly
flags `suspect`. Grid tier now REFUSES log-spaced X ticks (was a trusted linear map, ~16x
mid-plot error); `calibrate_axes` dual-fits linear/log so 1-2-5 tick sets calibrate.

Result TK100E10N1 @50V: Ciss 8688 (-1.3%), Coss 1393 (-7.1%), Crss 61 (-3.2%) vs table typ;
validation pass. Overlays awaiting Fab's verification before curves land in dslib
COSS_CURVES (same for TI CSD19531KCS — see its +5-10% curve-vs-table offset and Qoss 62 vs
98 nC tension, unresolved). Related: [[cv-crop-transform]], [[cv-anchor-assignment]],
[[chart-overlay-tick-labels]].
