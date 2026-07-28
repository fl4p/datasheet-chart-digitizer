---
name: dsdig-identical-curve-bundles-need-source-proof
description: Byte-identical digitized curves across distinct parts are not proof of wrong-part staging when the source PDFs publish identical curve geometry.
metadata:
  type: fact
---

For dsdig corpus audits, do not classify byte-identical curve CSV bundles across
distinct parts as wrong-part staging from content hashes alone. Compare the
source plot's curve geometry separately from labels and annotations.

2026-07-17 AO reverse-recovery example: AOB414 and AOD4126 fig17/fig19 have
different raster plot-interior hashes because temperature and quantity labels
move, but their four source curve centerlines are exactly identical after
normalizing to the common plot box. `_fill_outline_centerlines` produced exact
fig17 SHA `e7951fcb...` (lengths 59/57/55/60) and fig19 SHA `468440e9...`
(lengths 10/37/25/31) for both PDFs. Re-running AOD4126 alone reproduced the
backlog CSV hashes. Treat these as shared source evidence, not extraction
corruption; downstream fits may deduplicate identical evidence to avoid
overweighting it, but must not invalidate human verification without a source
geometry mismatch.
