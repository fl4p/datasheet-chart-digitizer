# RDS(on)-vs-VGS golden panels: provenance

**Human-verified by Fab on the v3 overlays, 2026-09-28.** Fab inspected all 15 v3
overlays and flagged four parts: RQ3E110AJ (tick provenance and calibration span,
R3-8/9/10), BRCS020N03RA (table max below a curve, R3-11), FDP8870 (coincident tails,
R3-13) and RQ3E180AJ (overlay colours only, R3-14). Every digitized curve on the other
11 panels passed his check. Those 11 panels are frozen here.

"Verified" means a visual check of the v3 overlays by a human. It is **not** a guarantee
of every point: a point off the curve by less than the overlay shows would pass such a
check. The goldens pin the output that passed, so any change to it has to be explicit.

- **Source run:** `/Users/fab/dev/ee/solar-charger-eval/rds-vgs/batch15_v3/rdson_gate_voltage.json`.
- **Reviewer manifest:** `/Users/fab/dev/ee/solar-charger-eval/rds-vgs/batch15_v3_manifest.json`.
  It names the primary panel per part, and that is the panel frozen here.
- **Code:** v3 was produced on `feat/rds-vgs` at c09bcf2. Commit b99938a differs from it
  only in a memory note. The Opus round-3 review re-ran b99938a on the raster PDFs and
  reproduced the v3 CSVs byte-identically.
- **Frozen by:** `tools/rds_vgs_freeze_golden.py`, which creates fixtures and never
  rewrites them.
- **PDFs:** not in the repo. The tests find them at `/Users/fab/dev/ee/solar-charger-eval/ds/`
  and check the SHA-256 below. **If a PDF is missing or its hash differs, the panel's test
  SKIPS LOUDLY; it never passes.** The mutation harness treats any skip as a baseline failure.

| part | pdf | page | figure | sha256 |
|---|---|---|---|---|
| AO3400A_UMW_C347475 | AO3400A_UMW_C347475.pdf | 3 | 5 | `ccbfdb29368f0c66c6d94b88a5377a783fdb0f0d7b80259e4ee7612c9f3b4da4` |
| CSD17302Q5A_TI | CSD17302Q5A_TI.pdf | 4 | 7 | `a10d642277e6d56a67a999c3859d015b49a880efa90d89d294e8677b43db2b58` |
| CSD17304Q3_TI | CSD17304Q3_TI.pdf | 4 | 7 | `005a59d174f8a9f826ecee7889ba34ae6ef103162361bdd2fd6341e7cf445f07` |
| CSD17306Q5A_TI | CSD17306Q5A_TI.pdf | 4 | 7 | `0ed210b49ad5569f5a6709add825d57f28f34d355016232852bd26c39cf9c537` |
| CSD17307Q5A_TI | CSD17307Q5A_TI.pdf | 4 | 7 | `834d9176e2a39ca673337e76ffe20fc36fe897a3c89d8f2b2461fc78f850c26c` |
| CSD18502KCS_TI | CSD18502KCS_TI.pdf | 5 | 4-7 | `eca036130a1c414d3014fb3cb50ebfabf61e38cc4d081b6d53d5ec823b2a9bd6` |
| IRLB8743_IFX | IRLB8743_IFX.pdf | 6 | 12 | `acf2086f0c76032388c74520a3a3001f3e45d7af6fb5ba9752ea830712b72332` |
| IRLB8748_IFX | IRLB8748_IFX.pdf | 6 | 12 | `6a4964eb4fe12d7f1cc3b4e1ae8957dcea74255662fd823349d5d15f0c0ed89e` |
| RQ6E080AJ_Rohm | RQ6E080AJ_Rohm.pdf | 7 | 12 | `bf66b42689a3f76a071299b0629a2949aaf32c391b3323ce7f901d45d195eca4` |
| SIS176LDN_Vishay | SIS176LDN_Vishay.pdf | 4 | t491 | `700ec58e30dd04c89324f262d041185a8423be7dca730c607f537b9b3779d820` |
| WSR3090_LCSC_C719278 | WSR3090_LCSC_C719278.pdf | 3 | 2 | `1df5861af883cefe68c908815085e0b3f97f090a211e08c44a3176e319fdd9fd` |

## Round 4 (Fab's review of the v4 overlays, 2026-09-29)

