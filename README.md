# datasheet-chart-digitizer

Standalone datasheet chart digitizer.

The `dsdig annotate` workflow currently detects and overlays these MOSFET chart
families:

1. Capacitance plots (`Ciss`, `Coss`, and `Crss` versus `VDS`).
2. Gate-charge plots for Miller plateau voltage (`Vpl`) extraction.
3. Diode reverse-recovery panels (`Qrr`/`Irm`/`trr`/`S` versus `IF` or
   `di/dt` at 25/125 °C, Alpha & Omega layout — filled outline curves, dual
   linear y axes, and spec-table + cross-panel scale verification in
   `reverse_recovery_validation.py`).
4. Breakdown-voltage plots (`V(BR)DSS` versus `Tj`, Infineon Diagram-15 layout
   and the older numbered-caption layout — one stroked vector line on
   linear/linear axes with negative-`Tj` ticks; plot frame from the vector
   uniform-pitch gridline family with raster fallback, a clipping warning when
   the curve touches the frame, and a fitted `V(25 °C)`/slope summary plus a
   tri-state spec-table anchor verdict that verifies the chart's min-anchored
   interpretation instead of assuming it).
5. Saturation transfer plots (`Id` versus `Vgs` at multiple junction
   temperatures), with optional anchor-based temperature-coefficient fitting
   whose output requires human review before curation or use.
6. Body-diode forward-voltage plots (`Is` versus `Vsd`).
7. On-resistance plots: absolute `RDS(on)` versus drain current and normalized
   `RDS(on)` versus junction temperature.
8. On-resistance versus gate-source voltage, `RDS(on)(VGS)` at one or more Tj/ID
   (unvalidated class: samples await human overlay review before batch use).

The core pieces are kept generic so other datasheet chart types can be added
as plugins.

## What It Does

- Finds chart panels in PDF datasheets and writes `charts.json`.
- Emits chart crops for visual inspection.
- Records each crop's effective PDF region as `crop_box_pt`; digitizers use a
  shared PDF-point/crop-pixel transform so vector geometry, raster traces, and
  position-based calibration stay aligned. Legacy indexes fall back to the
  historical two-point crop margin.
- Digitizes vector PDF traces first, with raster fallback.
- Calibrates axes from tick labels/gridlines.
- Writes calibrated point CSVs plus overlays.
- For MOSFET capacitance charts, validates `Coss(V)` against datasheet `Qoss`,
  `Co(tr)`, and `Co(er)` where available.
- Scores candidate `Ciss`/`Coss`/`Crss` assignments against datasheet table
  anchors and records per-anchor log/relative residuals in the manifest. Anchor
  evidence can relabel vector or raster traces only when multiple anchors agree;
  graph/table inconsistencies remain diagnostics rather than forced fits.

## Install

```bash
python3 -m pip install -e .
```

The command-line tools require `pdftotext` and `pdftoppm` from Poppler.

## Usage

```bash
dsdig find /path/to/datasheets/*.pdf --out work/charts
dsdig digitize-capacitance work/charts/charts.json --out work/charts
dsdig export-coss-spice work/charts/points/crops/PART/pNN_diagram_MM.points.csv --out work/spice --name PART
dsdig export-coss-spice work/charts/capacitance_digitization.json --out work/spice-batch
dsdig export-coss-dslib work/charts/capacitance_digitization.json --out work/dslib-coss
dsdig digitize-vpl /path/to/datasheet.pdf --out work/vpl
dsdig digitize-reverse-recovery /path/to/AOT414.pdf --out work/rr
dsdig digitize-breakdown-voltage work/charts/charts.json --out work/bv
dsdig digitize-transfer work/charts/charts.json --out work/transfer
dsdig digitize-rds-vgs work/charts/charts.json --out work/rds-vgs
dsdig digitize-rds-vgs --pdf /path/to/datasheet.pdf --out work/rds-vgs
dsdig annotate /path/to/datasheet.pdf --out /path/to/datasheet-with-curves.pdf
datasheet-layout-cluster /path/to/datasheets --out work/layouts
```

