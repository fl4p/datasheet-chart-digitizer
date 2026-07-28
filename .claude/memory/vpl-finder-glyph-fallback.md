---
name: vpl-finder-glyph-fallback
description: dsdig packaged Vpl finder has zero legacy-available misses after guarded glyph-text fallback
metadata:
  type: fact
---

# Vpl finder glyph fallback

`datasheet-chart-digitizer` commit `8ae676b` resolves the last packaged Vpl
finder miss, IPI65R190CFD. When Poppler emits mostly unreadable custom-font
glyphs and PyMuPDF is substantially more readable, the finder uses the latter,
deduplicates overprinted words, and records `text_source=pymupdf_fallback`.
Fallback pages require grid or axis evidence, preventing the page-6 gate-charge
specification table from becoming a false chart. Expanded parity is 61 matched,
0 missing, 5 legacy-unavailable, and 0 tool errors. Issue #1 remains open for
the other standalone-digitizer slices.
