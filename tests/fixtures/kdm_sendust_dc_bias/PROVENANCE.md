# kdm_sendust_dc_bias: provenance

Reference data for `digitize-powder-dc-bias` (`powder_dc_bias.py`). This is one chart,
KDM's Sendust "%µ vs DC magnetizing force" panel, in three renderings.

## Truth: the vendor's printed rows

The panel prints its own law,

    %µ = 100 / (a + b·H^c)        H in oersted, %µ = percent of initial permeability

with one (a, b, c) row per grade (26, 40, 60, 75, 90, 125µ). The truth is these printed
rows, not a digitizer output or a human trace. The rows were read off each rendering at
4–8× zoom.

- The 40/60/75/90/125µ rows are identical in all three renderings.
- The 26µ row differs: `Ljf_KS.pdf` prints 3.46E-05 / 1.861, while the other two print
  5.59E-05 / 1.825. Each case therefore carries the rows printed on its own rendering.
- `Ljf_KS.pdf` prints 125µ c as "1.78"; the other two print "1.780".

Independent anchors that the printed 125µ and 60µ rows describe the material:
- **KS184-125A spec table:** L(50 Oe) min 344.4 µH with L0 913.0 µH ±8 %. That is 41.0 %
  of the −8 % L0; the printed 125µ row gives 41.4 %.
- **KDM material overview (`Ljf_KDM.pdf`):** Sendust 47 % at 100 Oe; the 60µ row gives 47.2 %.

## Sources (not committed)

The vendor PDFs are not committed, following `rds_vgs_golden` and the Keil test. The test
reads them from the paths and checks the SHA-256 in `cases.json`. **A missing PDF or a hash
mismatch skips loudly ("REFERENCE NOT CHECKED"); it never passes.**

| id | file | page | content |
|---|---|---|---|
| `ks184_vector` | `ee/datasheets/kdm/KS184-125A_semic_Ljf_T184-S-125A_KD.pdf` | 1 | Vector page: six stroked Bezier curves, white gridline strokes, outlined glyphs |
| `ljf_ks_raster` | `ee/datasheets/kdm/semic-2026-11-24/Ljf_KS.pdf` (semic.cz) | 1 | 300 ppi JPEG strips |
| `catalog_p14_raster` | `ee/datasheets/kdm/semic-2026-11-24/k6_Catalogue_Alloy_Powder_Core.pdf` (semic.cz) | 14 | 128 ppi JPEG scan; top panel. Page 18 of the same catalogue is the B-H curves, not this chart |

Fetched 2026-11-24 by plain HTTPS; URLs are in `cases.json`.

## Tolerance

Measured on branch `powder-dc-bias` (2026-11-24). Per curve, the digitized points
(%µ levels 5..95) are compared with the printed row.

| case | levels used per curve | worst rms %-pt | worst max %-pt |
|---|---|---|---|
| ks184_vector | 18–19 | 0.33 (125µ) | 0.53 (60µ) |
| ljf_ks_raster | 14 | 0.48 (125µ) | 0.77 (60µ) |
| catalog_p14_raster | 10 | 0.37 (60µ) | 0.80 (60µ) |

The gates are rms ≤ 0.5 and max ≤ 0.9 %-pt per curve, plus a minimum number of levels per
case. The printed 10 %-pt gridlines are unevenly drawn in the artwork (15.09 pt pitch with
three 14.20 pt gaps on the vector page; the same pattern appears in `Ljf_KS`). That alone
puts about ±0.5 %-pt on any reading, so the gate is not tighter than the chart.

The binding check requires each curve to lie closest (median |residual|) to its own
printed row. Known-bad calibration:
- Reversing the grade order makes the module refuse the chart.
- A 25 % error in b (125µ) exceeds the max gate.

## Review state

**Not human-verified.** The class status is `overlay-review-required` and stays that way
until Fab approves the overlays. Do not mark these cases `human_verified`.
