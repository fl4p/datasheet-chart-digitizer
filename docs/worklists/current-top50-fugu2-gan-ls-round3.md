# top50-fugu2-gan-ls packet — Fab round-3 feedback (2026-07-29)

Status: 3 fixes IMPLEMENTED with tests; 1 slice OPEN (plot-box x seating).

Source of record: `.vibe-drops/a3f6a2d1-top50-fugu2-gan-ls-001.review (2).json`
(exported 2026-07-29T07:16Z, 22 items; dropped three times byte-identically as
`288b47b6`/`a3f6a2d1`/`d841b07c`). Packet built at `08d4eda` = HEAD, so every
flag below reproduces on the current tree — no staleness discount this round.

Verdicts: **15 green · 5 rework · 1 flagged · 1 rework (dup)**.

## Closed by this round's review

All 11 EPC colored-vector panels are human-GREEN, confirming the legend-color
identity slice (`0aeef19` + `564f95d`) and the vector-gridline seating in
`a48ae35`. `docs/worklists/current-epc-color-capacitance.md` can stay closed.
The 4 Infineon fig11/fig12 vector panels and ISC0802NLSATMA1 are GREEN too.

## A. Wrong panel digitized — 2 parts — FINDER, highest severity

`infineon/IAUTN12S5N018GATMA1/fig10`, `infineon/IAUTN12S5N018TATMA1/fig10`
(rework): "wrong diagram digitized, its the one above".

**Root cause (confirmed, not inferred).** Page 8 is a 2x2 layout: "10 Typ.
capacitances" top-right, "12 Typ. avalanche characteristics" directly below it.
`infer_grid_regions_from_h_rules` returned only TWO regions, each 643 pt tall,
spanning a whole column — so the capacitance caption could bind the avalanche
plot. The emitted panel text proved the merge (it carried both captions), and
the crop was 679x1678 against ~679x832 for single-panel crops.

The bridging chain in the right column:

| y (pt) | what | gap | verdict |
|---|---|---|---|
| 337.3 | diagram 10 grid bottom | | |
| 383.3 | diagram 10 **outer frame** bottom | 46.1 | same panel |
| 410.3 | diagram 12 **outer frame** top | 27.0 | same panel — **no check ran** |
| 440.9 | diagram 12 grid top | 30.6 | same panel |

Two compounding defects in `grid_rows_belong_to_same_panel`:

1. `gap <= 28.0` returned "same panel" **without scanning for a caption**. The
   caption "12 Typ. avalanche characteristics" sits at y=399.8, inside that
   27.0 pt gap, because the two panels' own frame rules bracket it. A boundary
   check that answers "fine" when it has not looked is the anti-monotone
   false PASS.
2. Caption recognition only knew `Figure`/`Fig`/`Diagram`. Infineon numbers
   panels bare ("12 Typ. ..."), so even a scan would have seen nothing.

**Fix** (`finder_grid_geometry.py`): the caption scan now runs for every gap
inside the 74 pt cutoff, and `_starts_bare_numbered_caption` recognises the
bare-number convention. Its discriminators against in-plot text are positional:
the number must OPEN its text line *within the column band* (page-wide lines
carry the left column's number first — this is why the left column split while
the right did not in the first attempt), and the title word must follow within
14 pt (which is what separates a caption from a Y tick sharing a line with a
far-right legend entry, and from `f = 1 MHz`, whose "1" never opens a line).

Result: 4 regions per page, panel text clean, and the correct chart digitized
with correct three-trace identity (overlay-verified). Tests:
`tests/test_finder_stacked_panel_caption_boundary.py` (14).

### A/B history — the patch regressed twice before it was safe

Corpus: 1175 PDFs resolved from every backlog part with a digitized chart,
6730 shared panels. Side A = clean HEAD, side B = HEAD + this patch only.
**None of the four fixed parts is in that corpus** (it is built from parts
already in the review backlog), so the A/B measures COLLATERAL ONLY and cannot
validate the fix; the fixes were verified directly on their targets.

