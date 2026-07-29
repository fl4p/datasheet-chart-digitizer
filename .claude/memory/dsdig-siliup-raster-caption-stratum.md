---
name: dsdig-siliup-raster-caption-stratum
description: "Siliup/WPS page-wide-JPEG datasheets need the raster-frame caption recovery stratum in find_charts (frames+captions baked into images, no text layer)"
metadata: 
  node_type: memory
  type: project
  originSessionId: a5d8fbb8-d358-4799-bc7a-7c8a6027e6ff
  modified: 2026-07-28T16:01:19.981Z
---

Siliup (WPS-generated) datasheets embed whole chart pages as 1-3 page-wide JPEGs:
frames, grids, curves AND captions are raster; the text layer holds only
header/footer, and there are no vector drawings. Normal discovery finds nothing.

`find_charts.py` gained a raster stratum (2026-07-28) firing ONLY where a page
yields no titles, no captions, no axis spans, and no vector-frame recovery:
image-rect coverage >= 0.35 -> `detect_raster_grid_frames` (long-stroke
morphology; (top,bottom) line pairs accepted only with full-span verticals at
BOTH corners + >=1 interior grid line) -> whole-page tesseract ->
`frame_bound_short_caption_segments(below_only=True)` (captions 24-52 pt below
an owned frame). Raster-recovered capacitance captions keep their owned frame —
the image-rect override would bind the multi-panel page JPEG (mis-crop).

**Why:** the panels are correct only because every link is positively
evidenced; each missing link keeps the page a visible finder gap.
**How to apply:** don't loosen `below_only` or the corner-verified frame test to
chase more panels; above-caption raster vendors should fail closed until given
their own evidenced path. Additions are provable via `text_source ==
"tesseract_fallback"` — the 501-PDF A/B showed 0 removed/changed, 257 added
(48/53 PDFs Siliup). Related: [[dsdig-toshiba-raster-ocr]],
[[dsdig-capacitance-closed-bottom-frame]].