`annotate` is the single-PDF review workflow: it detects every currently supported chart,
runs the corresponding digitizer, writes all crops/overlays plus
`annotated_pdf_manifest.json` under a sibling `*-artifacts` directory, and places accepted
digitized curves back inside a copy of the original PDF. Use `--work-dir` to choose a different
artifact directory and `--dpi 220` (the default) to control render resolution. Panels that an
extractor refuses remain explicit in the manifest and are not painted into the output PDF.
Only explicit accepted statuses (`ok`, `pass`, and `verified`) are painted by default. Add
`--include-review-required` when preparing a human-review sheet that should also show overlays
marked `overlay-review-required`; fail-closed statuses still remain excluded.

`digitize-vpl` is standalone and uses the package's generic chart finder. Its
package-owned experimental `GateChargeResult` records the selected panel, Vpl
estimate, status, trace source, score, curve points, axis evidence, and
diagnostics. Callers must retain the result metadata; there is intentionally no
package scalar `find_vpl()` API because the result status and diagnostics are
part of the experimental compatibility contract.
Relative PDF arguments are resolved under `--datasheet-root/datasheets`.

The Vpl digitizer can use an installed `tesseract` executable in two bounded
fallback cases. If normal discovery finds no gate-charge panel, per-page OCR can
supply words to a second discovery pass. If a normally discovered panel produces
only an assumed or grid-inferred axis, OCR can retry that panel's axis
extraction before the result is accepted or refused. OCR words are mapped back
to PDF-point coordinates and recorded with `text_source=tesseract_fallback`.
Missing, failed, or timed-out Tesseract runs leave the native result unchanged;
they do not replace the normal finder path.

`datasheet-layout-cluster` is an offline corpus-indexing tool for choosing
representative regression samples. It emits page-level chart/table/mixed
clusters and whole-document layout clusters from normalized geometry, text
occupancy, image placement, caption families, frames, and ruling lines. Vendor
and part-number text do not participate in similarity, so layouts may cluster
across series or vendors. Files named like `PART.pdf.r600.pdf`,
`PART.pdf.gs.pdf`, `PART.pdf.cups.pdf`, or `PART.pdf.sips.pdf` are excluded from
clustering and recorded separately in `generated-pdf-variants.json`. Layout
clusters are never runtime detector authority.

`digitize-rds-vgs` scans the PDFs named in a `charts.json` (or given with `--pdf`) with its
own caption/frame locator, because the shared finder misses most RDS(on)-versus-VGS panels.
A panel is owned only when its x-axis title names VGS and not a drain current. It serves
per-curve `(VGS [V], RDS(on) [mOhm])` points with the curve's temperature *as printed*
(`temperature_c` plus `temperature_kind`: Tj, Tc, Ta, or unspecified) and ID when a label
binds to it (legend swatch, leader line, proximity, or elimination -- otherwise `None`).
Readouts at 2.5/3.3/4.5 V are interpolated along the curve and always labelled "typical
curve, not a guaranteed value". Outputs: `rdson_gate_voltage.json`, `crops/`,
`overlays/PART/*.rds_vgs_overlay.png`, `points/PART/*.rds_vgs_points.csv`, OCR scratch
under `work/`.

Status rules (RDS(on)-vs-VGS):

- `refused`: nothing is served -- axes not calibrated, RDS unit unreadable, no curve
  traced, a curve rising with VGS, a non-linear x axis, or tick scatter too large.
- `ok`: every gate passed with no reason at all, and the table check is `verified`. In
  particular an `ok` panel has no curve with a gap, no partial raster trace, no curve
  marked `usable: false`, no unbound curve parameter, and no readout in the states below
  other than `read` / `not_on_chart`.
- `review_required`: anything else; every cause is listed in `reasons`.

