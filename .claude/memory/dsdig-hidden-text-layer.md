---
name: dsdig-hidden-text-layer
description: "dsdig reads PDF text only through visible_text: white/alpha-0/render-mode-3 spans are real decoys in the corpus (Vishay Excel leftovers, HXY stale table cells, TI spacing text); OCR layers over scans are alpha-0 and must be kept"
metadata:
  type: project
---

Corpus census 2026-11-27 (vup mirror, 1357 PDFs / 12001 pages):
- 245 PDFs carry dropped (invisible) spans, nearly all white-on-white. 36 Vishay PDFs hold
  Excel template leftovers in white beside the plot ("Axis Title", "10000/1000/100",
  "1st/2nd line"): a hidden second Y tick column. TI "TEXT ADDED FOR SPACING".
  hxy/2N7002KDW.unicoded.pdf: unpainted stale table values (500/0.5/0.05 beside 5/3.75/0.375).
- Render mode 3 alone is rare (4 spans). Scanned PDFs (3 Infineon, all pages one image)
  carry alpha-0 OCR layers over the image: hidden text over an image is kept.
- char_flags 64 (FZ_STEXT_CLIP) + alpha 0 = text used as a clip path; it is visible
  (HXY "°C" cells). Not hidden.
- White-text visibility = dark share of the rendered page under the span; bimodal
  (5580 < 0.02, ~12.8k > 0.5), threshold 0.25.

**Why:** these decoys silently change tick/title/unit/spec readings (gate-charge
nearest-column VGS axis, Vth column overwrite, owned axis titles).
**How to apply:** never call page.get_text for labels/titles/units/spec text; use
visible_text.words/payload/text/get, and run_text_bbox (poppler) already filters.
No module-level cache: tests/rds_digitize_cache.py requires a pristine package.
