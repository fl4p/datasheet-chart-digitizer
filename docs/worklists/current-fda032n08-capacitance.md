# FDA032N08 sparse-grid capacitance recovery

**Status:** implementation in progress; no commit/push and `human_verified=false`.

## Defect

`dsdig annotate` detects FDA032N08 page 3 Figure 5 as `capacitances`, but the
capacitance extractor refuses it with `could not find plot grid verticals; found 3`.
The logarithmic plot has a complete source-owned frame and decade rails, while its
minor gridlines are dotted and disappear under the established morphology. The
generic six-vertical gate therefore rejects a valid chart before vector extraction.

## Required direction

1. Keep the shared `find_plot_box` six-vertical contract unchanged for transfer,
   diode, and other callers.
2. Recover the capacitance panel only from positive closure evidence: the long
   top/bottom/left/right rails must close the same inner rectangle. Additional solid
   decade rails strengthen the evidence when present, but are not mandatory because
   some raster charts draw every inner gridline as a dashed stroke.
3. Reject crop/page borders, missing sides, partial rails, and ambiguous rectangles.
4. Reuse the unchanged vector trace extractor, assignment, axis calibration, and
   fail-closed validation after recovering the box.
5. Preserve every unaffected chart's box, points, identity, status, and overlay.
6. Raster extraction must suppress only the evidenced outer frame rails in both
   sparse- and dense-grid regimes; a top/bottom rail can never become Ciss/Crss.

## Acceptance

- FDA032N08 Figure 5 produces source-faithful Ciss/Coss/Crss points and an embedded
  overlay at the evidenced frame `(252, 68, 840, 489)` in the 220 dpi crop.
- The three curves remain on their own printed strokes from 0.1 V through 80 V;
  axis tick round-trips and physical values remain trusted.
- Missing-bottom and outer-crop-border fixtures fail closed.
- HYG050N13NS1W Figure 9 exercises the two-side-rail case: its dashed inner grid
  cannot satisfy morphology, while its four solid source-frame sides must recover.
- Run the full frozen capacitance corpus A/B. Every delta must have exact closed-frame
  evidence and microscopic review; any unsupported box/point/status movement is RED.
- Freeze a byte-reproducible annotated PDF, run `qpdf --check`, obtain an independent
  agent review, then require Fab's human verdict. Agents never set `human_verified`.