Readout states: `read`; `not_on_chart` (outside the source curve's plotted span -- only
claimed where the trace is known to be complete there: vector paths, or raster ends on the
frame); `not_in_extracted_trace` (outside or inside a gap of a raster trace that stopped
inside the plot -- the source may have the curve there); `curve_not_usable`. Nothing is
extrapolated and nothing is interpolated across an unread stretch. Unread stretches are
listed per curve (`gaps`, with their kind in `gap_kinds`) and drawn as breaks in the
overlay: `gap` (no curve ink traced there), `untraced_section` (the ink is continuous but
the column tracker did not sample it, e.g. a near-vertical stretch), `annotation_contact`
(points pulled off the curve by a touching arrow or label were removed; a removed point
always opens an interval between its surviving neighbours, however close they are, and
its VGS is listed in `annotation_contact_removed_vgs_v`). Readouts and the overlay use the
same list of intervals. Only columns hidden under a vertical grid rule the tracker itself
erased are bridged (`columns_interpolated_across_erased_grid_rules`); a projection peak
is erased as a rule only if it is dark over >= 90 % of the plot height, or >= 75 % and on
the tick lattice (`raster_grid_rules_px` lists erased and refused peaks). A 1-2 point end
cut off from a raster track by a real gap is dropped before the track is admitted as a
curve, and every dropped point is listed (`dropped_end_stub_points`, with a reason). `temperature_kind` is null only when
`temperature_c` is null, and every curve without a temperature carries a
`curve_N_temperature_c_unknown` reason. The overlay header lists every reason, word-wrapped
to the image width and never clipped; the legend shows each readout's state (`n/c`,
`not traced`, `unusable`). A raster fragment spanning under 30 % of the VGS axis whose
ink, followed from BOTH ends, does not run on to the frame (`trace_complete.*_ink_reaches_frame`)
is `usable: false` with a `not_usable_reason`, gets no readouts, and is never validated.

