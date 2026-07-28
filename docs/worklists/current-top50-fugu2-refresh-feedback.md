# top50-fugu2 refresh packet — human feedback triage (2026-07-28)

Status: OPEN — triaged, not yet sliced.

Source of record:
`/Users/fab/dev/pv/pwr-mosfet-lib/out/fugu2-100v-LS1p/coss-review-top50-2026-07-28-refresh/top50-fugu2-ls-missing-curves-001.review.json`
(exported 2026-07-28T16:07Z, 46 items; persisted to that packet's
`review-backlog/MANIFEST.zz-human-feedback.top50-fugu2-ls-missing-curves.jsonl`).
Packet built at dsdig `db361e8` (clean worktree).

Verdicts: 18 green · 19 flagged · 6 rework · 3 pending.

**Safety note:** every flagged/rework item was already fail-closed by the
pipeline (`status` gap / extraction_error / finder_gap). All six
`extract_ok=true` rows are human-GREEN (infineon fig11 ×6). No wrong values
were servable. Everything below is recovery work, not a served-value incident.

## A. Wrong panel bound to a capacitance caption (1 part) — finder, highest severity class

- `huayi/HYG018N10NS1P/capacitance/extraction-error` — "this is not a c(v) chart".
- Page 5 is a 2×2 grid, captions ABOVE their charts. The "Figure 9:
  Capacitance Characteristics" caption (bbox y≈325–336 pt) bound to the plot
  ABOVE it (Figure 7, RDS(on) vs Tj, bbox y≈129–291) instead of its own chart
  below. Sibling HYG030N10NS1P (near-identical layout) binds correctly
  (bbox y≈335–532). `infer_grid_regions_from_h_rules` finds NO grid for huayi's
  dotted C(V) grids on either part, so the binding comes from the
  vector-frame/synthetic caption fallbacks — divergence is inside that chain.
- Cheap guard regardless of root cause: HYG018's emitted panel has **empty
  panel text** — a caption-bound `capacitances` panel whose crop text carries
  no capacitance evidence (pF / Ciss / C-Capacitance) should be refused or
  re-bound rather than shipped as evidence.

## B. Trace bands snapping to gridline/frame with identity shift (5–6 parts) — digitizer

- `st/STP310N10F7/fig08` (rework): "Crss ignored, Coss as Crss, Ciss as Coss,
  gridline as Ciss" — red trace sits exactly on the 14000 pF gridline.
- `mcc/MCP100N10Y-TP/fig05` (rework): same shift-by-one wording.
- `onsemi/FDPF2D3N10C/fig08`, `onsemi/FDP2D3N10C/fig08` (rework): "crss on
  gridline, Coss on Crss curve, Ciss looks right" — partial shift.
- `goford/GT035N12T/fig05`, `goford/GT023N10T/fig05`, `goford/GT045N10TH/fig05`
  (flagged): Crss snaps to the bottom frame/border or the Coss curve while
  Ciss/Coss are tracked correctly.
- Mechanism (root-caused): ST/MCC draw the grid in trace-dark ink at 5-8%
  coverage, under the historical `dark.mean() > 0.10` trigger for grid/trace
  separation, so whole gridline sets survived as per-column stroke centers
  and stole band slots. The onsemi variant is an eroded frame stroke
  surviving the opening. The goford variant is the bottom FRAME stealing the
  Crss slot on a light-grid chart (separate sub-case, see below).
