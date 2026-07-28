---
name: dsdig-full-corpus-authoritative-harness
description: Shared data-dependent extractor changes require a full-corpus rerun with the production selection contract; bounded samples or substitute selectors are invalid regression evidence
metadata:
  node_type: memory
  type: project
---

For dsdig shared extractor changes, acceptance requires re-running the complete
affected corpus with the same candidate-selection contract used by the public
CLI and the stored comparison baseline. A small fixture packet proves the named
fixes, but it does not bound collateral behavior in data-dependent panel or
frame detection.

Do not replace the production selector with a convenient sort. Gate-charge
collateral once used `sorted(results, key=_result_sort_key)[0]`, while the
authoritative harness and CLI select the first result with non-null `vpl` and
only then choose a review candidate when no value exists. The substitute method
created false deltas on fail-closed PDFs and invalidated a 304-part comparison.

For every accepted corpus rerun, freeze the corpus-list hash, source hashes,
exact command, output-manifest hash, row/error counts, and full delta list. Any
`plot_right` or `plot_bottom` change requires a source-level own-frame versus
neighbor-panel check even when the primary scalar is unchanged. Missing or
non-comparable evidence keeps the collateral gate blocked.

Run A and B sequentially when OCR/render subprocesses have timeouts or compete
for CPU/memory, unless resource budgets are explicitly isolated and
repeatability is demonstrated. Concurrent Tesseract corpus runs once produced
different review candidates through load-sensitive timeout behavior; that is
environment drift, not causal patch evidence.

Keep production identity semantics fixed as well. The capacitance finder names
`.gs`/`.cups` render variants with the base part and therefore reuses the base
`<part>.pdf.nop.csv` anchor table. An A/B harness must not rewrite that chart
part or silently substitute the transformed OCR table. Disambiguate native and
variant rows externally with exact source-PDF path/hash plus crop path/content
hash, page/diagram, bbox, and render settings; a pathname alone is not content
provenance. Record the canonical crop-set hash, base-part collisions, and both
table hashes, and give any table-binding change its own causal A/B.

Related: [[dsdig-gate-charge-panel-local-calibration]],
[[chart-review-checklist]], [[dsdig-sweep-green-axis-integrity-retro]].