- **Round 1 — 38/6730 bbox deltas, 34 of them regressions.** onsemi
  NTMFS/NVMFS capacitance panels were truncated below their own x tick labels
  (NTMFS010N10GTWG p4 d7 crop 630x458 -> 629x351, losing the bottom decade and
  the x axis title). Cause: the "caption opens its line" test was scoped to a
  band derived from the bounding rule pair. At x=[138.6,295.2] that band starts
  at 107.3 and clipped the `f =` off `f = 1 MHz`, so the remaining `1` looked
  like it opened the line. Replaced with a band-independent 40 pt left
  clearance (measured 2.2 pt for that `1` vs 85.2 pt for the real cross-column
  `12`).
- **Round 2 — 4 deltas, 2 of them still regressions.** `FDB15N50` p4 d3
  transfer lost its top (y0 305.7 -> 352.1, dropping the 60/50 y ticks) and
  `IRF644S,_SiHF644S` p3 d904 gate_charge lost its bottom x tick row
  (y1 661.1 -> 632.9). Two distinct causes: (a) a Y tick label beside an in-plot
  annotation is geometrically identical to a caption (`50` ~5 pt left of
  `VDD = 100V`), so geometry alone can never separate them; (b) removing the
  short-gap fast path exposed the PRE-EXISTING figure-keyword rule to in-plot
  cross-references — IRF644S figure 6 prints "For test circuit see figure 13"
  inside the plot.
- **Final shape.** Three changes together: require a caption phrase from the
  same vocabulary `find_charts._caption_starts` uses; require a `figure`
  keyword to open its line; and bound ALL new scanning to gaps <= 28 pt so
  wider gaps keep byte-identical pre-change behaviour. The patch can therefore
  only ADD splits in the previously-unscanned short-gap region, which bounds
  the collateral by construction rather than by threshold tuning.

The two remaining round-2 deltas were pre-existing finder false positives,
present identically on both sides, and neither serves a value:
`PSMN2R4-30YLD` p2 d13 binds a gate_charge caption to a spec-table row on a
page with no chart (the served Vpl=2.62 comes from the real p9 d13 chart, so
the false panel is never selected), and `FDP52N20` p5 d7 binds a "Breakdown
Voltage Variation" caption to Figure 9's Safe Operating Area plot
(`digitize-breakdown-voltage` returns `panels: []` with an explicit
non-monotone-Tj-axis error). Recorded here as known noise; not claimed.

## B. Y ticks + chart bbox — 2 parts — PARTLY FIXED

`infineon/IAUA170N10S5N031AUMA1/fig10`, `infineon/IAUTN12S5N017ATMA1/fig10`
(rework): "y-axis ticks wrong, chart bbox wrong." Same oversized crop as A, so
part A's fix applies; two further defects were behind it.

**B1 — spurious decade 0 (FIXED).** `_parse_y_decades_from_chart_text` pairs
adjacent number TOKENS, but `_number_tokens` has already stripped the prose
between them. So the caption's own "10" paired with the condition line's "0"
(`V GS = 0 V`) and appended decade 0: the ladder spanned 10^0..10^5 on a
10^1..10^5 axis, displacing every tick by a decade. Fix: require the two
numbers to be adjacent **in the text** (blanks only), which is exactly what a
split `10 5` label looks like. Tests:
`tests/test_capacitance_text_decade_ladder.py` (4).

This unlocked more than the ladder. With the correct decade set the axis leaves
the `text_order_normalized_plot_extent` fallback and reaches a real
**gridline fit**: `y_source=gridline_fit_from_text_decades`,
`grid_resid_px=0.16`, `grid_span=0.995`, and `axis_calibration_trusted: True`.
Fab's "y-axis ticks wrong" is resolved — the decade lines now land on the
printed rules.

