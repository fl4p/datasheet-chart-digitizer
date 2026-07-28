---
name: dsdig-superscript-axis-trust
description: Parse Unicode superscript axis ticks semantically from span geometry; active axis errors override a trusted calibration flag
metadata:
  node_type: memory
  type: project
---

NXP capacitance PDFs exposed a systematic tick bug: PyMuPDF word text flattened
`10²` to `102`, and the old tokenizer then served 102 V instead of 100 V on all
17 charts. Parse superscript runs as exponents (`10²` = 100, `10⁻²` = 0.01),
using span geometry because word extraction may lose the superscript relationship.
Keep a true linear `102` fixture so exponent handling cannot become heuristic
power-of-ten snapping.

Separately, `axis_calibration_trusted=true` is invalid whenever an active
`axis_position_error`, `axis_grid_error`, or `axis_ocr_error` remains. Preserve
failed attempts as separate provenance, but never let the trusted flag override
an active residual/error. PSMN013-100BS was the known-bad: it reported a 25.82 V
position residual and multiple axis errors while still claiming trusted output.

Regression evidence: all 17 NXP charts now use real powers-of-ten with 102 absent;
PSMN013 spans 0.01..100 V with a 0.0026 V residual; the unrelated Infineon
IAUCN08S5L160T true-linear 0/20/40/60/80 V chart stays exact and upgrades safely
from grid fallback to direct position-text calibration. Related:
[[dsdig-sweep-green-axis-integrity-retro]], [[chart-review-checklist]].
