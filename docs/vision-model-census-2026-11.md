# Census for issue #19: chart media, finder coverage and refusals by stage

Date: 2026-11-24. dsdig commit: `e2d8ddc` (`main`). Issue: #19, task 1.
This is a read-only measurement. No model was trained and no digitizer was changed.
Companion: `docs/vision-models-sota-2026-11.md` (model and licence survey for tasks 2–4).

## Correction, 2026-11-24 (after the cause split)

`docs/vision-model-cause-split-2026-11.md` looked at 50 vector and 50 raster axis-calibration stops one by one (agent readings, not human-verified). It changes three statements below:

1. **Task 4: the stop rule fires. Do not train a recogniser now.** Misread digits are 2 of 50 raster stops (5 counting dropped or unread labels). For 26 of 50 no OCR ran at all: 16 are body-diode and RDS(on) charts, which have no OCR path, and 10 are transfer charts refused on their axis-title text before the raster path is reached. The fix is to wire the existing OCR path into those classes.
2. **Task 2: a correct box alone would fix few axis stops** (10 of 100 yes, 6 unsure). The case for the detector rests on the finder audit in section 3 (missed and mis-boxed charts), not on the axis-calibration totals.
3. **"Axis calibration is the largest stop" overstates it.** The stage rule is first-match by priority: 234 of the 465 axis rows also match a later-stage rule (165 of the 241 on vector panels; recomputed from `charts.jsonl`). In the sample, 18 of 50 vector and 3 of 50 raster rows were not axis failures. In particular "transfer panel lacks owned VGS and ID axis evidence" (75 rows) is a check of the axis-title text (`transfer_characteristics.py`), not a calibration failure.

The tables below are unchanged; read sections 4.2, 4.3 and 6 with these three points.

## Independent review, 2026-11-24

A Codex (GPT-6 Astra, xhigh) adversarial review recomputed the headline numbers from the raw rows and they held. Its verdict: on track with corrections. What it changes here:

- **"Layout detector: go, build first" is not established.** The detector merits one bounded experiment, measured against a cheap baseline (neighbour-aware tick-band expansion and routing fixes in the heuristic finder) on correctly served charts and wrong serves. A 4 pt expansion of the finder's boxes alone raised clean boxes from 182 to 225 of 355, while neighbour contamination rose from 7 to 10, so blind padding is unsafe.
- **The cause split's "at most 10 of 100" is 10 yes plus 6 unsure**, and it predicts calibration, not a correctly served value.
- **HXY sensitivity:** without HXY (21 % of PDFs, shared templates) the weighted raster share is 16.5 %, not 21.2 %.
- **Order of work it recommends:** existing-OCR wiring and routing repairs first, the cheap finder baseline second, the detector experiment third; OCR-model and segmentation training stay deferred.

## Verdict

| Issue task | Verdict | Main evidence |
|---|---|---|
| 2. Layout detector | **Go**, build first | On 194 audited pages the finder gave a clean box to 142 of 372 charts, a defective box to 64 and no box to 166. Of the kinds dsdig digitizes, 52 were missed and 55 had a defective box. |
| 4. Tick-label OCR | **Superseded: no recogniser training now** (see the correction above). Originally: go after task 2, with a stop rule | 24 % of found panels have no native text for their tick labels. On raster panels, axis calibration is the largest stop: 193 of 293 non-pass charts. Whether those stops are misread digits or mislocated tick bands is not measured yet. |
| 3. Curve segmentation | **No-go for now**; revisit when raster charts reach the tracer | Raster is not negligible (21 % of found panels, 33 % of PDFs). But only 37 of 979 non-pass charts stopped at tracing on a raster panel; raster charts mostly stop earlier. The raster charts seen are clean bitmaps, not scans. The local LineFormer run misses the 1 px target and over-detects. |

Issue #18 (no raster entry point) stays a prerequisite for tasks 3 and 4.
The issue's rule "if raster is negligible, only item 2 is worth building" does not apply: raster is not negligible.
Item 3 is deferred for a different reason, given in section 6.

## 1. What was measured, and on what

Corpus: `pwr-mosfet-lib/datasheets`, 22,391 canonical PDFs in 41 vendor directories.
Generated copies (`*.pdf.<x>.pdf`) are excluded. This is a MOSFET library.
MLCC, powder-core and paper charts are not in it and were not sampled (section 7).

