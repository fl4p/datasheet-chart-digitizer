# datasheet-chart-digitizer

Standalone datasheet chart digitizer.

The package currently ships one mature chart plugin: Infineon-style MOSFET
capacitance plots (`Ciss`, `Coss`, `Crss` versus `VDS`). The core pieces are
kept generic so other datasheet chart types can be added as plugins.

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
```

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

## Scope

The repository name is intentionally generic. Planned plugins include Qoss(VDS),
gate-charge, SOA, diode, thermal-impedance, efficiency, and magnetics curves.
The existing MOSFET capacitance digitizer is the first production-quality
plugin and acts as the reference implementation for extraction, calibration,
overlays, and validation status reporting.
