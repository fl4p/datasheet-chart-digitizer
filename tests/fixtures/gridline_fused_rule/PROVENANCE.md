# gridline_fused_rule fixtures: provenance

`ty_MBASL042SCG1R5CWNA01_c_vs_f.webp`: Taiyo Yuden MBASL042SCG1R5CWNA01 (1.5 pF C0G 01005),
"Capacitance Freq." chart. A KNOWN-GOOD control for the fused-rule guard: its decade rules
are drawn bold (a 3 px anti-aliased stroke, grey 118/0/99, against 1 px minors) and are
locally one row thinner along part of their length. A first draft of the guard re-measured
the 100 pF decade on those thinner positions and moved the served y axis from 0.03 px to
0.32 px off the vector rule; the guard must leave such a rule as it is.

- **Image:** the vendor-truth benchmark's case PNG
  (`out/vlm-chart2table/cases/vendor/taiyo_yuden/ty_MBASL042SCG1R5CWNA01_mlcc_c_vs_f.png`,
  gitignored, sha256 `4fae481e7f12c0f25fbcee4cc15818a6188868bb3983e8127b07c9e490a0a796`),
  converted to grayscale, uncropped. Lossless WebP; the round trip is pixel-identical.
- **Ground truth** (in the test): the case's `calibration_ticks_px` -- per labelled value the
  vector label centre and the VECTOR gridline pixel from the PDF drawing, converted to
  pixel-index coordinates (case px - 0.5). The benchmark gates this chart's vendor data
  (Taiyo Yuden store curve `capacitance_vs_f`) at 0.1 % of span.
- **Source PDF:** `out/vlm-chart2table/vendor_src/taiyo_yuden/MBASL042SCG1R5CWNA01.pdf`,
  sha256 `2fd52d83226cc504cdd7c29c2d7daffc145b5f153cdd9e5823f539adc77d657c`
  (https://ds.yuden.co.jp/TYCOMPAS/or/download?pn=MBASL042SCG1R5CWNA01&fileType=Datasheet).
  Not needed to run the tests.