| Run | Sample | Tool | Output (gitignored) |
|---|---|---|---|
| Chart medium | 2,237 PDFs: up to 100 per vendor, seed 19 | `tools/census/chart_medium_census.py` (production finder at 180 dpi) | `out/census-issue19/medium/` |
| Refusals by stage | 301 PDFs: 10 per vendor from the sample above, seed 19 | `tools/census/refusal_census.py` (runs `tools/review_ab/run_part.py`) | `out/census-issue19/refusal/` |
| Finder audit | 30 PDFs drawn with probability proportional to the PDFs each sampled row stands for, seed 19; 194 pages | `tools/census/finder_audit_sheets.py`, labelled by eye | `out/census-issue19/finder-audit/` |

Corpus-level shares weight each vendor by population / sample size.
Each run directory holds `provenance.json` (commit, seed, sample-list hash, host, tool versions, classification rules) and `report.md`.

**Host.** The Mac was loaded by other sessions, so the two runs ran on vup (x86, Ubuntu 26.04) in a user-local environment: Python 3.11, PyMuPDF 2.1, tesseract 5.5.4, poppler 26.11.
The Mac has PyMuPDF 1.28, tesseract 5.5.3 and poppler 26.07.
The finder's OCR path is host-sensitive.
Check: the first 29 PDFs (all agmsemi, the first vendor in sample order and a mostly raster one) gave the same 108 panels with the same class and medium on both hosts.
That is a spot check on one vendor, not a proof for the whole sample.

**Why a fresh run.** No stored output can give a rate.
Every existing set of per-chart outcomes is a selected population: human-verified charts, fix packets, top-N part lists, or a 50-PDF novelty-maximised sample.
All are 80 to 300 commits behind `main`.
The closest, `out/astra-review-50` (2026-09-30, commit `3aa5907`), gives 53 passes in 180 chart rows (29 %) through the same report; the fresh run gives 30 %.

## 2. Raster or vector

Rule per found panel, on its `crop_box_pt`:
- **vector**: stroked path length of at least 4 crop diagonals, or (no strokes, no image) at least 40 filled-path items. The second case is charts drawn as filled outlines.
- **raster**: embedded images of at least 90 pt cover half the crop or more, and there are no such paths.
- **neither**: none of the above.

Eye check of the rule: 40 sampled panels, 10 per first-pass category.
- 10 of 10 "vector" and 10 of 10 "raster" were right.
- 10 of 10 "both" were vector charts over a watermark image, so "both" counts as vector.
- The first rule had no filled-path clause and put 7.8 % of panels in "neither". Of 10 sampled, 5 were real vector charts drawn as filled outlines and 5 were finder false positives (tables, waveform sketches). The filled-path clause was added for that reason; it was not re-checked on a fresh sample.

### 2.1 Result

Finder status: 2,235 ok, 2 errors (`infineon/BSZ100N06LS3 G.pdf`: no text from either text path; `st/STB85NF55.pdf`: `pdftoppm` failed). 3.4 % of PDFs gave no panel.

| | Share of found panels (weighted) |
|---|---:|
| Vector, stroked paths | 71.0 % |
| Vector, filled outlines | 5.5 % |
| Vector over a background image | 0.1 % |
| **Raster** | **21.2 %** |
| Neither | 2.3 % |
| Tick labels not available as native text | 24.2 % |
| PDFs with at least one raster panel | 33.2 % |

These shares are of the panels the finder returns.
The finder returns fewer of the charts on raster pages (section 3), so the true raster share is higher than 21 %. By how much is not measured.

### 2.2 By chart class

| Finder class | Panels in sample | Vector | Raster | Neither | Labels not text |
|---|---:|---:|---:|---:|---:|
| capacitances | 2,043 | 75.4 % | 24.2 % | 0.4 % | 26.3 % |
| gate_charge | 2,433 | 65.8 % | 24.5 % | 9.7 % | 37.1 % |
| transfer | 1,697 | 71.1 % | 28.1 % | 0.8 % | 27.4 % |
| rds_on | 2,840 | 78.5 % | 21.3 % | 0.3 % | 20.9 % |
| body_diode | 1,568 | 62.7 % | 35.7 % | 1.7 % | 37.4 % |
| breakdown_voltage | 363 | 84.5 % | 15.5 % | 0.0 % | 19.0 % |
| threshold_voltage | 336 | 61.5 % | 36.6 % | 1.9 % | 39.2 % |
| safe_operating_area | 903 | 85.1 % | 14.0 % | 1.0 % | 18.2 % |
| chart (unclassified) | 248 | 95.2 % | 3.8 % | 1.0 % | 2.8 % |

