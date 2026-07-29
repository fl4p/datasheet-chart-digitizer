---
name: dsdig-epc-color-capacitance
description: "EPC GaN C(V) identity comes from the printed colored legend; source-hue overlays are human-review-only; EPC2091/EPC2032 guarded vector rescues passed full-corpus A/B"
metadata: 
  node_type: memory
  type: project
  originSessionId: bee5643e-f421-442b-9742-8e9701d3c45b
  modified: 2026-07-29T06:52:56.001Z
---

EPC GaN capacitance charts draw Ciss/Coss/Crss in color with a colored-swatch legend;
grayscale banding merged the dark pair and missed light-green Crss entirely. Legend-color
binding landed in `capacitance_vector.py` as `0aeef19` (provenance
`legend_color_components`), and linear-Y grid seating on rect-drawn grids as `564f95d`.
Fab human-GREENed all 10 re-extracted EPC panels 2026-07-29; all 13 GaN panels
selected by that review packet now extract `ok` with trusted axes. This does **not**
mean every linear/log sibling in the finder index is recovered.

Follow-up LS2 feedback on 2026-07-29
(`.vibe-drops/da0a2d84-fugu2-100v-LS2p-gan-coss-scalar-top50-001.review.json`)
confirmed EPC2361/EPC2367 fig901 are trace-correct; the questioned red/orange
bottom line is the 6 px plot-box frame overlapping a true near-zero Crss tail,
not another curve. Fab clarified that legend/source hues intentionally render
the capacitance overlays, but **only as human-review presentation**. Keep
`source_color_rgb` out of served extraction values and do not use overlay color
as new curve-identity evidence.

The same feedback exposed live recovery debt on EPC2091 fig902 at clean HEAD
`08d4eda`: vector sees only two candidates because its valid full-width Ciss
path has seven source vertices versus the generic eight-point gate; raster
fallback produces wrong short Coss/Crss but safely refuses physical output.
The guarded rescue lowers the source-vertex threshold only for exactly three
unique color owners with complete one-to-one legend binding.

A second LS2 review on 2026-07-29
(`.vibe-drops/081ff85f-fugu2-100v-LS2p-gan-coss-scalar-top50-001.review (1).json`,
23 GREEN / 2 REWORK) exposed EPC2032 fig901/fig902. Both were already
fail-closed raster gaps. Their legitimate Crss stroke is a light, moderately
saturated green outside the generic curve-color heuristic. A second rescue
admits chromatic strokes only under the same exact three-owner + complete
legend proof. Frozen affected A/B on clean `36cfb89` changes only EPC2032
fig901/fig902 and EPC2091 fig902: all three become vector `ok`,
`trace_validation=pass`, `physical_output_available=true`; EPC2091 fig901 is
byte-identical. The 5x crossings/low-V approaches are source-faithful.

Production-scale negative A/B used 1,093 hash-locked capacitance panels from
1,168 unique PDFs (crop-set
`bddc4235e76d4158516c1f5f76e63e640cb4591ccf25cc0a6abd9f8f3b10be5d`).
Baseline and candidate are identical: 941 results / 152 exceptions, result
manifest `a1dadd95dd204af39e0178dea74110399ca357caefbc7fc9e7ad6860db57a5c3`,
and the artifact-aware comparator reports 0 changed panels. The combined
guarded candidate is A/B-accepted.

**Why:** color identity is source evidence the band heuristic cannot see, and it is
immune to Ciss/Coss crossings by construction (verified at 5x on EPC2022, whose earlier
flag was a bulk comment — its extraction was already correct).

**How to apply:** legend evidence may only ADD naming — absent/partial/ambiguous legends
fall back to positional naming; the sole hard refusal is a complete legend contradicting
right-edge order. Two fail-opens that adversarial review caught and that must stay fixed:
proximity must never arbitrate between differently-colored segments on one legend row,
and the chromatic-slope qualifier must require plot-relevant sloped ink (else a frame rail
is readmitted). Sparse or generic-chromatic paths may only enter through exact
three-color ownership plus complete one-to-one legend proof. See
[[dsdig-orphaned-lane-salvage]] for how the axis half was recovered.
Open from the same review: `toshiba/TPH2R70AR5` coss-snaps-to-ciss (log-log raster,
already fail-closed).