Calibration provenance: `tick_source` names where the used tick labels came from
(`text_layer`, `page_ocr` for an image-only page, `panel_ocr` for a raster panel on a text
page, `crop_ocr`, `axis_band_ocr`), and `tick_origins` gives it per tick. When the used
ticks stop more than half a step short of a frame edge, crop and axis-band OCR are added
and the axes refitted (`tick_completion` says what happened). `used_tick_span` records
each axis's used range. A served reading beyond it carries `calibration_span:
outside_anchored` if the frame edge sits within 1 px of the fitted tick lattice, and
otherwise `outside_unanchored` plus a `curve_N_readout_outside_calibrated_span` reason,
which keeps the panel off `ok`. Two curves drawn on top of each other are listed in
`coincident_with` on both, with a reason. A usable curve read above a table maximum at the
table's VGS, which no anchor judged, is recorded in `validation.diagnostics` together with
what is unknown about its bindings, without a verdict. The overlay marks every used tick
with blue "+" markers and values on the plot (the v3 style), draws traces in an Okabe-Ito
palette (no dark colours) on white halos with nested widths (c0 widest, each later curve
narrower on top, all solid), labels each curve directly, and gives each curve a legend
row with a swatch.

Raster curve structure: a steep head is traced row by row up to the frame across grid
rules (`row_traced_points_px`); a head still short of the frame while its ink runs on
gets `curve_N_head_not_traced_to_frame`. Exactly the printed curves are served: a branch
that merges into a tail takes the tail over (`shared_tail`), the tail is never a curve of
its own, and two lines printed side by side as one band are split into their halves;
shared stretches are `coincident_with`. Label arrows and leader lines are followed
straight to their tip, across the curves they cross (`raster_leaders_px`); a label the
plot OCR missed is read at the arrow's tail; a tip in ink shared by two touching lines
names neither (`leader_tip_between_touching_curves`).

Table check: `verified` needs a consistent anchor at the table's own drain current (within
2 %); consistent anchors only at a nearby current (within the 0.75-1.34 ratio used for
evaluation) give `consistent_at_approximate_conditions`; any inconsistent anchor gives
`inconsistent`; otherwise `not_evaluable`. Each anchor states the temperature kind on
both sides and any equivalence it assumed (e.g. chart Tc taken as table Ta), and an
unreadable table cell (e.g. a max printed "12..8") is reported and not checked.

Key capacitance-pipeline outputs:

- `charts.json`: chart panel index.
- `crops/...png`: cropped chart panels.
- `overlays/...overlay.png`: digitized traces overlaid on the chart.
- `points/...points.csv`: pixel and calibrated data-space trace points.
- `capacitance_digitization.json`: diagnostics and validation manifest.

## Coss SPICE Export

For compact storage, keep Coss as adaptive log-space knots rather than a global
polynomial capacitance fit. For simulator use, integrate those knots to charge:

```text
digitized Coss(V) -> adaptive log-space Coss knots -> Qoss(V) table -> simulator-specific model
```

`export-coss-spice` reads the calibrated `Coss` rows from a `.points.csv` file,
from a `capacitance_digitization.json` manifest, or from a digitizer output
directory. It stores compact adaptive knots in `log10(Coss)` versus
`log1p(VDS/Vscale)`, then integrates that model to a monotone `Qoss(V)` table.
For each exported curve it writes:

- `<name>.coss_model.json`: adaptive Coss knots plus the derived table.
- `<name>.qoss_table.csv`: `VDS`, `Coss`, `Qoss`, and `Eoss` samples.
- `<name>.qoss_table.cir`: a QSPICE-oriented behavioral current-source snippet
  using `I = ddt(Qoss(VDS))`.

When the input is a manifest or directory, all discovered `.points.csv` files
are exported and `coss_export_manifest.json` records the generated paths plus
fit error and effective-capacitance summary values.

The `.cir` snippet uses QSPICE/LTspice-style `table()` syntax, but it is not an
LTspice switching-loss validation artifact. In the dcdc-tools loss harness,
LTspice over-counted switching loss with behavioral charge models during fast
Coss rings; QSPICE handled the same charge formulation correctly. Treat the
JSON/CSV outputs as the portable source of truth and build simulator-specific
primitive or fitted models from them when needed.

## Downstream Consumers

The package emits chart-native artifacts—calibrated point CSVs and validation
manifests—plus explicit, file-based export formats. `export-coss-spice` writes
portable Coss/Qoss model artifacts, while `export-coss-dslib` converts only
validation-gated capacitance manifests into dslib-style `(VDS, Coss, Crss)`
knots and optional `(VDS, Ciss)` pairs. The latter records pass/rejection
reasons in per-chart JSON files and `dslib_coss_manifest.json`; it does not
modify a downstream parts database. Database persistence, curation, and other
consumer-specific integration remain the consumer repository's responsibility.

## Local Regression Corpus

Maintainers with access to the local regression corpora should run the combined
regression after trace, calibration, or validation changes:

```bash
DSDIG_DATASHEET_ROOT=/path/to/datasheet-corpus \
  python tools/regression/run_local_regression.py