- SLICE STATUS (2026-07-28, uncommitted): separation now also triggers on
  the structural signature — >=3 distinct interior full-span dark rules in
  BOTH orientations (`_dark_grid_rule_evidence`; one orientation alone is a
  flat trace riding a decade line, e.g. XRS200N12T, and must not trigger) —
  plus a frame-residual rail pass inside the branch. Packet A/B deltas are
  exactly the four human-rework parts: MCP100N10Y-TP recovers correct
  three-trace identity (overlay-verified; spans partial -> suspect, class
  D); STP310N10F7, FDPF2D3N10C, FDP2D3N10C now refuse loudly (thin curves
  do not survive the opening) instead of serving shifted identities.
  Everything else in the packet is byte-identical. Tests:
  `tests/test_capacitance_black_grid_masquerade.py` (11).
  - OPEN sub-case — goford frame-band ownership: GT035N12T/GT023N10T (and
    GT045N10TH's crss-on-coss): light-grid charts where the bottom frame
    residual occupies the third band slot with exactly 3 centers. Edge
    blanking is not safe naively (a real Crss tail rides the bottom axis;
    a flat Ciss rides the top line). Still fail-closed suspect + flagged.
  - OPEN sub-case — ST/onsemi thin-curve recovery after separation
    (STP310/315, FDPF/FDP): the opening erases 1px curves with the 1px
    grid; needs a stroke-preserving separation for those styles.

## C. Axis calibration untrusted → overlay has no tick labels (~10 parts) — axis + overlay

- NCE fig07 family (NCEP023N10, NCEP026N10, NCEP030N12, NCEP01T18,
  NCEP039N10M…), goford GT120N10/GT060N10T/GT045N10TH, vishay SUP70030E(-GE3)
  / SUP70042E-GE3, ST fig08.
- Human comment is uniformly "missing/no axis labels": the verify overlay only
  draws consumed tick/unit annotations when calibration is trusted, so an
  untrusted axis renders a bare crop (checklist requires the overlay to show
  consumed ticks — an untrusted axis currently shows nothing at all).
- On several (e.g. NCEP023N10, NCEP035N10M — human-GREEN despite gap status)
  the trace pixels are visibly correct; the loss is purely axis trust.
  Recovering trust for the NCE/goford log-grid style is the highest-yield
  single slice in this packet: it upgrades many already-good extractions.
- SLICE STATUS (2026-07-28, committed `f3d877c`): three OCR-path evidence recoveries
  landed in `capacitance_axis.py`/`region_ocr.py` — mangled `10^N` ladder
  repair (>=4 uniform rows, >=2 independently agreeing exponent anchors,
  contradictions refuse), unique single X-tick outlier drop (>=5 remaining
  ticks fit <=0.5 V), rotated-title unit recovery (literal pF/nF only;
  `(oF)` stays absent). Packet A/B: exactly one delta — NCEP023N10 to
  trusted `ok` with tick-labeled overlay; zero collateral. Tests:
  `tests/test_capacitance_ocr_axis_ladder.py` (12, incl. GT120 lying-anchor
  refusal calibration).
  - Still refusing honestly: NCEP035N10M (no exposed exponent digits — OCR
    escalation at 600/800 dpi and isolated-superscript psm10 both yield
    noise; would need non-OCR offset evidence, e.g. spec-table anchor-implied
    decade offset), GT120N10 (exposed digits contradict), NCEP01T18 (y
    recovered, duplicate OCR x tick `60,60` with only 4 ticks).
  - BLOCKED on the in-flight linear-grid-seating slice (other agent,
    uncommitted edits to `_seat_linear_y_ticks_on_grid` /
    `_vector_horizontal_gridline_candidates`): NCEP026N10, GT035N12T,
    GT060N10T (tick does not own exactly one gridline within 3 px),
    GT023N10T / GT045N10TH (fit misses grid center by 1.8–2.2 px, max 1).
    With unit + outlier recoveries these now reach seating; the seating
    tolerances for OCR-sourced label centers are that slice's call.

## D. Early trace termination (5 parts) — digitizer

- `nce/NCEP026N10` (Ciss right + Crss left), `nce/NCEP01T18` (Crss left),
  `nce/NCEP035N12` (Crss+Coss left), `infineon/IPP030N10N3GXKSA1` (Crss left),
  `huayi/HYG025N10NS1P` (Coss right), `ao/AOT66916L` (Coss left).
- Existing peer-relative span checks already mark these suspect; work is
  recovering the missing span, not detection.

## E. Found chart, banding refuses entirely (4 parts) — digitizer

- `st/STP315N10F7` ("good chart but nothing digitized"),
  `vishay/SUP70030E`, `SUP70030E-GE3`, `SUP70042E-GE3` — "could not establish
  three stable trace bands: 0, 0, 0" (71,71,71 for STP315). Vishay colored
  charts + ST black-on-black grid styles.

## F. Finder gap remains (4 parts) — finder

- siliup SP010N02GHTQ / SP010N02BGHTQ / SP010N04AGHTQ / SP010N03AGHTQ:
  no capacitance chart candidate. Untriaged (layout not yet inspected).

## Packet-level wins vs the 2026-07-28 original

finder-gaps 26 → 4; extraction attempts 12 → 37; the 8 spec-table
"extraction errors" are gone (all bound to real charts now); 10 newly
recovered charts are human-GREEN at the trace level.
