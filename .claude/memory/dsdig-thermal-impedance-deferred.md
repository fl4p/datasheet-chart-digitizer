---
name: dsdig-thermal-impedance-deferred
description: "TI Zth extractor is human-rejected: points scatter off printed curves; entire slice deferred to dsdig issue #8"
metadata:
  type: fact
---

The transient-thermal-impedance (Zth) extraction slice is **human RED** and must
not be landed. All three TI review overlays (CSD19534KCS, CSD19531KCS, and
CSD86311W1723) showed extracted points sprinkled across the chart rather than
staying on their printed duty-cycle curves. Automated axis, collapse, unit, and
test gates were green, but they did not prove point-to-source-stroke fidelity.

Defer the entire uncommitted numeric-axis/OCR/thermal stack to
`fl4p/datasheet-chart-digitizer#8`. Rework must add a direct source-stroke
distance/fidelity gate and repeat the same three human overlays. Frozen thermal
hashes `75056bb3...` / `2b243e05...` are rejected evidence, not a landing
candidate. See [[chart-overlay-tick-labels]] for the human-overlay contract.
