# ST V(BR)DSS temperature caption and grid ownership

**Status:** STD14 v2 and the STF7N60M2 adjacent-frame follow-up are focused
dual-agent-GREEN. Authoritative full-corpus finder/breakdown A/B and human
scale review remain. No commit/push and `human_verified=false`.

## Defect

ST `STD14NM50NAG` page 6 Figure 11, `Normalized V(BR)DSS vs temperature`,
is a supported breakdown-temperature chart but default 220-DPI annotation did
not detect it.

Two independent finder defects combined:

- classification recognized prose `breakdown voltage` but not the standard
  compact device symbol `V(BR)DSS`;
- at 220 DPI, the raster rule detector grouped the wide enclosing page-cell
  rails with the narrower inner plot grid. That produced a full-cell region,
  which was then rejected as an overlapping neighbour. At 120 DPI the bad
  region disappeared and a loose synthetic fallback happened to recover the
  chart, making the miss DPI-dependent.

## Guarded design

- Classify a title containing compact `V(BR)DSS` as breakdown only when the
  title/context also says temperature.
- Group horizontal grid rules only when their widths are compatible. A wide
  enclosing cell rail must not expand the owned inner plot grid.
- For a numbered V(BR)DSS caption, prefer the single immediately following
  grid only when it is materially nearer than any preceding grid. This blocks
  the remote `Tj` axis on Figure 9 from reversing Figure 11 ownership.
- Keep `find_charts.py` at or below the project 1500-line limit.

## Acceptance

- At the annotation-default 220 DPI, Figure 11 is emitted exactly once as
  `breakdown_voltage`, with bbox approximately
  `[207.8, 552.2, 385.9, 719.0]`, containing its own V(BR)DSS/Tj scales and
  curve but no Figure 9 chart.
- Existing Figures 4, 5, 6, 7, 9, and 10 remain byte-identical in the focused
  same-source A/B; Figure 8 is a VGS(th)-temperature chart and is intentionally
  unsupported.
- Wide page-cell rails and narrow plot grids form separate groups in a
  calibrated negative fixture.
- The recovered breakdown extractor must either emit a source-faithful overlay
  or fail closed explicitly. Detector recovery alone does not make the item
  GREEN.
- Recover a curve encoded as a dark pure-filled path only in the existing
  stroke-empty fallback. White fills, rectangle-only masks, short annotations,
  and multiple full-span candidates remain refused.
- For normalized charts, accept the table minimum only when a V(BR)DSS row's
  number is positioned in an evidenced `Min` column and its `V` unit is in the
  evidenced `Unit` column on the same row. Condition values and neighboring
  rows remain ineligible.
- Run authoritative full-corpus finder A/B and obtain independent/human review
  before shared-finder acceptance.
- Bind every consumed numeric tick to its observed printed grid intersection,
  refit at those observed centers, and emit a real per-tick fail-if-diverge
  assertion. Moving only the overlay marker while leaving the numeric mapping
  label-centered is not acceptable.
- Serve no more than one arithmetic X interval beyond the last labeled tick.
  Preserve the full source trace as diagnostic evidence and explicitly report
  withheld source points; do not numerically extrapolate through two unlabeled
  intervals merely because the printed stroke continues to the frame.

## Focused evidence

- Baseline default 220-DPI find: 6 panels; Figure 11 absent.
- Candidate default 220-DPI find: 7 panels; the only addition is page 6 Figure
  11 `breakdown_voltage` at the owned bottom-center grid.
- Read-only diagnosis proved the plot curve is the page's only full-span dark
  centerline but is encoded as PDF drawing type `f`; the existing fallback
  admitted only `fs`. It also proved the page-3 source table owns `500` in the
  `Min` column, while the existing parser recognized only dash-slot rows.
- Frozen packet: `/private/tmp/dsdig-st-std14-breakdown-v1`. Candidate/repeat
  annotated PDFs are byte-identical (SHA-256
  `5c610d71cde2efdfc6eb361dc01c6800a1b33a0b53899822a53eab4c9ffdb50b`),
  as are the breakdown overlay (SHA-256
  `0bd04459c72e581fb0020bf11a128b2fde562b778fabd4fb061fac876b3614e3`)
  and embedded overlay.