```

This runs the C(V) corpus and the Vpl gate-charge full-curve verifier against
the 15 human-checked Vpl samples. It also runs a 66-sample Vpl finder-parity
guard that compares packaged chart discovery against the current `dslib.viz`
baseline. The packaged finder currently matches every legacy-available sample;
legacy-unavailable samples remain explicit while the standalone finder is
consolidated. Pages whose Poppler text is visibly glyph-corrupted can use a
conservative PyMuPDF text fallback, recorded as `text_source` in chart metadata.
For C(V)-only work, use:

```bash
python tools/regression/run_capacitance_regression.py
```

The C(V) harness regenerates outputs in a temporary directory and fails on
trace semantic regressions, unexpected untrusted axis calibration, or unexpected
Qoss validation statuses. It includes the focused Coss/Ciss label-overlap
repairs, the dashed-line case, and the 35-chart random-manufacturer C(V) sample.
The Vpl harness runs the packaged `datasheet_chart_digitizer.gate_charge_vpl`
module against the 15 human-reviewed gate-charge overlays in explicit
`--reference-assisted` audit mode. Normal `digitize-vpl` runs do not use human
reference values to choose the reported estimate. Finder parity measures chart
discovery only; numerical Vpl acceptance is checked separately. The current
dslib reference-corpus gate passes all 63 entries: 63 estimates within ±0.5 V,
0 outside tolerance, 0 unresolved, and 0 missing PDFs. Downstream cutover from
the legacy estimator remains a separate consumer change.

## Axis calibration anchoring

Every class serves the value→pixel mapping from the rules the tick labels name: gridlines,
or tick marks where a chart has no solid grid. It never uses the label glyph centres, which sit
up to several pixels off their rule. Measured before the fix: 6.9 px (0.9 % of span) on
reverse leakage, and up to 0.46 % on capacitance, gate charge and reverse recovery.

`gridline_anchor.check_served_on_grid` checks the served mapping at every labelled tick. It
returns verified, failed or unverified. An axis it cannot evaluate is `unverified`, never OK.
It was calibrated on known-bad fits: a label-centre fit fails, and a shift of any size up to
three grid pitches never flips back to verified.

Known open issue: on 7 Infineon IPS-verified charts, the y axis registers one grid pitch off
(see `docs/model-benchmark-2026-09.md`).

## A/B regression over human-verified charts

`tools/review_ab/` re-runs a branch and its base over the human-verified sets and compares
served status, served curve values and Vpl. A part or chart present on only one side is
reported as INCOMPLETE and exits 2; missing evidence is never a pass. Run it before merging
any trace or calibration change. The acceptance rule used so far: no status regressions,
and no served value moving by more than 0.5 % of span without a PDF-render review.

```bash
python tools/review_ab/hv_ab.py <src-tree> <label> capacitance <hv-key> 8   # one side
python tools/review_ab/hv_cmp.py <tree-A> <tree-B> out.json                # compare
```

See `tools/review_ab/README.md`.

## Synthetic chart generator

`tools/synth_charts/` renders seeded datasheet-style charts: vector PDF pages plus PNG
crops, 12 chart classes, and four difficulty tiers. The ground truth is exact: vector p95
≤ 0.0002 % of span. Style and degradation factors are recorded per case (grid density, tick
formats, label offsets, stroke width, legends vs leaders, raster damage, path structure), so
dsdig accuracy can be broken down by factor. `python -m tools.synth_charts.baseline` runs
dsdig on every page and writes a per-class and per-factor report. See
`tools/synth_charts/README.md`.

Result on set1 (400 charts) after the fixes merged with it: dsdig serves 50 of the 295
charts in its classes, 1 of them wrong. Served charts are accurate: capacitance p95 0.11 %,
body diode 0.22 %, gate charge 0.31 % of span. Most unserved charts are explicit refusals.

## Agent skill

`skills/chart-digitization/` is the agent skill for reading values off datasheet charts:
dsdig first, what its statuses mean, the hand-then-multi-model fallback, and review rules.
`IMPROVING.md` there covers changing and benchmarking dsdig. It is symlinked into
`~/.claude/skills/chart-digitization`, so it versions with the code.

## Benchmark and model comparison

`docs/model-benchmark-2026-09.md` compares dsdig with vision-language models on four sets:
- human-verified charts
- vendor-data ground truth (Infineon IPS, Taiyo Yuden, Murata)
- the RDS(on)-vs-VGS goldens
- the synthetic hard subset

The harness lives in `out/vlm-chart2table/` (gitignored, local).
`docs/chart-digitisation-landscape-2026-09.md` and `docs/chart-digitisation-benchmarks-2026-09.md`
survey the literature, tools, benchmarks and reference-data sources.

## Scope

The repository name is intentionally generic. Planned plugins include Qoss(VDS),
SOA, thermal-impedance, efficiency, and magnetics curves. Body-diode forward
voltage and both supported `RDS(on)` chart families are included in the
single-PDF `annotate` workflow.
The existing MOSFET capacitance digitizer is the first production-quality
plugin and acts as the reference implementation for extraction, calibration,
overlays, and validation status reporting. The Vpl digitizer is a
self-contained package component with a result-oriented Python API and
regression checks against locally stored, human-reviewed datasheet samples.
