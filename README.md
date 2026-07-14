# datasheet-chart-digitizer

Standalone datasheet chart digitizer.

The package currently ships four MOSFET chart plugins: capacitance plots
(`Ciss`, `Coss`, `Crss` versus `VDS`), gate-charge plots for Miller plateau
voltage (`Vpl`) extraction, diode reverse-recovery panels (`Qrr`/`Irm`/
`trr`/`S` versus `IF` or `di/dt` at 25/125 °C, Alpha & Omega layout — filled
outline curves, dual linear y axes, spec-table + cross-panel scale
verification in `reverse_recovery_validation.py`), and breakdown-voltage
plots (`V(BR)DSS` versus `Tj`, Infineon Diagram-15 layout and the older
numbered-caption layout — one stroked vector line on linear/linear axes with
negative-`Tj` ticks; plot frame from the vector uniform-pitch gridline
family with raster fallback, a clipping warning when the curve touches the
frame, and a fitted `V(25 °C)`/slope summary plus a tri-state spec-table
anchor verdict that verifies the chart's min-anchored interpretation instead
of assuming it). The core pieces are kept generic so other datasheet chart
types can be added as plugins.

## What It Does

- Finds chart panels in PDF datasheets and writes `charts.json`.
- Emits chart crops for visual inspection.
- Digitizes vector PDF traces first, with raster fallback.
- Calibrates axes from tick labels/gridlines.
- Writes calibrated point CSVs plus overlays.
- For MOSFET capacitance charts, validates `Coss(V)` against datasheet `Qoss`,
  `Co(tr)`, and `Co(er)` where available.

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
dsdig digitize-vpl /path/to/datasheet.pdf --datasheet-root /path/to/pwr-mosfet-lib --out work/vpl
dsdig digitize-reverse-recovery /path/to/AOT414.pdf --out work/rr
dsdig digitize-breakdown-voltage work/charts/charts.json --out work/bv
```

`digitize-vpl` is currently packaged with the repository but still depends on
`pwr-mosfet-lib`'s `dslib.viz` chart finder. Pass `--datasheet-root` pointing at
a checkout that contains both `datasheets/` and `dslib/`. Relative PDF
arguments are resolved under `--datasheet-root/datasheets`. This is the next
piece to replace before Vpl is fully standalone.

If `.pdf.nop.csv` anchor tables are not next to the PDFs, pass their directory:

```bash
dsdig digitize-capacitance work/charts/charts.json \
  --datasheet-root /path/to/anchor-csv-dir \
  --out work/charts
```

Key outputs:

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

This package stops at chart-native artifacts: calibrated point CSVs, validation
manifests, compact Coss knot JSON, and Qoss/Eoss tables. Consumer-specific
database adapters belong in the consumer repository. For example, dslib/fetlib
imports `datasheet-chart-digitizer` and converts reviewed C(V) point CSVs into
its own `COSS_CURVES`/`CISS_CURVES` database format.

## Local Regression Corpus

Fab's workstation has local regression corpora under `/Users/fab/dev/pv/ee/out`
and `/Users/fab/dev/pv/pwr-mosfet-lib`. Run the combined local regression after
trace, calibration, or validation changes:

```bash
python tools/run_local_regression.py
```

This runs the C(V) corpus and the Vpl gate-charge full-curve verifier against
the 15 human-checked Vpl samples. It also runs a 66-sample Vpl finder-parity
guard that compares packaged chart discovery against the current `dslib.viz`
baseline; known packaged-finder misses and legacy-unavailable samples are
explicit and should only shrink while the standalone finder is consolidated.
For C(V)-only work, use:

```bash
python tools/run_capacitance_regression.py
```

The C(V) harness regenerates outputs in a temporary directory and fails on
trace semantic regressions, unexpected untrusted axis calibration, or unexpected
Qoss validation statuses. It includes the focused Coss/Ciss label-overlap
repairs, the dashed-line case, and the 35-chart random-manufacturer C(V) sample.
The Vpl harness runs the packaged `datasheet_chart_digitizer.gate_charge_vpl`
module against the 15 human-reviewed gate-charge overlays in explicit
`--reference-assisted` audit mode. Normal `digitize-vpl` runs do not use human
reference values to choose the reported estimate.

## Scope

The repository name is intentionally generic. Planned plugins include Qoss(VDS),
gate-charge, SOA, diode, thermal-impedance, efficiency, and magnetics curves.
The existing MOSFET capacitance digitizer is the first production-quality
plugin and acts as the reference implementation for extraction, calibration,
overlays, and validation status reporting. The Vpl digitizer is wired as a
package plugin with local human-reference regressions and will be refactored
toward the same module boundaries as it matures. Its remaining external
dependency is chart discovery from `pwr-mosfet-lib`'s `dslib.viz`.