- The original v1 worklist count of 374 points was generated at a different
  render setting; the frozen 220-DPI v1 CSV actually contains 456 data rows.
  This correction does not validate the v1 endpoint contract.
- The recovered full source curve covers approximately -50..150 C,
  yields 499.92 V at 25 C and 471.3 mV/K, and verifies against the source-owned
  500 V page-3 minimum. The overlay follows the printed curve across the full
  frame; endpoint contact retains an explicit review warning.
- Focused pure-fill, positioned-Min, wrong-unit-column, and white-fill negative
  tests pass (4 tests). Two unrelated panels in the same PDF remain explicit
  per-panel errors (transfer axis identity and body-diode Y calibration); they
  do not abort annotation or affect the Figure 11 result.
- Strict v1 review:
  `/private/tmp/dsdig-st-std14-breakdown-v1/reviews/agent-std14-breakdown-v1-001.codex-hxy-breakdown-review.json`
  (SHA-256 `a57e4a30802080086aa194075f1730657060644e2caeb8bed78992e1e0e16b52`).
  Pure-filled-trace recovery and positioned Min/Unit ownership are GREEN, but
  the item is UNVERIFIED: v1 used label-centered calibration without exact
  grid-center assertions and served through two unlabeled intervals to 150 C.

## Exact-center and bounded-endpoint v2 evidence

- Packet: `/private/tmp/dsdig-st-std14-breakdown-v2`; candidate and repeat were
  generated independently at annotation-default 220 DPI.
- Candidate/repeat annotated PDFs are byte-identical (SHA-256
  `aa19f290853ddd2d064a083ac42a5f770f59de3609d0e694be059f3291b215da`).
  The standalone and annotation overlay are byte-identical (SHA-256
  `2aebcb1c8111ac3248f8d174c771e9063f92db2dd1e84901488e5f0973fd5016`),
  embedded overlays are byte-identical (SHA-256
  `8deaccc72cc61b9138812ebcbd670531b612a2b024d816bae0b784f9632f7c2f`),
  and CSVs are byte-identical (SHA-256
  `14819900af7f03a3cbeb42e869eb7fd4e2466238d4e1bc310ddf9e9db425b186`).
  Canonical physical result JSON after removing only absolute artifact paths is
  `eb01bd641c9b875082c88e529f26ffc54509a9a55c7f120c412cd183a9134b4b`
  for both runs.
- The 456-point full source trace remains counted for provenance. The served
  trace is 398 points through 125.4 C; 58 source points from the second
  unlabeled interval are explicitly withheld. The overlay and CSV use the
  served trace, while the source trace still drives the frame-touch warning.
- All seven X labels and ten Y labels own unique observed gridlines. Every
  emitted inverse-fit assertion is within one pixel; maximum errors are
  0.429 px on X and 0.875 px on Y. The numeric mapping and review crosshairs
  therefore consume the same printed grid evidence.
- Result remains `verified`: V(25 C)=499.912 V against the source-owned 500 V
  minimum, slope=477.99 mV/K, and served Tj range=-50.5..125.4 C. The raster
  frame-touch warning remains explicit and must be reviewed rather than
  silently cleared.
- Focused STD14 end-to-end plus exact-center, duplicate-ownership, pure-filled,
  positioned-Min, and bounded-interval tests pass. The full current-tree file
  run has 55 passes plus four failures/errors in other in-flight shared
  finder/vector fixtures (IPP040N06N and STF7N60M2); those are not treated as a
  collateral oracle and must be isolated or repaired before shared landing.
- Source SHA-256 is
  `c19eaef0e518466196ff2d79b1166def709bee844db53b53040afe480e0f3284`;
  test SHA-256 is
  `012ff44778971f01c87ed63f76f90914ba8b78ce1d0e6beac45d6785c94e65d0`.

V2 is a frozen focused item whose required independent review is recorded
below. Full-corpus breakdown A/B remains mandatory because grid-centered
refitting moves values; no shared acceptance or landing is implied.

