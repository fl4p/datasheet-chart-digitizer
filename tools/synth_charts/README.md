# synth_charts: datasheet-style charts with exact ground truth

Generates seeded, physically plausible datasheet charts as vector PDF pages (1-4 charts per
page) and PNG case crops, with GT that is the drawn geometry itself (clipped to the frame, in
data coordinates). Cases use the `out/vlm-chart2table` schema, so `score_cases.py`,
`frontier/run_frontier.py` and `frontier/inrange.py` run on them unchanged.

Environment: the tool needs matplotlib (`pip install -e .[synth]`); dsdig itself does not.

```bash
python -m tools.synth_charts --n 400 --seed 1 --out out/synth-charts/set1     # generate
python -m tools.synth_charts.validate out/synth-charts/set1                    # self-checks
python -m tools.synth_charts.checks knownbad out/synth-charts/knownbad         # broken-generator controls
python -m tools.synth_charts.export out/synth-charts/set1                      # -> vlm-chart2table/cases_synth*.json
python -m tools.synth_charts.checks scorer --cases cases_synth.json            # perfect / shift / swap
python -m tools.synth_charts.baseline run   out/synth-charts/set1 -j 8         # dsdig on every page
python -m tools.synth_charts.baseline score out/synth-charts/set1              # -> dsdig_baseline/REPORT.md
python -m tools.synth_charts.contact out/synth-charts/set1 sheet.webp [ID ...] # GT overlay contact sheet
```

## Classes (`models.py`)

capacitance (Ciss/Coss/Crss vs VDS; log-log, lin-log, lin-lin with Crss on the 0 rail; Coss/Ciss
crossing, Crss knee, near-merged Coss/Crss), transfer (2-4 Tj, ZTC crossing, shallow crossing,
log-y), output (ID vs VDS per VGS), rdson_temperature (normalised or absolute), rdson_id (flat
curves, curves ending mid-plot), gate_charge (Miller plateau, several VDD, shared first segment),
body_diode (Tj crossing at high current), reverse_leakage (log y), core_loss (Steinmetz, log-log),
mlcc_impedance (|Z| and ESR with a resonance dip), breakdown (V(BR)DSS vs Tj), zth (Foster
ladder, duty cycles).

## Factors (`styles.py`, recorded per case in `factors`)

Grid major/minor (dense log minor grids), tick direction, frame, fonts (sans/serif, sizes),
tick formats (plain, 10^n, 10ⁿ, 1E+4, 1.0E-02, SI prefixes, decimal comma), constant label
offsets from the gridline, label jitter, dropped edge labels, frame past the last label,
contradicting template labels, axis-title placement, caption style (`Figure N.` / `Fig. N` /
Infineon `Diagram N:` in ruled cells), legend / in-plot labels / leader arrows, colour vs
black-and-white, identity by colour / dash / width / none, stroke 0.25-2.2 pt, markers,
vector structure (one path, split pieces, merged subpaths, filled outline, Bezier), clip path vs
pre-clipped, raster-embedded charts (optionally scan-damaged), distractors (reference lines,
annotation arrows crossing curves), curves born on a gridline, signed / reversed axes (P-channel),
page density and neighbour gap, case dpi 72-300, degradations (blur, resample, low contrast,
bleed, rotation/shear with recorded affine, noise/speckle, JPEG). Derived per case: crossings,
minimum crossing angle, closest approach, merged fraction, curves clipped at the frame / ending
mid-plot / hugging a rail.

## Conventions

- Axis `min` is the value at the left/bottom frame edge (`min > max` = reversed axis).
- `plot_box_px`: continuous pixel coordinates, origin at the top-left corner of pixel (0,0), of
  the clean render; for rotated/sheared degradations map through `gt_provenance.degradation.affine`.
- GT = the drawn polyline (or the exact Bezier), clipped to the frame, decimated with a 2e-6-of-span
  Douglas-Peucker bound. Labels: `label` is what the scorer binds, `printed` the text on the chart.
- Tiers: easy / medium / hard / adversarial set the factor distributions; the hard subset export is
  stratified over classes from the hard and adversarial tiers.