**B2 — plot-box x seating (OPEN, next slice).** The only remaining refusal is
`all_traces_left_edge_gap`, and it is the guard working correctly. `find_plot_box`
returns `x0=36, x1=721` (the **panel border**) with `y0=125, y1=769` (the
**plot frame**): the y extent is right, the x extent is not. The panel-border
verticals span y 38..894 and so overrun the horizontal rules' extent
(125..769), yet they still set x0/x1, putting 0 V at the panel edge instead of
the y-axis at x≈165. Traces then start ~19% inside the box.

Fixing it should clear the refusal and make these four panels servable. The
principled rule is mutual closure — a vertical may only bound the plot box if it
spans about the same y range as the horizontal rules do, and vice versa. NOT
done here on purpose: `find_plot_box` in `capacitance_traces.py` is the most
widely shared box detector in the library, so it needs its own slice and its own
full-corpus A/B rather than riding along with three unrelated changes.

## C. Crss "gap" at 30..35 V — 1 part — FIXED (was a SERVED WRONG VALUE)

`crmicro/CRSM038N10N4/fig06` (flagged): "crss has a gap at 30..35V, fix".

**This was the only non-fail-closed item in the packet.** It carried
`status: ok`, `trace_validation_status: pass`,
`physical_output_available: true`, `axis_calibration_trusted: true` — the wrong
Crss segment was servable. (The packet's `extraction_status: gap` came from
`qoss_validation=reference_unavailable`, a separate sub-contract; the C(V)
curves themselves were servable.)

It is not a gap: the Crss band **snaps onto the 100 pF gridline**. Extracted
Crss jumps from 125.3 pF at 29.3 V to 99.7 pF at 30.1 V (13 px), sits flat on
the rule to ~35.5 V, then rejoins — a nonphysical step, ~20% off. Coss does the
same, more mildly, on the 1000 pF rule over ~48-55 V.

**Root cause.** The 3000/1000/100 pF rules are printed in trace-dark ink (row
occupancy 0.94 at `<90`) while the vertical grid is light gray (max interior
column occupancy 0.114). `_dark_grid_rule_evidence` requires >=3 distinct
full-span dark rules in BOTH orientations — deliberately, so a flat trace riding
a decade line cannot trigger the opening (XRS200N12T) — so it returned False, no
rails were blanked, and the dark rules stole band slots wherever a curve went
shallow. Meanwhile `source_absent_columns: 0` for Crss: grid ink IS ink, so the
source-support guard cannot see this class at all.

**Fix** — new `grid_rule_capture_diagnostics` in `capacitance_source_support.py`,
gated in `trace_validation_summary`. A run is a capture only when all of:

- >= max(10, 3% of width) consecutive columns pinned within 1 px of one row,
  with the run's own y spread <= 2 px;
- the run departs the trace's own approach trajectory by >= 4 px (fit on the 14
  columns outside the run, extrapolated only to the ADJACENT run edge — a
  distant midpoint read 48 px on a steep approach);
- the rule is evidenced in columns where this trace is NOT (>= 0.80 occupancy
  over >= 20 eligible columns).

The last two conditions exist because ink alone cannot separate capture from
coincidence: a flat trace darkens its own row across the full width (Toshiba
TPH2R70AR5 Ciss measured 1.000 outside its run), and Toshiba draws grid and data
in one ink. Columns inside a shared-collapse span are skipped so the
shared-collapse gate keeps sole ownership of them.

Per the guard checklist, an unevaluated check is **unverified, not clean**: an
absent or `evaluated: False` diagnostic adds
`grid_rule_capture_unevaluated`, because nothing else in the pipeline would
notice grid capture. Calibration: CRSM038N10N4 now refuses
(`Crss_captured_by_grid_rule`, dev 11.0 px, off-trace occupancy 0.935,
`physical_output_available: False`); the 4 human-GREEN vector panels and both
Toshiba panels gained no new reason. Tests:
`tests/test_capacitance_grid_rule_capture.py` (11, incl. monotonicity in run
length and both unevaluated paths).