The 9.7 % "neither" for gate charge is consistent with the finder's gate-charge false positives (definition waveforms and tables, section 3).

### 2.3 By vendor

The split is by vendor, not by class. Vendors with 130 or more PDFs:

| Vendor | Canonical PDFs | Panels found | Vector | Raster | Labels not text | PDFs with a raster panel |
|---|---:|---:|---:|---:|---:|---:|
| infineon | 6,540 | 1,018 | 94.4 % | 3.0 % | 6.0 % | 5.1 % |
| hxy | 4,709 | 388 | 43.6 % | 54.1 % | 57.2 % | 65.0 % |
| littelfuse | 1,506 | 327 | 100.0 % | 0.0 % | 0.3 % | 0.0 % |
| onsemi | 1,317 | 632 | 96.5 % | 2.5 % | 2.1 % | 4.0 % |
| toshiba | 1,011 | 693 | 5.8 % | 94.2 % | 86.7 % | 89.0 % |
| st | 1,003 | 772 | 98.2 % | 0.1 % | 25.0 % | 1.0 % |
| siliup | 843 | 440 | 0.0 % | 100.0 % | 100.0 % | 93.0 % |
| nce | 744 | 576 | 0.0 % | 100.0 % | 100.0 % | 100.0 % |
| xnrusemi | 644 | 516 | 78.5 % | 20.5 % | 22.3 % | 41.0 % |
| goford | 621 | 418 | 55.0 % | 45.0 % | 44.7 % | 96.0 % |
| vishay | 576 | 787 | 82.2 % | 5.2 % | 7.5 % | 10.0 % |
| huayi | 545 | 356 | 57.3 % | 40.7 % | 42.7 % | 41.0 % |
| ao | 418 | 588 | 99.3 % | 0.0 % | 1.0 % | 0.0 % |
| nxp | 386 | 713 | 93.5 % | 0.0 % | 0.6 % | 0.0 % |
| mcc | 375 | 299 | 100.0 % | 0.0 % | 1.3 % | 0.0 % |
| ti | 212 | 749 | 96.9 % | 0.3 % | 1.2 % | 2.0 % |
| ts | 202 | 590 | 97.1 % | 2.9 % | 2.9 % | 13.0 % |
| good_ark | 193 | 350 | 12.3 % | 82.0 % | 87.7 % | 85.0 % |
| agmsemi | 176 | 423 | 39.2 % | 57.9 % | 62.2 % | 68.0 % |
| crmicro | 133 | 593 | 71.5 % | 27.7 % | 34.2 % | 40.0 % |

Each vendor share rests on 100 PDFs. The full table, including the 21 small vendors, is in `out/census-issue19/medium/report.md`.

Notes:
- ST has 25 % of panels with no native text but almost no raster: those are vector charts whose labels are outlined glyphs.
- HXY is 21 % of the corpus and, per an earlier measurement, over 90 % of its sheets share chart templates. Its weight in the corpus shares is by PDF count, not by distinct chart.
- The 10 raster panels looked at (Toshiba, NCE, HXY) are clean black-and-white gridded bitmaps placed in a digital PDF. None is a scan. Only 8.0 % of chart or image pages have no native text layer.

## 3. Finder coverage, by eye

194 pages of 30 PDFs were rendered with every finder `crop_box_pt` drawn on.
Two reviewers (agents, 97 pages each) opened every page and counted charts and boxes; a second pass named the printed kind of every missed or defective chart.
I checked three pages against the labels (`IPB80N06S4-07` p5, `NCEP063N10AGU` p3, `RQ3E180AJTB1-HXY` p3) and they agreed.
This is an agent count with a three-page check, not a human-verified set.

Definitions: a chart is a data plot with numeric axes. "Box defective" means the box cuts into the plot area or through the tick labels of an axis, or holds more than one chart.

| Group | Pages | Charts | Clean box | Box defective | No box | Boxes on a non-chart |
|---|---:|---:|---:|---:|---:|---:|
| **All** | 194 | 372 | 142 (38 %) | 64 (17 %) | 166 (45 %) | 8 |
| Vector pages | 80 | 293 | 123 (42 %) | 51 | 119 (41 %) | 4 |
| Raster pages | 13 | 64 | 16 (25 %) | 12 | 36 (56 %) | 0 |
| Mixed pages | 3 | 15 | 3 | 1 | 11 | 0 |

