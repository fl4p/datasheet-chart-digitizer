---
name: dsdig-dslib-dependency-inversion
description: dsdig owns Vpl extraction and passes the 63-case corpus; fetlib now defaults its scalar API to the pinned package implementation
metadata:
  type: project
---

`datasheet-chart-digitizer` owns Vpl discovery, tracing, calibration, and result
provenance without a runtime dependency on `dslib.viz`. Its package API is
`GateChargeResult`/`find_vpl_result()` rather than a scalar so callers can retain
status, diagnostics, trace/axis provenance, and overlay geometry. Locality,
irregular-plateau, dual-axis, faint-vector, raster-axis, and optional bounded
Tesseract fallback fixes landed through `3894959`. The human-reference gate is
accepted at 63 within ±0.5 V / 0 off / 0 unresolved / 0 missing.

`pwr-mosfet-lib` commit `b117457b` pins the full `3894959` dsdig hash and makes
the default `dslib.viz.find_vpl(pdf)` scalar use the package result. The
full-result bridge remains `find_vpl_package_result()`. Explicit non-default
legacy extraction controls (`enable_raster=False` or `enable_ocr=True`) still
delegate to the legacy implementation. AGM smoke is package 4.1864 V versus
legacy 4.1800 V. Commit `bc4e7beb` hardens the local 63-case scalar gate:
`VPL_REQUIRE_ALL=1 python3 test/test_viz_vpl.py` requires all fixtures, fails
unresolved existing PDFs, pins the sample count, and runs without unrelated
fontTools imports. Pushed tips verified on 2026-07-15: dsdig `main` at
`3894959e02a557572803b9f40207ec3a94b5de85` and fetlib `master` at
`bc4e7beb8986333d3911e2596a4955965842658b`.