| part | state |
|---|---|
| CSD17306Q5A_TI, CSD17302Q5A_TI, IRLB8748_IFX, IRLB8743_IFX, AO3400A_UMW_C347475, CSD17304Q3_TI, CSD17307Q5A_TI, SIS176LDN_Vishay, CSD18502KCS_TI | **re-verified by Fab on v4, 2026-09-29.** Their data is unchanged (frozen from v3; v4 matched it). |
| BRCS020N03RA_LCSC_C22449012 | **human-verified by Fab on the v4 overlays, 2026-09-29.** Frozen from `batch15_v4` (`/Users/fab/dev/ee/solar-charger-eval/rds-vgs/batch15_v4/rdson_gate_voltage.json`, manifest `batch15_v4_manifest.json`; v4 was produced at 9905d9d, and dac11de added a test only). SHA-256 `f1dcb8bb3f648e5d580d891bf83237c5e349318641ec00d680d7d35c218395ce`. The same caveat applies: a visual check, not a guarantee of every point. |
| BRCS020N03RA_LCSC_C22449012 (after F4-1) | **pending re-bless: F4-1.** It was frozen from v4 as Fab verified it. F4-1 then traced c0's steep head up the band it shares with c1, to the top frame (77 row-traced points, 3.40–3.56 V). No v4 point moved. Until Fab re-blesses, the ink is pinned as for the other pending panels. |
| WSR3090_LCSC_C719278 | **pending re-bless: F4-3.** Fab found the temperature labels wrong or missing on v4. The fix binds them by following the label arrows. Until Fab re-blesses, the test pins only the ink (see below). |
| RQ6E080AJ_Rohm | **pending re-bless: F4-4.** Fab found 3 served pieces for 2 printed curves on v4. Now 2 curves, each its own branch plus the shared tail. The test pins only the ink. |

`PENDING.json` lists the two pending panels. Only Fab's re-blessing removes an entry. For
a pending panel the test checks the calibration. It also checks that every golden point
is still served by some curve, and that every served point is either a golden point or a
point the row tracker added on a steep head (F4-1, listed in `row_traced_points_px`). Every
read value must also be a golden read value. Labels, curve count and grouping are not
compared until the panel is re-blessed.

## Round 7 (Fab's review of the v7 packet, 2026-09-30)

**All six open panels human-verified by Fab on the batch15_v7 review packet**
(`/Users/fab/dev/ee/solar-charger-eval/rds-vgs/review/rds-vgs-v7-001.html`), in his words
"all green". The Round-5/6 changes they carry: I_D and temperature bound by the order
rule (F5-1/F5-3), the print visible under the traces (F5-2), right ends traced to the
frame, and unsampled stretches traced (F6-1). Source run:
`/Users/fab/dev/ee/solar-charger-eval/rds-vgs/batch15_v7/rdson_gate_voltage.json`, manifest
`batch15_v7_manifest.json`, produced at 2481153; a944bcd reproduces it.

- **Newly frozen** (`tools/rds_vgs_freeze_golden.py`):

| part | pdf | page | figure | sha256 |
|---|---|---|---|---|
| RQ3E110AJ_Rohm | RQ3E110AJ_Rohm.pdf | 7 | 12 | `a3643501e6d06eb5dad4cb25dd79959d043a1c9e808626f3571b46406588a850` |
| FDP8870_onsemi | FDP8870_onsemi.pdf | 5 | 9 | `d91898fd60742a51d35f8d7cf465d7ef0356ffbfab429a147b5c7303ed2b5879` |
| RQ3E180AJ_Rohm | RQ3E180AJ_Rohm.pdf | 7 | 12 | `e831dc30efad352f105385d4ac504220b36ee49876ef8de6e5450f92382a19cc` |

- **Re-frozen** (`--refreeze v7`): WSR3090_LCSC_C719278, RQ6E080AJ_Rohm and
  BRCS020N03RA_LCSC_C22449012. Their original fixtures stay untouched; the verified v7
  output sits in `<part>/v7/`. A `"kind": "refreeze"` entry in `REBLESSED.json` points the
  test at it and pins the SHA-256 of every file it supersedes, so the entry goes stale
  (the test fails) if the original changes. Path entries listed before a refreeze
  applied to the superseded fixture and are no longer applied.
- `PENDING.json` is empty: all 15 panels get the full comparison below.

## Front-page copies (Fab's review of the batch_all packet, 2026-09-30)

