# Compact formula caption ownership

Status: finder patch and full-corpus V3 packet independently AGENT-GREEN.
`human_verified=false`; no commit or push.

## Defect

Several legacy Toshiba, MCC, and GoFord families identify charts with compact
formula captions instead of prose titles, for example `ID-VGS`, `RDS(on)-ID`,
`RDS(on)-Ta`, `IDR-VDS`, and normalized `V(BR)DSS-Ta`. The finder either treated
these as unsupported generic charts or attached them to an adjacent plot row.
The failure had two ownership layouts: Toshiba composite numbers (`Fig. 8.10`)
caption the preceding plot, while MCC/GoFord simple numbers (`Fig. 10`) caption
the following plot. Fused next-caption tokens could also merge two rows.

## Bounded contract

- Classify only exact compact formula families; `ID-VDS`, `VDS-VGS`, and
  `Vth-Ta` remain unsupported.
- Route `RDS(on)-ID/IDs` to current and `RDS(on)-Ta/Tj/Tc` to temperature.
  `RDS(on)-VGS` remains unsupported.
- Composite `Fig. N.M` compact captions own the directly preceding plot.
- Simple `Fig. N` captions may own the following plot only when its geometry is
  materially nearer; otherwise preserve the evidenced preceding layout.
- Confine synthetic compact-caption crops to one column and split a grid at a
  fused next `Fig...` token.
- Finder recovery does not imply extraction success. Raster/outlined axes that
  cannot be calibrated remain explicit per-panel refusals.

## Frozen V3 evidence

Technical packet:
`/private/tmp/dsdig-compact-formula-finder-v3/machine.json`
(SHA-256 `c24423b45062a3d5dc780f83be253c21693f0b8e7f35757fbc40f486716593e6`).

- Corpus: 14,359 PDFs; 5,045 prefilter candidates; 140 affected PDFs.
- Errors: zero prefilter, discovery, and affected A/B errors.
- Candidate/repeat: equal for all affected PDFs.
- Delta: 231 additions, zero removals: 127 RDS(on), 92 body-diode, and
  12 transfer panels.
- All 251 unchanged shared panels are byte-identical. The sole changed shared
  panel, MCC `SI2302A-TP` page 3 Diagram 4, improves by trimming the following
  chart's caption from its crop.
- The independent review visually checked all 231 additions and all 11 V2
  blocker cases; it found zero direction violations.

Provenance is additive and immutable:

- `/private/tmp/dsdig-compact-formula-finder-v3/provenance.json`
  (SHA-256 `4275d6afba0834bd4d7a9fce629aeeff638595f80573e4fa772b9b47467b04bd`)
  binds the exact A/B command, harness, source, runtime, dependencies, and all
  14,359 PDFs.
- `/private/tmp/dsdig-compact-formula-finder-v3/input-provenance-supplement.json`
  (SHA-256 `24cbed8fcddfd0a37ec15176c84dc69448ebabeb6b2038c7378abd3f7ee32361`)
  binds all 7,125 consumed text sidecars and the exact `pdftotext`/`pdftoppm`
  binaries and versions.

The original V3 review correctly stayed RED while these provenance inputs were
missing:
`/private/tmp/dsdig-compact-formula-finder-v3/reviews/compact-formula-finder-independent-review.json`
(SHA-256 `fa36d86b2724b8fbdff5f46a260b218a8ce1bb2a548dbd3681ed380355ec384d`).
The separate superseding provenance-closure review is AGENT-GREEN:
`/private/tmp/dsdig-compact-formula-finder-v3/reviews/compact-formula-finder-provenance-closure-review.json`
(SHA-256 `bdb4236f0f46248a5dc4cc59226c49474b2d2c1438b172143b637c47191558e9`).
Both reviews have `human_verified=false`.

## Rejected predecessor packets

V1 and V2 are retained as negative evidence and must not be promoted:

- V1 removed neither the MCC/GoFord neighbor misownership nor wide Toshiba
  crops. Review SHA-256:
  `72d5a5f3716aae92111c52d16c6ca16e456943e1de5c2d7d07960a5393856aa6`.
- V2 still merged eight MCC body-diode panels with the following chart and
  bound three Toshiba body-diode captions to switching-time plots. Review
  SHA-256:
  `d46df7458e684c7ab66f062ea77379a5bfc54bada91fcf9f01de7562455b4687`.

## Separate extractor gaps

On Toshiba `XPW4R10ANB`, the five page-7 charts are now detected and correctly
owned, but their raster/outlined tick labels still fail the numeric-axis and
temperature-label readers. Those honest refusals are a separate extractor/OCR
slice; this finder verdict does not upgrade them.
