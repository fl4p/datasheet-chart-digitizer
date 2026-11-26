# cap_wrong_serve fixtures: provenance

Raster C(V) panel crops that main (e711bc4) served with status `ok` while part of a trace
sat on the wrong ink or was missing. The first five come from the 2026-11 raster-tracer benchmark
sample (`dsdig-i19-tracer-bench`, `out/realcharts/judgements.json`; agent judgements, G6N90F
confirmed by eye by Fab) and none of them is human-verified. Two were added later (see below).

| fixture | source PDF (pwr-mosfet-lib/datasheets) | page / diagram | defect on main |
|---|---|---|---|
| `G6N90F.webp` | `goford/G6N90F.pdf` | 4 / 5 | Crss chord across the knee, 2.5-7.5 V |
| `AGMH022N10H.webp` | `agmsemi/AGMH022N10H.pdf` | 5 / 7 | Coss and Crss start on Ciss ink, then drop |
| `DMN39M1LFVW-7-HXY.unicoded.webp` | `hxy/DMN39M1LFVW-7-HXY.unicoded.pdf` | 4 / 7 | Crss starts on Ciss/Coss ink |
| `PSMN020-30MLC.webp` | `nxp/PSMN020-30MLC.pdf` | 8 / 14 | Coss on Ciss ink, then an 18 px jump |
| `SP010N02GHTF.webp` | `siliup/SP010N02GHTF.pdf` | 4 / 902 | Coss on Ciss ink, then a 13 px jump |
| `4435.webp` | `goford/4435.pdf` | 4 / 10 | Ciss and Crss start at ~1.2 V; ink starts at 0 V (benchmark `partial`) |
| `BSC320N20NS3GATMA1.webp` | `infineon/BSC320N20NS3GATMA1.pdf` | 8 / 11 | Crss starts at ~4.4 V; its steep printed segment from the frame is unserved |

`4435` and `BSC320N20NS3GATMA1` were added with the low-VDS start check (parent `fead2e3`,
2026-11-25); their crops are from `run_part.py` on `fix/capacitance-wrong-serve`, same
conversion. **BSC320N20NS3GATMA1 is in the human-verified capacitance set and was served
`ok`**; the check refuses it for its missing Crss knee. That verdict is pending Fab's review.

- **Image:** the crop PNG `tools/review_ab/run_part.py` wrote at 220 dpi
  (`crops/<part>/p<page>_diagram_<diagram>.png`), converted to grayscale with
  `cv2.COLOR_BGR2GRAY` (what the raster tracer reads), stored as lossless WebP. The round trip
  is pixel-identical.
- **Anchors** used by the tracer seed are the spec-table values main parsed from the PDF; the
  test carries them as literals (G6N90F and DMN39M1 had none).
- **Reproduction:** `find_capacitance_plot_box_with_method` -> `extract_trace_components` ->
  `select_trace_assignment(axis=None)` -> `repair_merged_ciss_coss_identity` on these crops
  reproduces main's served points exactly for all five.
- **PDF sha256:**
  - G6N90F `1c4019e5db847e52e322b23f51edaec00e6823014b35f6c360ec9c04fe7c661a`
  - AGMH022N10H `bf0e399bf37422ce872e6c5a015b383f6562b7cd73fb7179f79c35d2520abbe2`
  - DMN39M1LFVW-7-HXY.unicoded `02640cc904480c66b89751a362f9b114d325f0e507c3b323f0c101fa02378505`
  - PSMN020-30MLC `f0f89a6480a47f7eb60833e3d0c9f3f4d8fade667395f20d4f9054e62b36c46f`
  - SP010N02GHTF `db62d75fd8d26f1ad1c12e5365ec2d4ac9080f58a1ba78bf78dfd08a216e5152`
  - 4435 `1a0ac68ded6a031b4232cf6af3cb3835bf4a1c089089bf89d97043075c77e257`
  - BSC320N20NS3GATMA1 `59f6e4b8aa1c5fd020fbd267dfb5b45d4544e5f182ce58d6a6bfe2000293bc7f`

  The PDFs are not needed to run the tests.