Kinds of the missed and defective charts:

| Printed kind | No box | Box defective |
|---|---:|---:|
| gate charge | 0 | 19 |
| body diode | 12 | 5 |
| RDS(on) vs Tj | 7 | 6 |
| capacitance | 2 | 10 |
| threshold voltage | 10 | 2 |
| RDS(on) vs I_D | 8 | 4 |
| transfer | 6 | 4 |
| RDS(on) vs VGS | 7 | 2 |
| breakdown voltage | 0 | 3 |
| **kinds dsdig digitizes** | **52** | **55** |
| output, derating, SOA, thermal impedance, switching, other | 114 | 9 |

The pages carried 165 boxes of the finder classes that dsdig digitizes.
So of roughly 217 charts of those kinds, about a quarter had no box and about a quarter a defective one.
This is approximate: the kinds of the correctly boxed charts were not recorded, and the finder's own class label is sometimes wrong.

What the defects are:
- **Tick labels outside the box.** The most common defect: the box ends at the plot frame, so an edge runs through the x or y tick labels.
- **One box over two charts**, and a gate-charge box shifted down by half a chart.
- **Wrong class.** A box labelled "transfer" sat on the output chart on 3 pages; one labelled "capacitances" sat on an RDS(on)-vs-Tj chart.
- **All 8 false boxes** frame tables or waveform sketches, and all 8 are labelled `gate_charge`.

Limits of this audit:
- 30 PDFs, 11 of them Infineon (it is 29 % of the corpus). Per-vendor rows hold 5 to 23 pages; do not read them as rates.
- A missing or tight box is not always a lost value. Gate charge and RDS(on) vs Tj locate their own panels, and digitizers read tick labels from the page text, not only from the crop. The refusal run below measures the outcome side.
- The finder ran at 180 dpi (the CLI default).

## 4. Refusals and failures by stage

301 PDFs, every class `run_part.py` runs, 1,401 chart rows, all 301 runs completed.
Not run: RDS(on) vs VGS, Vth, MLCC, core loss, paper plots. Reverse leakage ran and produced no rows on these MOSFET sheets.

### 4.1 Outcome by class

"Pass" is `ok` or `verified`. "Served, unverified" is a value the class itself marks for review. "Refused" includes exceptions with a refusal reason.

| Class | Charts | Pass | Served, unverified | Refused | Pass, weighted | Refused, weighted |
|---|---:|---:|---:|---:|---:|---:|
| gate charge | 328 | 250 | 29 | 49 | 57 % | 30 % |
| capacitance | 255 | 89 | 132 | 34 | 45 % | 15 % |
| transfer | 219 | 0 | 60 | 159 | 0 % | 72 % |
| RDS(on) vs I_D | 206 | 4 | 0 | 202 | 1 % | 99 % |
| body diode | 177 | 45 | 2 | 130 | 28 % | 71 % |
| RDS(on) vs Tj | 153 | 28 | 0 | 125 | 13 % | 87 % |
| breakdown | 43 | 6 | 4 | 33 | 41 % | 43 % |
| reverse recovery | 20 | 0 | 0 | 20 | 0 % | 100 % |
| **All** | 1,401 | 422 | 227 | 752 | 30 % | 54 % |

Rows that overstate failure:
- **Transfer** never returns `ok`; every served chart is `overlay-review-required` by design.
- **RDS(on)**: each `rds_on` panel is tried by both the I_D and the Tj digitizer, so a panel of one kind is a refusal for the other. 59 errors are `rds_on_panel_not_routed_to_any_rdson_digitizer` (57 as the stop reason, 2 more with a second wording), which includes RDS(on)-vs-VGS panels this runner does not send to their digitizer.
- **Reverse recovery** supports the AO layout only; all 20 are "caption found but no gridline panel".

### 4.2 Stage that stopped each non-pass chart

Each non-pass chart gets the stage of the first matching rule over its own reason fields (`STAGE_RULES` in `tools/census/refusal_report.py`). 8 charts matched no rule and are listed as such.
**The stage is where the pipeline stopped, not where the cause is.** A crop that takes in a neighbour or loses its tick labels usually stops at axis calibration.

