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

On 2026-07-30, a 2,239-part fetlib run under Homebrew Python 3.14 was still
importing the global `3894959` package and emitted 270 unique gate-charge
refusals. Nineteen refused parts were already covered by the newer committed
bounded-axis regression corpus. Clean dsdig commit
`c0e8d68dd3c69e1135b8a092f15eeb725a576c11` passed 28 focused tests plus 37
subtests, and production-bridge replays recovered both `NCE0160G` and
`GSFT7R515`. That exact clean commit was installed into the Python 3.14 user
site, which precedes the stale global package and changes fetlib's
`chart_digitizer_salt()` for future processes.

This is a local runtime remediation, not a durable dependency update:
`pwr-mosfet-lib/requirements.txt` remains pinned to `3894959` because `c0e8d68`
was 20 commits ahead of `origin/main` and not yet available from the remote.
After the dsdig commits are pushed, update the fetlib pin; until then, a package
reinstall can reintroduce the stale runtime.

Later on 2026-07-30, the actual local fetlib interpreter was confirmed as
`/Users/fab/dev/venvs/fetlib/bin/python3` on Python 3.10.14. Dsdig's `>=3.11`
metadata floor had existed since the initial commit, while all 55 current source
modules compiled and imported under 3.10. After upgrading that venv from
PyMuPDF 1.24.14 / Pillow 10.4.0 / OpenCV 4.10.0.84 to PyMuPDF 1.28.0 /
Pillow 12.3.0 / OpenCV 5.0.0.93, the 31-test focused gate-charge suite passed
under Python 3.10 and the metadata floor was lowered to `>=3.10`. The venv now
has an editable install of this checkout; production `dslib.viz` bridge checks
recover `NCE0160G` and `GSFT7R515` with bounded OCR.

The editable install deliberately follows the checkout's working-tree files,
including uncommitted edits; use a commit-pinned installation when a frozen
runtime is required.
