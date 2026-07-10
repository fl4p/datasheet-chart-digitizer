# datasheet-chart-digitizer

Standalone datasheet chart digitizer.

The package currently ships two MOSFET chart plugins: capacitance plots
(`Ciss`, `Coss`, `Crss` versus `VDS`) and gate-charge plots for Miller plateau
voltage (`Vpl`) extraction. The core pieces are kept generic so other datasheet
chart types can be added as plugins.

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
dsdig digitize-vpl /path/to/datasheet.pdf --datasheet-root /path/to/pwr-mosfet-lib --out work/vpl
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
