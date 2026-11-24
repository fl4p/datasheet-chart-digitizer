# Cause split of axis-calibration stops (issue #19)

Date: 2026-11-24. dsdig source: `e2d8ddc` (no `src/` change since). Issue: #19, follow-up to task 1.
Input: the refusal census in `docs/vision-model-census-2026-11.md`, section 4.3 and section 6.

All causes below are agent readings of rendered pages, native PDF words and single-PDF reruns.
None of it is human-verified. Nothing here sets or implies `human_verified`.

## Result in short

- **Vector panels (50 sampled of 241).** 15 stops are a crop or label-ownership fault. 4 of those 15 are phantoms (a box on the wrong chart kind or on no chart). Without phantoms it is 11 of 46.
  10 are grid or frame seating. 24 are "other", and 18 of those are not axis-calibration failures at all.
  Most vector stops are neither crop nor ownership. By the census's own test, the layout detector helps axis stops less than the totals suggest.
- **Raster panels (50 sampled of 193).** 26 stop because the labels are pixels and the class has no path that reads them: 16 in classes with no OCR path at all, 10 raster transfer charts that never reach their OCR path.
  15 are crop faults (5 phantoms). 5 are OCR faults; only 2 of those are digits read wrongly.
- **Task 4 stop rule.** "Digits misread" is 2 of 50 raster stops (5 of 50 if dropped and unread labels count). That is a small minority, so the census stop rule applies: do not train a recogniser.
  The census assumed the existing tesseract path would then be the fix. For 26 of the 50 it is not, because there is no OCR path for those classes or it is gated off.

## 1. Method

**Population.** `out/census-issue19/refusal/charts.jsonl` in the main checkout (sha256 `26a993101e18985f075c3ed16226a739032dd56756d22750208971477bd196c9`), rows with stage `axis calibration`: 241 vector, 193 raster (26 "none" and 5 "unknown" medium rows were not sampled).

**Draw.** `tools/census/cause_split.py sample`, seed 19.
- Rows are sorted by `pdf|class|p<page>|d<diagram>` and shuffled with `Random("19:<medium>")`.
- They are accepted in that order while every cap holds: 2 per class × vendor, 5 per vendor, 12 per class, 2 per PDF.
- The first 50 accepted per medium form the sample. Nothing was hand-picked or replaced.
- The caps keep any one class or vendor from dominating. The vector sample covers 19 vendors and the raster sample 14.

| Medium | Population | Drawn | List sha256 (of the newline-joined keys) |
|---|---:|---:|---|
| vector | 241 | 50 | `571e3705d50f6c1e2c3dd4b5f4c4b52707faa07de32574823fd037f44edb378a` |
| raster | 193 | 50 | `fb78dfc52c23f897fcad2c12d22ce6c85000ae49725ce6101758077ef7fdd2ee` |

Both lists are stored in `out/cause-split/sample.json` (gitignored). They are also printed with their labels in the appendix.

Classes drawn:
- Vector: transfer 12, capacitance 12, RDS(on) vs I_D 8, RDS(on) vs Tj 7, body diode 6, gate charge 3, breakdown 2.
- Raster: body diode 12, transfer 12, capacitance 9, gate charge 9, RDS(on) vs I_D 5, breakdown 2, RDS(on) vs Tj 1.