| Class | Non-pass | Review by design | Finder / crop / routing | Axis calibration | Label / curve identity | Curve tracing | Gates | Unclassified |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| body diode | 132 | | 11 | 100 | 21 | | | |
| breakdown | 37 | | 7 | 18 | | 7 | 2 | 3 |
| capacitance | 166 | | 2 | 103 | | 59 | 2 | |
| gate charge | 78 | | 17 | 56 | | 2 | 3 | |
| RDS(on) vs I_D | 202 | | 59 | 43 | 25 | 72 | | 3 |
| RDS(on) vs Tj | 125 | | 53 | 29 | 34 | 7 | 2 | |
| reverse recovery | 20 | | 20 | | | | | |
| transfer | 219 | 60 | 1 | 116 | 25 | 15 | | 2 |
| **All** | 979 | 60 | 170 | 465 | 105 | 162 | 9 | 8 |

### 4.3 Stage by chart medium

| Outcome / stage | Charts | Vector | Raster | Neither | Unknown |
|---|---:|---:|---:|---:|---:|
| Pass | 422 | 339 | 77 | 6 | |
| Review by design | 60 | 59 | 1 | | |
| Finder / crop / routing | 170 | 54 | 52 | 6 | 58 |
| Axis calibration | 465 | 241 | 193 | 26 | 5 |
| Label / curve identity | 105 | 101 | 3 | 1 | |
| Curve tracing | 162 | 123 | 37 | 2 | |
| Gates / validation | 9 | 7 | 2 | | |
| Unclassified | 8 | 3 | 5 | | |

"Unknown" rows are errors that name no panel.
Pass share: 37 % on vector panels (339 of 927), 21 % on raster panels (77 of 370).

Readings:
- **Axis calibration is the largest stop on both media.** Top reasons: "transfer panel lacks owned VGS and ID axis evidence" (75), "X axis: no trustworthy numeric tick run" (93 across body diode and RDS(on) vs I_D), `axis_calibration_untrusted` (45), `axis_assumed_0_10` (32).
- **More than half of the axis stops (241) are on vector panels**, where the tick labels are native text. OCR cannot be the cause there. Label ownership and panel extent can.
- **On raster panels**, 193 of 293 non-pass charts stop at axis calibration, 52 at finder/crop, 37 at tracing.
- **Raster tracing already works where it is reached**: 64 of the 77 raster passes are gate charge. dsdig also uses its raster tracer on rendered vector crops: capacitance reported `extraction_method` raster on 114 results and vector on 107; gate charge `trace_source` raster on 224 and vector on 88.
- **Curve tracing stops on vector panels (123)** are mostly "no full-span vector curve" for RDS(on) vs I_D (72) and "could not establish three stable trace bands" for capacitance (25).

The full reason lists are in `out/census-issue19/refusal/report.md`; per-chart rows in `charts.jsonl`.

## 5. The local LineFormer run: verdict

`out/lineformer-eval/` (2026-09-29): LineFormer base weights and a battery fine-tune, CPU inference on 188 case images of human-verified and re-traced charts, scored against pixel-aligned dsdig ground truth.
The scorer was calibrated on synthetic controls first (`calibration_report.txt`).

| Human-verified set | Charts | Extra instances | Charts over-detected | Median of p95 error | Median vertical offset | Swap events |
|---|---:|---:|---:|---:|---:|---:|
| Capacitance, base | 149 | 174 | 68 | 2.0 px | −1.4 px | 23 |
| Capacitance, battery | 149 | 353 | 93 | 2.0 px | −1.5 px | 26 |
| Transfer, base | 19 | 18 | 8 | 3.4 px | −3.1 px | 13 |
| Transfer, battery | 19 | 17 | 8 | 3.2 px | −2.5 px | 12 |

- Of 9 transfer charts with a true crossing, the base model kept purity at or above 0.9 through every crossing on 2, the fine-tune on 3.
- Mean x-coverage of a curve by its best instance is 0.69 to 0.73.

**Verdict: not usable as a tracer, and not worth more work as a proposal source.**
- It misses the 1 px target by 2 to 3 times and carries a systematic vertical offset.
- It over-detects on about half the capacitance charts.
- It loses identity at the crossings it was meant to solve.
- It has no licence, so it cannot become a dependency (survey doc).
- The battery fine-tune made over-detection worse, so a quick fine-tune is not the fix.

## 6. Go / no-go

### Task 2, layout detector: go

