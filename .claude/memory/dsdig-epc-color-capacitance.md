---
name: dsdig-epc-color-capacitance
description: "EPC GaN C(V) identity comes from the printed colored legend; selected linear panels are human-GREEN, while EPC2091 fig902 sparse-vector recovery remains open"
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
not another curve. Do not recolor extraction to the source hues: review colors
must remain distinct from source colors.

The same feedback exposed live recovery debt on EPC2091 fig902 at clean HEAD
`08d4eda`: vector sees only two candidates because its valid full-width Ciss
path has seven source vertices versus the generic eight-point gate; raster
fallback produces wrong short Coss/Crss but safely refuses physical output.
A strict isolated prototype requiring exactly three color owners plus complete
one-to-one legend binding recovers 538/538/538 vector columns and passes 88
focused tests. It is not landed: the historical frozen 800-panel chart
index/crops expired from `/private/tmp`, so the authoritative full-corpus A/B
must be regenerated or replaced by an agreed frozen production corpus first.

**Why:** color identity is source evidence the band heuristic cannot see, and it is
immune to Ciss/Coss crossings by construction (verified at 5x on EPC2022, whose earlier
flag was a bulk comment — its extraction was already correct).

**How to apply:** legend evidence may only ADD naming — absent/partial/ambiguous legends
fall back to positional naming; the sole hard refusal is a complete legend contradicting
right-edge order. Two fail-opens that adversarial review caught and that must stay fixed:
proximity must never arbitrate between differently-colored segments on one legend row,
and the chromatic-slope qualifier must require plot-relevant sloped ink (else a frame rail
is readmitted). See [[dsdig-orphaned-lane-salvage]] for how the axis half was recovered.
Open from the same review: `toshiba/TPH2R70AR5` coss-snaps-to-ciss (log-log raster,
already fail-closed).
