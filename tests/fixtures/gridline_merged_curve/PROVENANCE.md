# gridline_merged_curve fixtures: provenance

`ifx_ISC058N04NM5_d05_output.webp`: Infineon ISC058N04NM5, datasheet page 7, "Diagram 5:
Typ. output characteristics". On the raw raster the V_GS = 4 V curve runs ~3 px above the
0 A rule and merges with it into one 6 px run (rows 655-660, centre 657.5) while the rule
itself is rows 659-660 (vector centre 659.29). Found by the vendor-truth benchmark
(`out/vlm-chart2table/cases_vendor.json`, gitignored), refused on main 6a1a61b.

- **Image:** the benchmark's case PNG
  (`out/vlm-chart2table/cases/vendor/infineon_ips/ifx_ISC058N04NM5_d05_output.png`,
  sha256 `820d65777b6509430c88acae1dd329910a7b420e9c39941c3c07a3aa800d275b`, a 180 dpi render of the PDF diagram cell), converted to
  grayscale, uncropped. Lossless WebP; the round trip is pixel-identical.
- **Ground truth** (in the test): `Y_TRUTH` / `X_TRUTH` are the VECTOR gridline pixel of
  each labelled value from the PDF drawing and the vector label centre, both in
  pixel-index coordinates (case px - 0.5). The benchmark gates this chart's vendor data
  (Infineon IPS output-chars feature 4831) at 0.1 % of span.
- **Source PDF:** `out/vlm-chart2table/vendor_src/infineon/ISC058N04NM5/datasheet.pdf`,
  sha256 `1fc32d8d8cdf20895a41ccdd29a749ea9673223353585e16e66888696b291655`.
  Not needed to run the tests.