- The finder misses or mis-boxes more charts than it boxes cleanly on the audited pages, and about half of the charts of digitized kinds.
- 170 charts stop at finder/crop/routing, and part of the 465 axis-calibration stops will be crop and label-ownership effects.
- It helps both media, which matters because 241 axis stops are on vector panels.

Before building:
1. **Define the label set against what the digitizers need.** Today's crop ends at the plot frame. A detector trained on those boxes would learn the same defect. Plot box, tick-label groups per axis, axis titles, caption, legend and in-plot labels need their own classes.
2. **Turn the audit into the evaluation set.** Have Fab review a subset of the 194 pages so the baseline is human-verified, and record the kind of every chart, not only the failures.
3. **Measure the cause split first.** Take about 50 axis-calibration stops on vector panels and check by eye whether the crop or the label ownership was wrong. If most are neither, the detector helps less than the totals suggest.

### Task 4, tick-label OCR: go after task 2, with a stop rule

- 24 % of found panels have no native tick-label text; on five vendors it is 85 to 100 %.
- 193 raster charts stop at axis calibration.
- Not known: how many of those fail because digits are misread, and how many because the tick band was not found. Only the first is an OCR problem.

Order of work:
1. Sample about 50 of the 193 and split them by eye into "digits misread", "tick band not found", "other".
2. If "digits misread" is a small minority, stop: the detector's tick-label boxes plus the existing tesseract path are the fix.
3. Otherwise train the recogniser on rendered tick labels. The raster charts are clean bitmaps, so scan simulation can be light.

### Task 3, curve segmentation: no-go for now

- Raster is 21 % of found panels, so the gate "raster is negligible" does not hold.
- But 37 of 979 non-pass charts stop at tracing on a raster panel. That 37 is a first-match bucket, not an upper bound: 55 raster non-pass charts match a tracing rule somewhere in their reasons, and neither count shows what a segmenter would recover.
- The existing raster tracer passes where the earlier stages pass.
- The one model tried locally fails on accuracy, over-detection and crossings (section 5).

Revisit when tasks 2 and 4 (or plain finder and axis fixes) bring raster charts to the tracer in numbers.
The trigger: re-run section 4 and see raster tracing stops above roughly 10 % of non-pass charts, or crossings on raster transfer and capacitance charts becoming the top raster reason.
The vector tracing stops (123) are a separate problem; path identity, not a pixel model, is the tool there.

## 7. What this census does not cover

- **Other chart families.** MLCC, powder-core and paper charts are not in the sampled library. Known from earlier work only: the Taiyo Yuden MLCC charts are vector, the KDM core-loss sheet is a scan.
- **Classes not run**: RDS(on) vs VGS and Vth in the refusal run.
- **Demand weighting.** Shares are per PDF in the library, not per part actually used in a design.
- **Uncertainty.** Vendor shares rest on 100 PDFs each, refusal rows on 10 PDFs per vendor, the audit on 30 PDFs. No confidence intervals were computed.
- **Unclaimed images.** 52.9 % of chart or image pages hold a figure-sized image that no panel claims. That includes package drawings and logos; the share that are charts was not measured separately from the audit.
- **Environment.** The runs were made on vup; one vendor was checked against the Mac.

## 8. Reproducing

```sh
PY=/Users/fab/dev/pv/ee/datasheet-chart-digitizer/.venv/bin/python
ROOT=/Users/fab/dev/pv/pwr-mosfet-lib/datasheets
export PYTHONPATH=$PWD/src:$PWD/tools/census

# 1. draw the sample, then run it (here or on another host with --sample-from)
$PY tools/census/chart_medium_census.py --root $ROOT --out out/census/sample --per-vendor 100 --seed 19 --list-only
$PY tools/census/chart_medium_census.py --root $ROOT --sample-from out/census/sample --out out/census/medium -j 4
$PY tools/census/census_report.py out/census/medium

# 2. all classes on a sub-sample, then the stage tables
$PY tools/census/refusal_census.py --root $ROOT --sample-from out/census/sample --out out/census/refusal --per-vendor 10 -j 4
$PY tools/census/refusal_report.py out/census/refusal --root $ROOT

# 3. pages to label by eye, then the tabulation of labels_*.json
$PY tools/census/finder_audit_sheets.py out/census/medium --root $ROOT --out out/census/finder-audit --pdfs 30
$PY tools/census/finder_audit_report.py out/census/finder-audit
```

The medium run took about 45 minutes with 12 workers on vup. Set `OMP_THREAD_LIMIT=1`, or tesseract oversubscribes the host.
