# Datasheet layout clustering

Status: implemented; focused tests and four-vendor corpus characterization green.

## Objective

Group canonical datasource PDFs by reusable page and document layout before
sampling. Multiple device series, and even multiple vendors, may share a
structural layout. Sampling one medoid plus structurally distinct outliers from
each cluster should provide better detector coverage than choosing parts only
by vendor or series name.

## Contract

- Cluster page layouts first, separating `chart`, `table`, `mixed`, and `other`
  roles and native-text, raster, hybrid, sparse, and vector-outline strata.
- Cluster whole documents from their ordered page-role and page-layout profile.
- Exclude generated copies named `*.pdf.<transform>.pdf` from both levels.
  Retain them in a separate variant index linked to the canonical PDF.
- Use normalized structural evidence: occupancy, plot frames, ruling lines,
  images, chart-family captions, and coarse page geometry.
- Exclude vendor and part-number language from similarity.
- Emit deterministic clusters, medoids, membership, and similarity scores.
- Use the result only for corpus sampling and regression coverage. Runtime
  chart detection must never depend on a cluster label.

## Outputs

`datasheet-layout-cluster ROOT --out OUT` writes:

- `layout-clusters.json`: schema, summary, page clusters, document clusters.
- `layout-pages.jsonl` / `.csv`: page signatures and assignments.
- `layout-documents.jsonl` / `.csv`: document signatures and assignments.
- `layout-page-clusters.csv` and `layout-document-clusters.csv`: review tables.
- `generated-pdf-variants.json`: excluded derived copies and canonical links.
- `scan-errors.json`: per-PDF failures without aborting the corpus scan.

## Acceptance

- Focused synthetic tests prove generated-copy exclusion, cross-vendor layout
  grouping, hard role/text-mode boundaries, and chart-page frame detection.
- Run Nexperia (`nxp`), ROHM, Vishay, and Littelfuse/IXYS as the first real
  corpus pass and inspect medoids plus cross-vendor clusters.
- Tune the fuzzy threshold only from frozen outputs; never tune against desired
  vendor labels, because vendor identity is not the target.

## First corpus result (2026-07-20)

The deterministic `nxp,rohm,vishay,littelfuse` pass scanned 2,471 canonical
PDFs without error and excluded 698 generated variants. It assigned 17,585
chart/table/mixed pages to 3,357 page-layout clusters and the canonical PDFs to
1,080 document-layout clusters. Two complete runs produced byte-identical
cluster, page-assignment, and document-assignment files. All 11 ROHM PDFs
formed one document-layout cluster despite spanning multiple part series.

Frozen output: `/private/tmp/dsdig-layout-clusters-v2`.
