---
name: cv-rank-is-not-identity-lane-assignment
description: The raster C(V) lanes were assigned by rank-in-column, so the bottom frame rail was served as Crss on every gray-grid panel and Crss climbed onto Coss once its own curve ended
metadata:
  type: project
---

Fixed 2026-08-08 (uncommitted at the time of writing) in `capacitance_traces.py`
+ `capacitance_pair_tracking.py`. This closes the "Still open" item left by
[[cv-full-span-grid-capture-hole]] -- the previous round made the tool DETECT
the GT020N10T frame capture, this one repairs the assignment.

**One root cause, two symptoms.** `_trace_candidates(..., "bottom")` returns
`centers[-1]` and the pair tracker took `centers[:1]` on a two-stroke column.
Rank is not identity:

1. `_remove_frame_residual_rails` was gated behind `_dark_grid_rule_evidence`,
   so on GRAY-grid panels the bottom rail survived as an extra stroke and won
   the Crss lane for the whole span. Silent -- `trace_validation_status ==
   "pass"` -- on GT048N10T, GT080N10T, GT085N10TH, GT035N12T, GT060N10T; the
   real Crss carried 500-2600 pF at 0 V and was entirely untraced. The frame
   exists on EVERY chart whatever ink the grid uses, so its removal must not be
   gated on the grid's colour.
2. With the rail gone, a column where Crss has decayed below resolution holds
   two strokes and `centers[-1]` is the COSS stroke. Crss climbed onto Coss
   (GT045N10T/TH: ~500 pF served at 100 V against a true ~10 pF) while Coss
   lost its lane. Fixed by reserving Coss's stroke: Crss is tracked last and a
   candidate within `PEER_STROKE_EXCLUSION_PX = 3.0` of Coss's y is withheld.
   Coss = Cds + Cgd > Crss = Cgd always, so they can never be one stroke.

**Three second-order traps, each of which cost a round:**

- `centers[:1]` on a two-stroke column assumed the vanished stroke was always
  Crss's. Backwards on exactly these panels -- Coss is then the LOWER of two,
  is never offered, and truncates mid-chart (GT048N10T Coss ended at 38 V of
  100 V). Widen to `centers[:2]`.
- Widening it alone REGRESSES IRFB4110G: the pair tracker's two-observation
  assignment is all-or-nothing, so a column the old single-observation branch
  used to feed now starves. The salvage branch must run whenever the joint
  assignment was rejected, not only when the column holds one stroke.
- The seed column is chosen among three-center columns. The rail used to pad
  almost every column to three, so the seed landed mid-plot BY ACCIDENT. With
  the rail gone the three-center run ends where Crss decays, and the last
  column of that run -- a steep Coss knee -- became the seed. The tracker
  starts there with a one-point, zero-slope history, so the next column is
  already out of reacquisition range and the trace dies at the seed
  (NCEP039N10M). `stable_three_center_columns` requires three centers across
  +-5 px so the seed is interior to a stable run.

**The failure direction is now right.** A Crss that cannot be resolved yields
`Crss_short_x_span` -- an explicit gap -- instead of a confident value taken
off the frame. NCEP039N10M now fails `Crss has too few sampled columns: 104`
where it used to "pass" with 414 columns of rail. That is correct and it is a
LOSS: `extract_trace_components` is all-or-nothing, so an unresolvable Crss
discards a perfectly good Ciss and Coss. Per-trace availability is the next
slice; it also blocks RX3P07BBHC16, whose Coss is correct and full-span but
withheld because Crss is short.

Corpus effect (47 panels, fugu2 HS+LS top-30): 5 silent-wrong Crss captures
repaired, 3 Crss-onto-Coss hand-offs repaired, 5 truncated Coss traces restored
to full span (NCEP065N10 27 V -> 100 V, XRS80N10T 25 V -> full), GT130N10F
promoted to `ok`, 1 panel correctly lost. `run_capacitance_regression.py`
failure list is a strict SUBSET of baseline (5 removed, 0 added); unittest
904 tests, 2 failures / 6 errors, unchanged from baseline.