**Human-verified by Fab** on `rds-vgs/review/rds-vgs-all-001.html`: his exported verdicts
(`rds-vgs-all-001.review.json`, 09:27 UTC) mark these five panels **green**. They are TI's
unnumbered page-1 copies of the figures frozen above. Frozen with
`tools/rds_vgs_freeze_golden.py --panel` from
`/Users/fab/dev/ee/solar-charger-eval/rds-vgs/batch_all/rdson_gate_voltage.json` (produced
at 1fd3bda), each into `<part>__p<page>_d<figure>/` beside the part's primary fixture. The
PDFs and their SHA-256 are the ones in the table above.

| fixture | page | figure |
|---|---|---|
| CSD17306Q5A_TI__p1_dt519 | 1 | t519 |
| CSD17302Q5A_TI__p1_dt525 | 1 | t525 |
| CSD17304Q3_TI__p1_dt512 | 1 | t512 |
| CSD17307Q5A_TI__p1_dt525 | 1 | t525 |
| CSD18502KCS_TI__p1_dt651 | 1 | t651 |

## batch_all parts (Fab's review of the batch_all packet, 2026-09-30)

**Human-verified by Fab** on `rds-vgs/review/rds-vgs-all-001.html`. In the export, the 15
panels below were left "pending"; Fab then stated, in his words, "the pending are actually
green". Frozen from `/Users/fab/dev/ee/solar-charger-eval/rds-vgs/batch_all/rdson_gate_voltage.json`
(produced at 1fd3bda). The primary panel per part comes from
`rds-vgs/work/batch_all_primary_manifest.json` and goes into `<part>/`; TI's page-1 copies go
into `<part>__p1_d<figure>/`. The PDFs are in the same `ds/` directory; each fixture records
its PDF's SHA-256 in `panel.json` (`pdf_sha256`).

| part | primary panel | page-1 copy |
|---|---|---|
| CSD17310Q5A_TI | p4 fig 7 | t537 |
| CSD17309Q3_TI | p6 fig 7 | t544 |
| CSD17318Q2_TI | p5 fig 4-7 | t693 |
| CSD18536KCS_TI | p5 fig 4-7 | t651 |
| IRLB8314_IFX | p5 fig 12 | |
| SISS76LDN_Vishay | p4 fig t491 | |
| IRLTS6342_IFX | p5 fig 12 | |
| AO3416_AOS | p3 fig 5 | |
| VBZM150N03_LCSC_C700703 | p4 fig t500 | |
| IRLB4132_IFX | p6 fig 12 | |
| IRLB8721_IFX | p6 fig 12 | |

Fab verified the curves. Some panel-level fields were frozen as dsdig served them, with
known open defects (`rds-vgs/FINDINGS-batch_all` classes C and D). Fixing those defects
changes these fixtures, and each change must be re-blessed explicitly:

- IRLB8314: validation `not_evaluable`, because the spec-row parser dropped the printed
  typ 2.6 / max 3.2 mΩ.
- IRLB8721 and IRLTS6342: `axis_ticks_not_bound_to_grid`.
- VBZM150N03 and IRLTS6342: chart vs table disagreement. That is a datasheet fact.

## Per panel

- `panel.json`: status, validation verdict and anchor verdicts, plot box, and the
  calibration of both axes (model, fit, used ticks). Per curve: label, temperature,
  temperature kind, ID, usable flag, and the readouts at 2.5 / 3.3 / 4.5 V with their
  states. `reasons_at_freeze` is kept for reference and is not compared.
- `points_c<N>.csv`: every served point: `vgs_v`, `rds_mohm`, `crop_x_px`, `crop_y_px`.

## Comparison (`tests/test_rdson_gate_voltage_golden.py`)

| quantity | tolerance |
|---|---|
| points, crop space | Nearest-point match of 0.5 px or less, in both directions: every golden point has a new point of the same curve within 0.5 px, and every new point has a golden one. |
| readouts | Values within 0.2 % relative. States identical. |
| calibration | The same used ticks (value; pixel within 0.3 px). At every golden tick pixel, the new fit agrees with the golden fit within 0.3 px. Same axis model. |
| labels and flags | Identical: temperature, temperature kind, ID, usable, curve count. |
| panel | Identical: status, validation verdict, anchor verdicts. |

## Re-blessing

A deliberate change to a golden panel is **never** made by editing its fixture.
Instead, add an entry to `REBLESSED.json` with these fields:

- `part`
- the JSON path of the value in `panel.json`
- the frozen value
- the new value
- the finding that motivates the change
- a note
- the date

The test applies these entries to the frozen data before comparing. A change without an
entry fails, and so does an entry whose frozen value no longer matches the fixture.
