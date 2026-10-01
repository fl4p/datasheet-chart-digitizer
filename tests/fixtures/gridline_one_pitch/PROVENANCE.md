# gridline_one_pitch fixtures: provenance

Six Infineon chart crops on which `gridline_anchor` (main 7d8dde7) bound the y labels
one minor grid pitch (~27 px) off. Found by the vendor-truth benchmark
(`out/vlm-chart2table/vendor_checks.py:dsdig_crosscheck`, gitignored).

- **Images:** the benchmark's case PNGs (`out/vlm-chart2table/cases/vendor/infineon_ips/<id>.png`,
  180 dpi renders of the Infineon PDF diagram cell), converted to grayscale and cropped to
  `crop_origin_px` + the plot width with margin, rows from 0 so the black rule above the
  plot stays in. Lossless WebP; the round trip is pixel-identical.
- **Ground truth:** `y_ticks[].true_rule_px` is the VECTOR gridline of each labelled value
  from the PDF drawing, `label_px` the vector label centre; both in pixel-index
  coordinates of the crop (case px - 0.5 - origin). The benchmark gates each chart's
  vendor data at 0.1 % of span; the rendered rules sit 0.14-0.41 px from this mapping.
- **Source PDFs:** `out/vlm-chart2table/vendor_src/infineon/<MPN>/datasheet.pdf`; SHA-256,
  page and diagram per case in `cases.json`. Not needed to run the tests.