Residual: the milder Coss ride on the 1000 pF rule (wobbles 149-153 px, y spread
4) stays undetected at these thresholds. Widening the capture tolerance is
self-defeating — it swallows the approach columns and inflates the spread — so
recovering it needs the rail-blanking slice below, not a looser guard.

### Digitizer collateral A/B — the guard found three MORE served wrong values

922 capacitance panels, same frozen finder inputs (side A of the finder A/B),
baseline vs candidate libraries both based on `b86cb3c`. **0 newly served, 4
newly refused, every refusal verified against the source overlay:**

| panel | was | verdict |
|---|---|---|
| `FDPF3860T` p4 d5 | `ok`, served | Ciss dead flat on the 500 pF rule for 156 columns (35% of span) while the real curve arcs 15-25 px above |
| `FDP050AN06A0` p6 d13 | `ok`, served | Coss abandons a steep diagonal and runs flat for 78 columns |
| `CRJF190N65GCF` p5 d11 | `ok`, served | Coss jumps up onto a rule through the printed "Coss" label, then back |
| `NVTFS007N08HLTAG` p5 d7 | `ok`, served | see the fabricated-ladder case below |

18 further panels gain a `*_captured_by_grid_rule` reason without any
servability change — they were already refused for other reasons, so a false
positive there costs nothing.

**A false-positive class the collateral run caught, and a wrong fix for it.**
Round 1 of this A/B refused 7, and `XR100N20G/H/T` (one chart, three variants)
were false positives: a flat Ciss correctly seated on its own stroke, with a
rule beside it and a slightly mis-seated approach yielding a 4.0 px deviation.
The first attempted discriminator — "is the abandoned curve still visible in
those columns?" — made things WORSE: it lost `FDP050AN06A0` (a real capture)
while keeping all three false positives, because printed gridlines themselves
count as unclaimed strokes. It was reverted.

What actually separates them is calibrated, not invented: real captures measure
6.0 / 11.0 / 11.9 / 17.0 px of approach deviation, the false positives measure
exactly 4.0 — the noise floor of a linear fit through a FLAT trace. The floor is
now 5.0, between the two groups, and all six labelled panels come out right.

## C2. Fabricated two-decade axis — FIXED, and it was serving 1000x-wrong values

The decade-ladder fix (B1) turned out to guard a second, worse serve. Panels
whose Y axis is labelled with SI/comma forms rather than `10^N` — `10K 1K 100 10`
(NVTFS007N08HLTAG) or `10,000 1,000 100 10 1` (IXFA16N60P3) — are unreadable to
the mantissa/exponent pair parser. Baseline nonetheless produced
`y_decades=[0.0, 1.0]` from prose-adjacent tokens, fitted those two fabricated
decades to two gridlines, and reported `y_grid_residual_px = 0.0` — a perfect
fit to invented ticks, which is exactly the "signature is a PROXY, not the source
of truth" trap. NVTFS007N08HLTAG served **Ciss 6.87 pF, Coss 6.64 pF, Crss
4.85 pF** on a chart whose axis runs 10-10000 pF: off by ~1000x, with
`status: ok` and `physical_output_available: true`.

With the ladder fix the spurious pair is gone, fewer than two decades remain, and
both panels refuse honestly (`axis_calibration_untrusted`). IXFA16N60P3 was
already refused for a span reason, so only NVTFS007N08HLTAG was a live serve.

Follow-up (not done): read SI-suffix and comma-grouped Y tick ladders
(`10K`/`1K`, `10,000`/`1,000`) so these panels calibrate instead of only failing
closed. That is additive recovery, not a correctness fix.

## Not covered: the raster-page Infineon fig10 variant (packet 931613a3)