- Independent v2 review:
  `/private/tmp/dsdig-st-std14-breakdown-v2/reviews/agent-std14-breakdown-v2-001.codex-hxy-breakdown-review.json`
  (SHA-256 `411e9c3a5ed4df5190bec957aaece38d3006b12a5b62e7ec517e7de07819519b`).
  Focused patch and Figure 11 are agent-GREEN with the explicit raster-frame
  warning retained. The reviewer independently reproduced all hashes, all 17
  unique grid-center assertions, the 456->398+58 point conservation, zero CSV
  rows/red pixels in the withheld interval, <=2 px source distance over the
  served trace, and the positioned 500 V Min/Unit ownership. Shared acceptance
  remains UNVERIFIED pending the authoritative full-corpus A/B; agents do not
  set `human_verified`.

## Post-packet collateral repair

After the immutable v2 review completed, the live tree added a
breakdown-specific rejection for full-span vector components whose total
vertical response is below 2% of plot height. This removes internal horizontal
gridlines from the exact-one-curve count without changing the shared vector
edge detector. The IPP040N06N regression and seven curve-count guards pass;
the full breakdown file improves from 55 passes plus four failures/errors to
59 passes plus the existing STF7N60M2 finder/raster-frame failure. This
follow-up has not been folded into the reviewed v2 packet and still needs its
own corpus delta/review before landing.

## STF7N60M2 adjacent-frame follow-up

STF7N60M2 page 6 Figure 11 exposed a near-equal raster ownership tie: the
caption sits between Figure 9 above and its own breakdown frame below, and the
finder emitted a two-row bbox `[85.907,326.817,263.165,709.921]`. A numbered
breakdown caption may now bind a same-column closed vector frame within 30 pt;
in a four-point near tie the following frame wins. Its local axis-gutter
expansion produces one-row bbox `[86.939,548.821,256.542,711.318]`.

At 220 DPI, the valid Y grid is rasterized just nonuniformly enough for ordinary
least squares to miss a center by 1.02 px. A constrained linear refit now solves
the exact one-pixel feasibility intersection; it never widens the tolerance and
refuses an empty intersection. The raster grid still supplies scale centers,
while the positively closed vector rectangle supplies the true outer frame.
Thus the green frame reaches the printed 150 C edge, the served red trace ends
after one unlabeled interval near 125 C, and 422 source points conserve as
364 served plus 58 explicitly withheld.

Frozen packet: `/private/tmp/dsdig-stf7-breakdown-frame-v1`. Candidate/repeat
charts, physical result JSON, annotated PDF, overlay, embedded overlay, and CSV
are deterministic. The only finder delta across STF7N60M2, STD14NM50NAG, and
SPD03N50C3ATMA1-HXY is STF7 Figure 11. STD14 and SPD03 physical result objects
remain byte-identical to baseline. STF7 verifies at V(25 C)=599.743 V,
609.65 mV/K; exact-center maxima are 0.535 px X and 1.000 px Y. The full
breakdown suite plus ownership guards is 65/65 GREEN.

Opus independently reviewed the frozen packet without carrying forward an
item verdict:
`/private/tmp/dsdig-stf7-breakdown-frame-v1/reviews/opus-stf7-breakdown-frame-review.json`
(SHA-256
`6ffd54217de7a3ab94e0f9870d04390fa7d1202def210fd9e2935f6bb572f979`).
The review independently confirmed the one-row Figure 11 ownership, removal of
the Figure 9 merge, exactly one finder delta, byte-identical STD14 controls,
422=364+58 point conservation, the ~125 C served endpoint with black-only
source continuation to 150 C, exact-center maxima, and candidate/repeat
determinism. This is focused agent-GREEN only; full-corpus finder/breakdown A/B
and human review remain required.

A second independent strict review is recorded at
`/private/tmp/dsdig-stf7-breakdown-frame-v1/reviews/agent-stf7-breakdown-frame-v1-001.codex-hxy-review.json`
(SHA-256
`bd1cc0f2e598215d18d3189f3aa6d90e901ffce0ba858028eb1857d44a54048c`).
It hash-bound the frozen candidate source, reran the exact 65-test gate, found
every rendered served-trace column at zero-pixel distance from the original
dark source stroke, independently verified the 600 V table ownership and
599.743 V result, and confirmed byte-repeat target artifacts plus unchanged
STD14/SPD03 controls. Its focused item and patch verdicts are GREEN with
`human_verified=false`; shared-finder full-corpus acceptance remains explicitly
unclaimed.