**Reading each chart.**
1. `cause_split.py render` draws the page around the recorded `crop_box_pt` with a margin of max(36 pt, 25 % of the crop's long edge). The crop is drawn in red, and the native PyMuPDF words of the region are listed with their position (in the crop, above, below, left or right).
2. Each render was looked at. The cause was assigned from the image plus the words, then checked against the census reason text and the code path that emits it.
3. Where image and words could not decide, the specific step was rerun on that one PDF on the Mac, at most 2 at a time. Reruns were 4 gate-charge `run_part.py` runs, 1 transfer `run_part.py` run, 3 transfer semantics/temperature probes, 1 `fit_numeric_axis` call and 6 tesseract passes over capacitance label bands.
4. Labels are in `out/cause-split/labels.tsv`. Tables come from `cause_split.py table` (`out/cause-split/tables.md`).

**Causes.** Every chart got exactly one primary cause:
- `crop_wrong`: the box is on the wrong chart, cuts the labels, or takes in a neighbour.
- `label_ownership`: the labels are native text but come out of the text layer wrong.
- `labels_not_text`: the labels are pixels or outlines and the class cannot read them.
- `ocr_misread`: an OCR path ran and lost or misread legible labels.
- `grid_or_frame`: labels and text are fine, but seating them on the grid or frame fails.
- `other`: anything else; the sub-cause names it.

A sub-cause names the mechanism.

**Phantoms** are boxes on a chart of another kind or on no chart. Their refusal is correct and no value is lost. They are counted, and the sufficiency totals are also given without them.

**Sufficiency columns.**
- `boxes_suffice`: would a correct plot box with its tick-label and title regions, given to today's code, let this chart calibrate?
- `ocr_suffices`: would correct OCR text for the tick labels let it calibrate?
- `crop_also_cut`: the crop visibly cuts tick labels or titles although the primary cause is something else.

**Host caveat.** The census ran on vup (tesseract 5.5.4). The reruns and OCR probes here ran on the Mac (tesseract 5.5.3). The census doc already notes that OCR is host-sensitive. Two gate-charge charts that were `low_confidence` in the census pass on the Mac (section 3.3).

## 2. Tables

### 2.1 Cause × medium

| cause | vector | raster | total |
|---|---:|---:|---:|
| crop_wrong | 10 | 15 | 25 |
| label_ownership | 5 | 0 | 5 |
| labels_not_text | 1 | 26 | 27 |
| ocr_misread | 0 | 5 | 5 |
| grid_or_frame | 10 | 0 | 10 |
| genuinely_unreadable | 0 | 0 | 0 |
| other | 24 | 4 | 28 |
| total | 50 | 50 | 100 |

Phantoms inside `crop_wrong`: 4 vector, 5 raster.

### 2.2 Cause × class (both media)

| cause | body_diode | breakdown | capacitance | gate_charge | rds_on_current | rds_on_temperature | transfer | total |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| crop_wrong | 2 | 2 | 4 | 8 | 0 | 4 | 5 | 25 |
| label_ownership | 1 | 0 | 1 | 0 | 0 | 1 | 2 | 5 |
| labels_not_text | 10 | 0 | 1 | 0 | 5 | 1 | 10 | 27 |
| ocr_misread | 0 | 0 | 5 | 0 | 0 | 0 | 0 | 5 |
| grid_or_frame | 2 | 1 | 0 | 0 | 1 | 1 | 5 | 10 |
| other | 3 | 1 | 10 | 4 | 7 | 1 | 2 | 28 |
| total | 18 | 4 | 21 | 12 | 13 | 8 | 24 | 100 |

### 2.3 Sub-causes

Vector (50):

| cause | sub-cause | n |
|---|---|---:|
| crop_wrong | wrong_class (phantom) | 3 |
| crop_wrong | not_a_chart (phantom) | 1 |
| crop_wrong | labels_cut | 3 |
| crop_wrong | neighbour_included | 2 |
| crop_wrong | wrong_chart | 1 |
| label_ownership | superscript_flattened (10^2 read as "102") | 2 |
| label_ownership | thousands_separator ("10,000") | 1 |
| label_ownership | merged_glyph_run | 1 |
| label_ownership | triplicated_text_layer | 1 |
| labels_not_text | outlined_glyphs | 1 |
| grid_or_frame | label_grid_seat (labels miss gridlines by more than 1.5 px) | 6 |
| grid_or_frame | dense_log_grid | 2 |
| grid_or_frame | label_offset_print | 1 |
| grid_or_frame | no_top_gridline | 1 |
| other | linear_axis_resolution (axis calibrated; a capacitance anchor falls below one axis pixel) | 8 |
| other | stage_priority_tracing (binding reason is tracing) | 4 |
| other | stage_priority_identity (binding reason is identity) | 2 |
| other | identity_gate_range (RDS(on) vs I_D x axis starts above 0) | 2 |
| other | masked_temperature_refusal (single-temperature transfer) | 2 |
| other | sparse_labels | 2 |
| other | log_axis_zero_label | 1 |
| other | off_scale_label | 1 |
| other | unsupported_unit (pC gate charge) | 1 |
| other | linear_log_ambiguous (labels span about 5 %) | 1 |

Raster (50):

| cause | sub-cause | n |
|---|---|---:|
| crop_wrong | not_a_chart (phantom) | 4 |
| crop_wrong | wrong_class (phantom) | 1 |
| crop_wrong | wrong_chart (the right chart is on the same page, unboxed) | 6 |
| crop_wrong | labels_cut | 3 |
| crop_wrong | two_charts | 1 |
| labels_not_text | no_ocr_path (body diode, RDS(on) vs I_D, RDS(on) vs Tj) | 16 |
| labels_not_text | title_semantics_gate (raster transfer) | 10 |
| ocr_misread | digit_misread | 2 |
| ocr_misread | labels_dropped | 2 |
| ocr_misread | nothing_read | 1 |
| other | passes_on_rerun (Mac host) | 2 |
| other | provenance_gate | 1 |
| other | printed_unit_nF | 1 |

### 2.4 Sufficiency

| column | medium | scope | n | yes | no | unsure |
|---|---|---|---:|---:|---:|---:|
| boxes_suffice | vector | all | 50 | 4 | 44 | 2 |
| boxes_suffice | vector | without phantoms | 46 | 4 | 40 | 2 |
| boxes_suffice | raster | all | 50 | 6 | 40 | 4 |
| boxes_suffice | raster | without phantoms | 45 | 6 | 35 | 4 |
| ocr_suffices | vector | all | 50 | 3 | 45 | 2 |
| ocr_suffices | vector | without phantoms | 46 | 3 | 41 | 2 |
| ocr_suffices | raster | all | 50 | 6 | 28 | 16 |
| ocr_suffices | raster | without phantoms | 45 | 6 | 23 | 16 |
| crop_also_cut | vector | all | 50 | 1 | 49 | 0 |
| crop_also_cut | raster | all | 50 | 16 | 34 | 0 |

Totals over both media:
- `boxes_suffice`: yes 10, unsure 6, of 100 (of 91 without phantoms).
- `ocr_suffices`: yes 9, unsure 18, of 100 (of 91 without phantoms).

The 16 raster `ocr_suffices: unsure` are the `no_ocr_path` charts. The census has no raster pass for those classes, so nothing shows that their tracers would succeed once the axes are read.

### 2.5 How much of the stage is axis calibration

The census assigns a chart's stage by rule priority over all its reasons, not by the binding reason. Any reason text containing "axis", "tick" or "calibration" wins over tracing, identity and gate reasons.

Over the whole population, axis-calibration rows whose reasons also match a later stage rule:

| medium | rows | any later stage | label / curve identity | curve tracing | gates / validation |
|---|---:|---:|---:|---:|---:|
| vector | 241 | 165 | 74 | 105 | 97 |
| raster | 193 | 43 | 23 | 31 | 22 |
| all | 465 | 234 | 97 | 162 | 145 |

A match is not proof that the later stage is the cause. By eye, 18 of the 50 vector sample rows are not axis-calibration failures:
- 8 linear_axis_resolution
- 4 stage_priority_tracing
- 2 stage_priority_identity
- 2 identity_gate_range
- 2 masked_temperature_refusal

The same holds for 3 of the 50 raster rows: 2 passes_on_rerun and 1 provenance_gate.

## 3. Findings

### 3.1 Vector: crop and ownership are a minority

- **Crop.** 6 real crop faults in 46 non-phantom rows: 3 labels cut, 2 neighbour included, 1 wrong chart. 4 of these 6 have `boxes_suffice: yes`; the other 2 are unsure.
- **Ownership.** 5 rows. All are text-layer defects: superscripts flattened into "102", comma thousands separators, glyph runs merged, and one datasheet whose every word appears three times at the same position.
  A better box does not fix any of them. Reading the label pixels would fix 3 of the 5 (`ocr_suffices: yes`).
- **Grid seating.** 10 rows. The labels are correct text and the crop is fine, but the seating of labels on gridlines fails: misses of about 2 px against a 1.5 px tolerance, dense log grids, an unlabelled top line.
  Neither better boxes nor OCR helps these.
- **Not axis failures.** 18 rows (section 2.5).

### 3.2 Raster: the labels are pixels and most classes cannot read pixels

- **No OCR path (16).** `diode_forward_voltage` reads `page.get_text("words")` only. RDS(on) vs I_D and RDS(on) vs Tj have no OCR path either. Every raster chart of these classes stops at axis calibration. The census has 0 raster passes for them.
- **Title gate (10).** `transfer_characteristics.process_chart` takes the raster OCR route (`process_raster_transfer`) only if `_validate_transfer_panel_semantics(chart)` passes first. That check needs "vgs" and "id" in the chart's native text. On a raster figure the titles are pixels, so 10 of the 12 raster transfer rows never reach OCR and report "lacks owned VGS and ID axis evidence". The other 2 are crop faults.
- **OCR misses and misreads (5, all capacitance).** All 5 have a correct crop and legible labels.
  - 2 have digits misread: "50" read as "30"; "6000" read as "8" and "000".
  - 2 have labels dropped near the lower-left corner.
  - 1 returned nothing from small labels crossed by gridlines.
- **Crop.** 6 boxes sit on the wrong chart while the right chart on the same page has no box, 3 cut labels, 1 covers two charts, and 5 are phantoms. Another 16 raster rows have a crop that cuts labels although something else stops them first (`crop_also_cut`).

### 3.3 Reruns that changed a reading

- **v07 and v11** (single-temperature transfer). The refusal says "lacks owned VGS and ID axis evidence". The probe shows the semantics would pass from the crop words. `_temperatures` fails first (found `[]` and `[25.0]`) inside the same `try`, and the semantic error is re-raised in its place. The refusal is correct (there is no 2..6 temperature family), but its text is wrong.
- **v14.** `fit_numeric_axis` on the five native labels 1.66 to 1.74 raises "linear/log calibration is ambiguous (0.23px vs 0.36px)". The labels and crop are fine; the span is too narrow to tell linear from log.
- **r33 and r49** (KIA gate charge, same template). These were `low_confidence` with `y_axis_grid_check_unverified` on vup. On the Mac, `run_part.py` serves them `ok` with the y grid verified (4 ticks within 1.51 px) and `axis_ocr_bounded`. This is the host sensitivity the census noted, now seen on the result.
- **r13.** After bounded OCR, the y grid check reports "no single linear registration places every labelled tick on an observed line". By eye the labels sit on their rules. The crop cuts the labels, so the OCR'd label positions are suspect. Classed `crop_wrong`, boxes unsure.
- **v20** (ST, outlined glyphs). Tesseract over the label band reads 800/600/400/200/0 and 10 to 40 correctly. Production still refuses with "need ... >=4 owned arithmetic capacitance labels". The crop is shifted left and down. Ownership against the shifted crop was not resolved, so OCR sufficiency stays unsure.

## 4. Examples

| id | Part, page, figure | What stops it |
|---|---|---|
| v04 | crmicro CRTD700P10L, p4, Fig 2 transfer | Clean vector chart. Every native word, including ticks 0 to 6 and both titles, is present three times at identical coordinates. "X axis (VGS): no monotone full-span tick-label row". `label_ownership`. |
| v08 | xnrusemi XR70P02F, p3, Figure 2 transfer | Log y labels 10^0, 10^1, 10^2 come out of the text layer as "100", "101", "102". `label_ownership`. |
| v01 | EPC EPC8010, p2, Figure 2 transfer | Crop and text are fine. The served calibration misses the 0.5 and 4 gridlines by about 2 px against a 1.5 px tolerance on a light-gray grid. `grid_or_frame`. |
| v02 | Panjit PSMP055N08NS1, p3, Fig.5 capacitance | The axis calibrated. The Crss/Ciss anchor falls below one pixel of a 0 to 6000 pF linear axis. Counted as axis calibration only because the reason contains "axis". `other`. |
| v07 | ST STH270N8F7-2, p6, Figure 8 transfer | Single curve, no temperature label. `_temperatures` fails, but the refusal shows the semantic error instead. `other: masked_temperature_refusal`. |
| v14 | ST STH270N8F7-2, p6, Figure 10 RDS(on) vs I_D | Labels 1.66 to 1.74 are native text. Linear and log fits are both within 0.4 px. `other: linear_log_ambiguous`. |
| r40 | ROHM RX3P07CBHC16, p5, Fig.5 vs Fig.7 | The breakdown box is on Fig.5 output characteristics; Fig.7 breakdown voltage is on the same page with no box. OCR ran on the wrong chart. `crop_wrong`, boxes suffice. |
| r42 | Toshiba TK095A65Z5, p6, Fig. 8.8 I_DR vs V_DS | Raster body-diode chart with legible labels. The class has no OCR path. The crop also cuts the y labels. `labels_not_text: no_ocr_path`. |
| r47 | Minos MPG90N08P, p3, Figure 2 transfer | Raster chart, two temperatures, legible labels. The titles are pixels, so the semantics check fails before the raster OCR path. r48 (Minos IRFB3607) carries the same figure. `labels_not_text: title_semantics_gate`. |
| r30 | Siliup SP40N02BGTD, p4, Figure 7 capacitance | Correct crop, bold blurred labels. Tesseract returns "8", "000", "1000", "15", "20", "7" for eight y and six x labels. `ocr_misread: digit_misread`. |

## 5. What this means for task 2 and task 4

**Task 2, layout detector.**
- The census asked for this check: "If most are neither [crop nor label ownership], the detector helps less than the totals suggest". On vector panels, 35 of 50 stops (35 of 46 without phantoms) are neither, so the detector helps axis stops less than the totals suggest.
- Where it does help:
  - Charts boxed on the wrong chart or on no chart: 8 real wrong-chart or two-chart boxes, plus 9 phantoms.
  - Raster crops that cut the label band: 3 primary plus 16 secondary.
- A correct box alone would let at most 10 of the 100 sampled stops calibrate, and 6 more are unsure.
- The census label-set point stands: the detector must output tick-label regions and titles, not just the plot frame. 16 of 50 raster crops already cut labels.

**Task 4, tick-label OCR.**
- The stop rule in census section 6: "If 'digits misread' is a small minority, stop".
- Digits misread: 2 of 50 raster stops. With dropped and unread labels counted: 5 of 50. The rule applies, so do not train a recogniser.
- The rule's second half, "the detector's tick-label boxes plus the existing tesseract path are the fix", does not hold for the largest raster group. 26 of 50 raster stops are classes that never run OCR:
  - 16 in body diode and RDS(on), which have no OCR path.
  - 10 raster transfer charts, gated off by a check on native title text.
- The fix for those is to wire the existing OCR into those classes and to let the transfer raster path read its titles by OCR. That is code work in dsdig, not a model.
- Only after that would those 26 charts show whether tesseract's accuracy is the limit.

## 6. Where this contradicts or refines the census doc

1. **Section 4.3, "lacks owned VGS and ID axis evidence" (75 rows) as an axis stop.** It is a check on axis-title text.
   - On vector panels it can hide a single-temperature refusal (v07, v11).
   - On raster panels it blocks the OCR path (10 of 12 raster transfer rows in the sample).
2. **Section 6, task 4, "the existing tesseract path is the fix".** For 26 of 50 raster stops no tesseract path runs (section 5).
3. **Section 4.3, "axis calibration is the largest stop".** The stage comes from rule priority.
   - 234 of the 465 rows (165 of 241 vector) also match a later stage rule.
   - By eye, 18 of 50 vector and 3 of 50 raster sampled rows are not axis-calibration failures.
4. **Section 4.3, "OCR cannot be the cause there" (vector).** This holds; no vector row is an OCR fault. But 1 of 50 vector charts (v20, ST) has outlined-glyph labels with no native text, so "vector" does not always mean "labels are text".
5. **Section 6, task 2, "part of the 465 axis-calibration stops will be crop and label-ownership effects".**
   - Measured: 11 of 46 non-phantom vector rows, and 10 of 45 non-phantom raster rows as the primary cause.
   - Another 16 raster rows have a crop that cuts labels as a secondary fault.
6. **Section 1, host sensitivity.** 2 of the 50 raster rows (r33, r49) change from `low_confidence` to `ok` between vup and the Mac on the same source. Per-chart outcomes on OCR paths are not portable between the two hosts.

## 7. Reproducing

```bash
cd dsdig-i19-cause-split
export PYTHONPATH=$PWD/src:$PWD/tools/census
PY=../datasheet-chart-digitizer/.venv/bin/python
CH=../dsdig-issue19/out/census-issue19/refusal/charts.jsonl
$PY tools/census/cause_split.py sample "$CH" --out out/cause-split
$PY tools/census/cause_split.py render out/cause-split/sample.json --root ../../pwr-mosfet-lib/datasheets
# label out/cause-split/labels.tsv by eye, then:
$PY tools/census/cause_split.py table out/cause-split/sample.json out/cause-split/labels.tsv --charts "$CH"
```

Reruns used `tools/review_ab/run_part.py` with `A50_CLASSES=gate_charge` or `transfer`. Outputs are in `out/cause-split/rerun/`.

## Appendix: the two samples with their labels

Agent readings, not human-verified. "boxes", "OCR" and "crop also cut" are the three columns of section 2.4.

| id | PDF | page | diagram | class | cause | sub-cause | boxes | OCR | crop also cut |
|---|---|---:|---:|---|---|---|---|---|---|
| v00 | infineon/IPB80N06S4-07.pdf | 5 | 8 | rds_on_temperature | crop_wrong | wrong_class | no | no | no |
| v01 | epc/EPC8010.pdf | 2 | 2 | transfer | grid_or_frame | label_grid_seat | no | no | no |
| v02 | panjit/PSMP055N08NS1_T0_00601.pdf | 3 | 5 | capacitance | other | linear_axis_resolution | no | no | no |
| v03 | infineon/IRF7424TRPBF.pdf | 3 | 3 | transfer | grid_or_frame | dense_log_grid | no | no | no |
| v04 | crmicro/CRTD700P10L.pdf | 4 | 2 | transfer | label_ownership | triplicated_text_layer | no | no | no |
| v05 | ao/AOTL66912.pdf | 3 | 6 | body_diode | other | log_axis_zero_label | no | no | no |
| v06 | mcc/MCG16N15-TP.pdf | 3 | 5 | capacitance | other | linear_axis_resolution | no | no | no |
| v07 | st/STH270N8F7-2.pdf | 6 | 8 | transfer | other | masked_temperature_refusal | no | no | no |
| v08 | xnrusemi/XR70P02F.pdf | 3 | 2 | transfer | label_ownership | superscript_flattened | no | yes | no |
| v09 | ao/AONA66916.pdf | 4 | 8 | capacitance | other | linear_axis_resolution | no | no | no |
| v10 | agmsemi/AGM15T13F.pdf | 3 | 5 | rds_on_temperature | crop_wrong | labels_cut | yes | no | no |
| v11 | goford/GT030N08T.pdf | 4 | 2 | transfer | other | masked_temperature_refusal | no | no | no |
| v12 | vishay/Si4848BDY.pdf | 3 | 901 | transfer | grid_or_frame | label_grid_seat | no | no | no |
| v13 | hxy/NVD5C478NT4G-HXY.pdf | 3 | 2 | transfer | grid_or_frame | label_offset_print | no | no | no |
| v14 | st/STH270N8F7-2.pdf | 6 | 10 | rds_on_current | other | linear_log_ambiguous | no | no | no |
| v15 | onsemi/NVMYS025N06CLTWG.pdf | 3 | 2 | transfer | crop_wrong | neighbour_included | yes | no | no |
| v16 | littelfuse/IXTX4N300P3HV.pdf | 4 | 10 | capacitance | label_ownership | thousands_separator | no | yes | no |
| v17 | epc_space/EPC7018GSH.pdf | 5 | 9 | capacitance | other | linear_axis_resolution | no | no | no |
| v18 | ao/AOB260L.pdf | 4 | 8 | capacitance | other | linear_axis_resolution | no | no | no |
| v19 | onsemi/NTMFS5C442NT3G.pdf | 4 | 10 | body_diode | other | sparse_labels | no | no | no |
| v20 | st/STN3NF06L.pdf | 7 | 8 | capacitance | labels_not_text | outlined_glyphs | no | unsure | yes |
| v21 | st/STF13N60DM2.pdf | 6 | 7 | capacitance | other | stage_priority_tracing | no | no | no |
| v22 | infineon/BSL308PEH6327XTSA1.pdf | 5 | 7 | transfer | crop_wrong | wrong_class | no | no | no |
| v23 | diotec/DI100N10PQ-AQ.pdf | 4 | 901 | transfer | crop_wrong | neighbour_included | yes | no | no |
| v24 | vishay/SIR872DP-T1-GE3.pdf | 3 | 903 | capacitance | other | linear_axis_resolution | no | no | no |
| v25 | xnrusemi/XR3400A.pdf | 4 | 7 | breakdown | other | off_scale_label | no | no | no |
| v26 | hxy/NVD5C478NT4G-HXY.pdf | 3 | 4 | body_diode | grid_or_frame | dense_log_grid | no | no | no |
| v27 | epc/EPC2371.pdf | 1 | 951 | gate_charge | crop_wrong | not_a_chart | no | no | no |
| v28 | vishay/SiR188DP.pdf | 3 | 902 | capacitance | other | linear_axis_resolution | no | no | no |
| v29 | st/STS10N3LH5.pdf | 6 | 7 | rds_on_current | other | stage_priority_tracing | no | no | no |
| v30 | wuxi/TTB115N08A.pdf | 3 | 6 | body_diode | label_ownership | superscript_flattened | no | unsure | no |
| v31 | epc/EPC2045.pdf | 3 | 901 | capacitance | other | linear_axis_resolution | no | no | no |
| v32 | ao/AOT482L.pdf | 3 | 3 | rds_on_current | grid_or_frame | no_top_gridline | no | no | no |
| v33 | onsemi/FDB0690N1507L.pdf | 4 | 2 | rds_on_temperature | crop_wrong | wrong_class | no | no | no |
| v34 | onsemi/FDWS86068-F085.pdf | 5 | 15 | capacitance | crop_wrong | labels_cut | unsure | no | no |
| v35 | epc/EPC2050.pdf | 2 | 2 | transfer | grid_or_frame | label_grid_seat | no | no | no |
| v36 | huayi/HYA032N06LS1C2.pdf | 4 | 6 | rds_on_current | other | stage_priority_identity | no | no | no |
| v37 | onsemi/FDS8880.pdf | 6 | 9 | rds_on_temperature | crop_wrong | labels_cut | unsure | no | no |
| v38 | infineon/IPB80N06S4-07.pdf | 7 | 14 | breakdown | grid_or_frame | label_grid_seat | no | no | no |
| v39 | epc_space/FBG10N30BC.pdf | 5 | 10 | rds_on_temperature | grid_or_frame | label_grid_seat | no | no | no |
| v40 | diodes/DMTH15H017LPSWQ-13.pdf | 4 | 7 | rds_on_temperature | other | stage_priority_identity | no | no | no |
| v41 | xnrusemi/XR60P02D.pdf | 4 | 8 | gate_charge | crop_wrong | wrong_chart | yes | no | no |
| v42 | diodes/DMTH8008LFGQ-13.pdf | 3 | 3 | rds_on_current | other | stage_priority_tracing | no | no | no |
| v43 | epc/EPC2306ENGRT.pdf | 3 | 8 | body_diode | grid_or_frame | label_grid_seat | no | no | no |
| v44 | diodes/DMTH15H017LPSWQ-13.pdf | 3 | 5 | rds_on_current | other | stage_priority_tracing | no | no | no |
| v45 | crmicro/CRSS038N08N.pdf | 4 | 3 | rds_on_current | other | identity_gate_range | no | no | no |
| v46 | mcc/SI3139KL3A-TP.pdf | 4 | 8 | gate_charge | other | unsupported_unit | no | no | no |
| v47 | crmicro/CRST045N10N.pdf | 4 | 3 | rds_on_current | other | identity_gate_range | no | no | no |
| v48 | infineon/IRFR8314TRPBF.pdf | 3 | 4 | rds_on_temperature | label_ownership | merged_glyph_run | no | yes | no |
| v49 | vishay/Si3440DV.pdf | 3 | 902 | body_diode | other | sparse_labels | no | no | no |
| r00 | goford/G60N04K.pdf | 4 | 6 | body_diode | labels_not_text | no_ocr_path | no | unsure | no |
| r01 | siliup/SP40N02BGTD.pdf | 3 | 905 | body_diode | labels_not_text | no_ocr_path | no | unsure | no |
| r02 | siliup/SP30P03TH.pdf | 4 | 901 | body_diode | labels_not_text | no_ocr_path | no | unsure | yes |
| r03 | nce/NCE60ND45AG.pdf | 4 | 2 | transfer | labels_not_text | title_semantics_gate | no | no | no |
| r04 | nce/NCEP1570GU.pdf | 3 | 3 | rds_on_current | labels_not_text | no_ocr_path | no | unsure | yes |
| r05 | rohm/RS6P100BHTB1.pdf | 5 | 902 | transfer | labels_not_text | title_semantics_gate | no | no | yes |
| r06 | toshiba/TK20E60W5.pdf | 5 | 86 | rds_on_current | labels_not_text | no_ocr_path | no | unsure | yes |
| r07 | siliup/SP010N02BGHTF.pdf | 3 | 901 | transfer | labels_not_text | title_semantics_gate | no | no | no |
| r08 | nce/nce0157D.pdf | 4 | 3 | rds_on_current | labels_not_text | no_ocr_path | no | unsure | yes |
| r09 | hxy/MSC040SMA120B4-HXY.pdf | 4 | 902 | body_diode | crop_wrong | wrong_class | no | no | no |
| r10 | agmsemi/AGM025N10H.pdf | 4 | 6 | transfer | crop_wrong | wrong_chart | yes | no | no |
| r11 | goford/G2304.pdf | 4 | 10 | capacitance | ocr_misread | labels_dropped | no | yes | no |
| r12 | agmsemi/AGM025N10H.pdf | 6 | 12 | capacitance | crop_wrong | labels_cut | unsure | no | no |
| r13 | goford/4435.pdf | 4 | 11 | gate_charge | crop_wrong | labels_cut | unsure | yes | no |
| r14 | toshiba/TK72A12N1.pdf | 5 | 86 | rds_on_temperature | labels_not_text | no_ocr_path | no | unsure | yes |
| r15 | nce/NCE30ND35Q.pdf | 4 | 2 | transfer | labels_not_text | title_semantics_gate | no | no | no |
| r16 | goford/GT750P10D5.pdf | 4 | 6 | body_diode | labels_not_text | no_ocr_path | no | unsure | no |
| r17 | good_ark/GSFN3908.pdf | 4 | 7 | capacitance | ocr_misread | labels_dropped | no | yes | no |
| r18 | goford/G2304.pdf | 3 | 6 | rds_on_current | labels_not_text | no_ocr_path | no | unsure | yes |
| r19 | crmicro/CRSS038N08N.pdf | 5 | 8 | body_diode | labels_not_text | no_ocr_path | no | unsure | yes |
| r20 | agmsemi/AGM1095M.pdf | 8 | 7 | capacitance | other | printed_unit_nF | no | no | no |
| r21 | diotec/DIT085N10.pdf | 1 | 1 | gate_charge | crop_wrong | not_a_chart | no | no | no |
| r22 | nce/NCE60ND45AG.pdf | 4 | 6 | body_diode | labels_not_text | no_ocr_path | no | unsure | yes |
| r23 | toshiba/TK72A12N1.pdf | 5 | 85 | rds_on_current | labels_not_text | no_ocr_path | no | unsure | yes |
| r24 | hxy/MSC040SMA120B4-HXY.pdf | 5 | 901 | gate_charge | other | provenance_gate | no | no | no |
| r25 | good_ark/GSF3404B.pdf | 4 | 11 | capacitance | ocr_misread | digit_misread | no | yes | no |
| r26 | kia_semicon_tech/KNB2708A.pdf | 6 | 901 | capacitance | crop_wrong | wrong_chart | yes | no | no |
| r27 | hxy/DMN39M1LFVW-7-HXY.unicoded.pdf | 3 | 2 | transfer | crop_wrong | two_charts | unsure | no | no |
| r28 | toshiba/TK125E60Z1.pdf | 5 | 88 | body_diode | labels_not_text | no_ocr_path | no | unsure | yes |
| r29 | agmsemi/AGM1095M.pdf | 7 | 2 | transfer | labels_not_text | title_semantics_gate | no | no | no |
| r30 | siliup/SP40N02BGTD.pdf | 4 | 901 | capacitance | ocr_misread | digit_misread | no | yes | no |
| r31 | crmicro/CR7N65FA9K.pdf | 5 | 902 | body_diode | labels_not_text | no_ocr_path | no | unsure | yes |
| r32 | siliup/SP60P30TH.pdf | 3 | 901 | transfer | labels_not_text | title_semantics_gate | no | no | no |
| r33 | kia_semicon_tech/KNB2908B.pdf | 5 | 7 | gate_charge | other | passes_on_rerun | no | no | no |
| r34 | xnrusemi/XRS40N10.pdf | 3 | 4 | body_diode | labels_not_text | no_ocr_path | no | unsure | no |
| r35 | huashuo/HSBL020N08.pdf | 4 | 7 | gate_charge | crop_wrong | labels_cut | unsure | no | no |
| r36 | agmsemi/AGM310MD.pdf | 6 | 4 | gate_charge | crop_wrong | not_a_chart | no | no | no |
| r37 | crmicro/CR7N65FA9K.pdf | 5 | 901 | transfer | labels_not_text | title_semantics_gate | no | no | no |
| r38 | rohm/RJ1P04BBHTL1.pdf | 5 | 902 | transfer | labels_not_text | title_semantics_gate | no | no | yes |
| r39 | minos/MPT180N08.pdf | 5 | 7 | body_diode | crop_wrong | wrong_chart | yes | no | no |
| r40 | rohm/RX3P07CBHC16.pdf | 5 | 7 | breakdown | crop_wrong | wrong_chart | yes | no | no |
| r41 | rohm/RS6N120BHTB1.pdf | 5 | 7 | breakdown | crop_wrong | wrong_chart | yes | no | no |
| r42 | toshiba/TK095A65Z5.pdf | 6 | 88 | body_diode | labels_not_text | no_ocr_path | no | unsure | yes |
| r43 | crmicro/CS25N50ANR.pdf | 6 | 12 | capacitance | ocr_misread | nothing_read | no | yes | no |
| r44 | kia_semicon_tech/KNP2708A.pdf | 6 | 901 | capacitance | crop_wrong | wrong_chart | yes | no | no |
| r45 | diotec/DIT085N10-AQ.pdf | 1 | 1 | gate_charge | crop_wrong | not_a_chart | no | no | no |
| r46 | hxy/AOSS21319C-HXY.pdf | 4 | 11 | gate_charge | crop_wrong | not_a_chart | no | no | no |
| r47 | minos/MPG90N08P.pdf | 3 | 2 | transfer | labels_not_text | title_semantics_gate | no | no | yes |
| r48 | minos/IRFB3607.pdf | 3 | 2 | transfer | labels_not_text | title_semantics_gate | no | no | yes |
| r49 | kia_semicon_tech/KNH2908B.pdf | 5 | 7 | gate_charge | other | passes_on_rerun | no | no | no |
