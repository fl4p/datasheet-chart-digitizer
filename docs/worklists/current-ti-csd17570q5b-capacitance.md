# TI CSD17570Q5B capacitance panel ownership

**Status:** deferred when the user supplied CSD13201W10 as the next explicit target;
no commit/push and `human_verified=false`.

## Defect

`dsdig annotate` detects page 5 Figure 5 (`Capacitance`), but the finder crop also
contains the right rail of neighboring Figure 4 plus page-divider rails.  The
shared dense-grid plot detector takes the global minimum and maximum vertical
strokes, producing a synthetic rectangle across two panels.  Vector curves then
appear to span only 72% of that false box, raster fallback follows horizontal
rails, and the item correctly remains `unverified` instead of being embedded.

## Required direction

1. Keep the shared `find_plot_box` contract unchanged for transfer, diode, and
   other callers.
2. In the capacitance-only wrapper, retain a dense result when its four sides
   close.  If its side rails do not close, search for one source-owned rectangle
   whose top/bottom horizontals share endpoints and whose vertical cohort shares
   those same top/bottom endpoints.
3. Require at least six matching vertical rails for this dense refinement.  Crop
   borders, page dividers, neighbor rails, partial grids, and ambiguous rectangles
   must not become plot sides.
4. Reuse the existing vector color extraction, assignment, axis calibration,
   validation, and serialization unchanged once the owned box is recovered.
5. Preserve the sparse four-rail recovery and all established negative fixtures.

## Acceptance

- CSD17570Q5B Figure 5 recovers only its printed plot frame, not Figure 4 or the
  page divider; Ciss/Coss/Crss remain on their blue/green/red source strokes.
- Position/grid axis calibration consumes the Figure 5 ticks and physical output
  is served only if every existing validation gate passes.
- Add a synthetic neighbor/page-divider fixture proving the owned dense frame is
  selected, plus an ambiguous/no-closure negative that preserves fail-closed or
  legacy-unverified behavior.
- Run focused capacitance tests, byte-repeat the target annotation, `qpdf --check`,
  and obtain an independent agent review.  Then run the frozen full capacitance
  corpus A/B before landing; any unrelated box/point/identity/status movement is
  RED.  Agents never set `human_verified`.
