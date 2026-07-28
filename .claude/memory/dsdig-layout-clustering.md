---
name: dsdig layout clustering
description: Canonical datasheets are indexed by page/document structure before vendor-series sampling; generated PDF transforms are excluded.
metadata:
  type: workflow
---

Use `datasheet-layout-cluster` in `datasheet-chart-digitizer` to group the local
datasheet library before choosing regression samples. The index is hierarchical:
page roles (`chart`, `table`, `mixed`) and text strata first, then whole-document
profiles. Similarity uses structural geometry and caption families, not vendor
or part-number text. A cluster label is sampling metadata only and must never be
runtime detector authority.

Files named `*.pdf.<transform>.pdf` are never clustering inputs. They are stored
in `generated-pdf-variants.json`, including nested transform chains, and linked
back to the first canonical `*.pdf` path.

The first `nxp,rohm,vishay,littelfuse` run on 2026-07-20 indexed 2,471 canonical
PDFs, excluded 698 generated variants, and had zero scan errors. Frozen output:
`/private/tmp/dsdig-layout-clusters-v2`.
