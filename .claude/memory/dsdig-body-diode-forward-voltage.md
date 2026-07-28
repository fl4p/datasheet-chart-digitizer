---
name: dsdig-body-diode-forward-voltage
description: "datasheet-chart-digitizer issue #5 body-diode forward-voltage extraction shipped on main at b409eec: generic body/transfer finder locality, linear/log numeric axes, temperature-labelled VSD/IF traces, human-verified overlays; fitting remains a separate future slice"
metadata:
  type: project
---

# dsdig body-diode forward-voltage extraction

Shipped to `fl4p/datasheet-chart-digitizer` `main` on 2026-07-16 as the audited stack:

- `acb7a2d1279658c1a6412307fc0a003c3a8ba345` — body-diode chart discovery guards
- `33562e86f67b79c4e30d7d3cecc532bbee4768e5` — numeric-axis calibration and forward-voltage trace extraction
- `b409eec271b9ddcd87f174a059cf32436172fd23` — numbered transfer-caption ownership fix required for combined finder correctness

The extraction API emits self-describing `[vsd_v, current_a]` points, calibrated linear/log axes and residuals, assigned curve temperatures, diagnostics, and a verified high-current crossover current when present. It refuses ambiguous axes, unstable temperature ordering, invalid curve counts, and non-local panel bindings.

Human verification passed on three layout/axis strata from the local corpus: Infineon IPP024N08NF2S (log current, two curves, high-current crossover), onsemi FDA032N08 (six-panel caption-association trap, log current), and Diodes DMTH83M2SPSWQ-13 (linear current, six temperature curves). Overlay tick crosshairs are regression-pinned to the exact calibrated intersections and every consumed tick carries its value and unit.

Final landing evidence: combined `206` tests plus `39` subtests passed, FDA caption integration passed `10/10` cold runs, transfer suites passed `15`, and ruff/diff/wheel gates passed. `origin/main` was remotely verified at full hash `b409eec271b9ddcd87f174a059cf32436172fd23`.

This landing is extraction-only. A Shockley-plus-series-resistance fit is intentionally still a separate future module and must validate `point_columns` before consuming points; do not describe issue #5 as having landed a diode fit.