A later packet flagged four more Infineon fig10 panels as axis-untrusted
(IAUTN15S6N025G/A/T, IAUCN10S7N021A — "no review labels / cropped source y
labels"). Checked end-to-end, clean HEAD vs this tree: **byte-identical, all
four still `overlay-review-required` with `y_decades=None`.** Same family, a
different root cause, NOT fixed here.

On those parts page 8 carries 32 words in total and **zero** words in the chart
band (y 100..400) — the panels are pure raster with no text layer, so no ladder
can be parsed and the refusal is honest. Fab's "no review labels" is the overlay
symptom of an untrusted axis (the verify overlay only draws consumed ticks when
calibration is trusted), the same presentation as §C of
`current-top50-fugu2-refresh-feedback.md`. My fixed group DID have a text layer;
there the bug was the fabricated decade 0.

Recovery for these needs OCR tick recovery for this Infineon raster page variant
(the tesseract stratum), not the text parser. Diagnosed, not claimed.

## Verification summary

- Finder A/B: 1175 PDFs, 6730 shared panels — **0 added, 0 dropped, 0 bbox
  deltas, 0 kind changes** (third cycle; the first two are recorded above).
- Digitizer collateral: 922 panels — 0 newly served, 4 newly refused, all four
  justified against source overlays.
- Neither corpus contains the four Infineon targets or CRSM038N10N4, so both
  A/Bs measure COLLATERAL ONLY. The fixes themselves were verified directly on
  their targets, end-to-end from the PDFs.
- Tests: 14 finder-boundary + 13 grid-capture + 4 decade-ladder = 31 new.
- Full suite, measured correctly: clean HEAD **5 failed / 815 passed**, this tree
  **4 failed / 852 passed** — the same set minus
  `test_annotate_pdf::test_csd13385_embeds_exactly_the_five_supported_charts`,
  which these changes FIX (verified deterministic: 2/2 fail on HEAD, 2/2 pass
  here). No new failures. The four remaining are pre-existing and unrelated.

**Methodology note for the next agent.** `.venv` holds an EDITABLE install
pinned to this checkout's `src`, so `pytest` run from a `git worktree` that
shares the venv still imports the MAIN tree's code. Baseline suite runs must set
`PYTHONPATH=<worktree>/src` (verified to take precedence) or they silently
measure the wrong tree — an earlier "3 failures pre-exist at clean HEAD" reading
here was wrong for exactly that reason. The A/B drivers were unaffected because
they set `sys.path` explicitly.

## D. Coss snaps onto Ciss — 2 parts — CORRECTLY FAIL-CLOSED, recovery open

`toshiba/TPH2R70AR5/fig811` (rework): "coss snaps to Ciss. Ciss not centered on
bold curve line, distracted by grid?"; `toshiba/TPM2R20AR5/fig811` (rework):
"coss snaps on Ciss, should stay on the bold curve line".

Rendered the source at 400 dpi (`pdftoppm -r 400 -f 6 -l 6`): Ciss and Coss
genuinely overlap within stroke width from 0.1 V and only become separable
around ~1 V. Both panels are already `status: unverified`,
`physical_output_available: False`, reason
`ciss_coss_shared_trace_orphans_source_branch` — the detector is right, and it
even names the unclaimed branch. Extraction holds them merged to ~1.66 V
(shared spans x 108-228, 232-262), a little past the point where the lower
branch is resolvable, which is what Fab is seeing.

Work needed is recovery, not detection: claim the lower branch as Coss as soon
as two stroke centers resolve. Same family as the v5 bridge-anchor work in
`dsdig-cv-shared-collapse-crossing-correction`. No wrong values served.

## Open slices, in priority order

1. **Plot-box mutual closure** (B2) — unblocks 4 Infineon panels; touches the
   shared `find_plot_box`, so it needs its own full-corpus A/B.
2. **Stroke-preserving rail blanking for mixed-darkness grids** (C residual) —
   CRMicro-style charts where horizontal rules are trace-dark but the vertical
   grid is light. Would let the captured segments be recovered rather than only
   refused. Also owns the goford bottom-frame sub-case still open in
   `current-top50-fugu2-refresh-feedback.md` §B.
3. **Shared-collapse early separation** (D) — Toshiba log-log low-V branches.
