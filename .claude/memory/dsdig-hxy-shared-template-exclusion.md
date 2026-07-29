---
name: dsdig HXY shared-template exclusion
description: Most HXY PDFs reuse exact chart bodies across unrelated parts, so their curves and Vpl are not trusted as independent part-specific measurements
metadata:
  node_type: memory
  type: project
---

The 2026-07-28 HXY collection audit hashed page-4 chart images for all 4,063
PDFs. Exact first-chart hashes repeat for 3,772 PDFs (92.84%); exact
multi-curve-body hashes repeat for 3,674 (90.43%). The largest groups contain
86 and 81 unrelated part numbers.

This proves that most, but not all, HXY chart bodies are copied rather than
independent part-specific measurements. It does not by itself prove that the
underlying data were invented. Dsdig therefore returns
`shared_curve_template_provenance_untrusted`, and the pwr-mosfet-lib integration
never serves HXY Vpl. Durable audit evidence is under
`out/supported-chart-fixes/hxy-audit/`.

Related: [[dsdig-identical-curve-bundles-need-source-proof]],
[[dsdig-data-bearing-chart-gate]].
