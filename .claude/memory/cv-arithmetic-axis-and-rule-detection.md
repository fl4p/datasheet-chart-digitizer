---
name: cv-arithmetic-axis-and-rule-detection
description: Linear-pF C(V) axes (goford/huayi/NCE/Sinopower) failed on axis-title unit ownership, a log misread of an arithmetic ladder, and a morphological rule detector blind to dotted/label-broken rules
metadata:
  type: project
---

Fixed 2026-08-07 (uncommitted at the time of writing) in `axis_calibration.py` +
`capacitance_axis.py`:

1. **Unit ownership.** `_capacitance_unit_token` only matched a STANDALONE `pF`
   token. Chinese-vendor panels print the unit inside the rotated axis title
   (`Capacitance(pF)`, `C-Capacitance(pF)`), one word in the PyMuPDF stream, so
   `_linear_capacitance_y_fit` never got its unit and every arithmetic ladder
   went uncalibrated. Titles are now accepted when the prefix is purely
   alphabetic — a numeric annotation (`Ciss=5000pF`) still cannot donate a unit,
   and conflicting units still refuse.

2. **A log axis fitted on an arithmetic ladder (silent-wrong).** `1000` and
   `10000` are decade-VALUED labels on a 0..10000 pF LINEAR axis. Two of them
   satisfied the `len(yd) >= 2` decade path, producing a TRUSTED calibration
   whose HYG030N10NS1P Ciss was **+20583 %** against its own spec table.
   `_decade_ladder_is_contradicted` now drops the log reading when the full
   label set itself passes the evenly-stepped/monotone/tight-residual ladder
   test and covers more labels than the decade subset. The disproof must be
   POSITIVE ladder evidence, not "a peer misses the log fit": OCR emits stray
   fragments (a `2` on top of `10³` on IAUTN15S6N025ATMA1) and a residual-only
   veto destroyed three already-verified extractions.

3. **Horizontal rule detection.** `_horizontal_gridline_candidates` opened the
   image with a `max(80, W/5)` horizontal kernel. That erases DOTTED grids
   (huayi/NCE: only the two frame rails survived), loses a solid rule that a
   curve LABEL interrupts (`Ciss` printed on GT020N10T's 12000 pF line leaves
   two sub-kernel fragments), and MERGES a near-horizontal trace into the rule
   it touches, dragging the center ~2 px (GT045N10T) — enough to fail the 1 px
   seating-fit gate. Replaced with per-row ink+span measurement, runs split
   into darkest plateaus, each plateau emitted separately. Two conventions are
   load-bearing: the center is `mean(rows) + 0.5` (row index = pixel TOP edge,
   matching the old `y + h/2`), and EVERY plateau of a run must be emitted —
   taking only the longest, or discarding runs thicker than 8 px, loses major
   gridlines on densely gridded log panels (AGM15T06C).

A two-decade log fit has an identically-zero residual and cannot falsify
itself, so the arithmetic disproof above is NOT sufficient on its own: dropping
one Y tick makes the arithmetic side unprovable and hands the axis straight back
to the 1000/10000 log reading. A `len(yd) < 3` reading must therefore be
CORROBORATED by every other label on the axis. A bare count floor is wrong --
Toshiba TPCC8105's OCR recovers exactly two Y labels and nothing else, and
banning two-decade fits outright broke it.

Corpus effect (120 C(V) panels over four review runs): 27 -> 15
`axis_calibration_untrusted` (12 panels recovered) with anchor agreement
Ciss/Coss within 0.1–5 %; HYG030N10NS1P corrected from -32 %/-42 % to
-2.1 %/+0.35 %. `run_capacitance_regression.py` failure list byte-identical
before/after; unittest suite 3 failures -> 2 (one pre-existing failure fixed),
errors unchanged.

Still untrusted and NOT extractable today: the raster-tick families where OCR
recovers only 1–2 of the four `10^N` Y labels (NCEP033/040N10, NCEP01T18,
NCEP030N12, GT120N10) and the ones whose X tick row OCRs empty (XRS80N10T,
SP010N04AGHTQ, SP010N02GHTQ, SP010N03BGHTQ). Grid geometry alone gives decade
SPACING but not which decade; pinning it from the spec-table anchor would
destroy the independence of the anchor validation. See
[[dsdig-capacitance-closed-bottom-frame]].
