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
