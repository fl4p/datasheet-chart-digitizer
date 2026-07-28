# Vishay below-frame unnumbered caption recovery

**Status:** bounded implementation, generated-copy-free 2,479-PDF finder A/B,
source-ownership audit, and sequential changed-PDF determinism are GREEN. The
final independent packet review is GREEN; human verification is not claimed.
`human_verified=false`.

## Defect

The Vishay layout prints unnumbered chart titles about 32 pt below each closed
vector frame. The existing frame-gated short-caption recovery only inspected a
0..24 pt band above frames. On the `SiR882BDP` medoid, the finder therefore saw
only synthetic gate-charge candidates and missed five supported page-3 charts
plus the page-4 source-drain diode chart.

The title lies between two chart rows: the preceding frame is about 32 pt above
the title and the next frame starts about 29 pt below it. Generic nearest-frame
selection therefore binds the wrong row. The x-axis label is nearer still,
about 16 pt below the frame, and must not be promoted as a title.

## Bounded fix

- Recover a supported short caption from either the existing 0..24 pt
  above-frame band or a distinct 24..52 pt below-frame band.
- Permit up to nine words only in the below-frame band, covering Vishay's
  `On-Resistance vs. Drain Current and Gate Voltage` caption.
- For frame-evidenced recovered titles, preserve the direction band during
  binding: above-frame captions own the following frame; below-frame captions
  own the preceding frame. Do not use the near-equal generic distance tie.
- Preserve the exact frame that evidenced a recovered caption on its
  `DiagramTitle`; binding must use that frame rather than search neighboring
  frames again. Select the below-frame page convention when a gate-axis fallback
  already exists or at least two supported titles independently own two
  distinct preceding frames. Multiple candidate lines owned by one frame do
  not establish a page convention. Once the repeated below-frame convention is
  established, do not mix in above-frame guesses: `SUD50N04-8m8P` proves that
  a missing true frame can otherwise cross-bind an above caption to an
  unrelated neighboring chart. Otherwise retain the legacy above-frame path
  rather than guessing from one ambiguous caption.
- Invoke frame-backed caption recovery when a page otherwise has only a gate
  axis-label fallback. Preserve that fallback if no caption is recovered.
- Reject explicit unit-bearing `Qg` axis labels from caption recovery. On pages
  that already have a gate-axis fallback, restrict the extra recovery to the
  new below-frame direction; this prevents a caption whose preceding frame was
  missed from stealing the following row's frame.

## Evidence

The canonical medoid `vishay/SiR882BDP.pdf` has SHA-256
`8bf8dbab78e2e49fe822fc4bfe305b51050a8ac5e31390868a259822c31b428e`.
The regenerated canonical layout index assigns it to a 57-member cluster with
fingerprint `08854b96d388f438c3f7` and mean medoid similarity 0.9281. The exact
57-member list has SHA-256
`3d30ab47c79c9610e7a590686627ed3e90e5f1dea46a9847f294b034e99d8b30`.

Same-host pushed-HEAD versus candidate finder A/B at 120 dpi is exact across
the cluster. Every member preserves its page-1 fallback, replaces only the
page-3 synthetic `Gate charge` row, and recovers the measured page-3 transfer,
capacitance, gate-charge, normalized-RDS-temperature, and RDS-current charts,
plus page-4 body diode. Fifty-six members also expose RDS-vs-VGS; one source
lacks that chart. All 398 candidate page-3/page-4 crops contain exactly one
source vector frame.

The medoid annotated PDF embeds the recovered gate-charge overlay with Vpl
2.789 V. Transfer, capacitance, and body-diode extraction errors and RDS binding
refusals remain separate extractor slices; this finder change does not claim
those charts are numerically GREEN.

Two pre-acceptance candidates were rejected after exposing adversarial Vishay
layouts. `SIR804DP-T1-GE3` promoted `Qg - Total Gate Charge (nC)` as a caption.
`Si4190ADY` then proved that rediscovering a nearby frame could cross-bind
otherwise valid recovered titles. Both are now regression controls: the former
is byte-identical to baseline and the latter's transfer, capacitance, gate, and
RDS-temperature titles retain their exact discovery frames.

A third rejected candidate tried to preserve an apparently mixed-direction
caption on `SUD50N04-8m8P` page 4. Source rendering proved that the true
top-right body-diode frame is absent from vector-frame evidence; the caption
was being cross-bound to the following threshold-voltage chart. The accepted
slice removes that false baseline panel and leaves recovery of the missing
frame to a separate frame-evidence slice.

## Collateral gate

The authoritative corpus is the regenerated Nexperia/ROHM/Vishay/Littelfuse
canonical index: 2,479 PDFs, 790 generated variants excluded, zero scan errors.
The ordered corpus-list SHA-256 is
`61bad59ca8c87fb4c97cdcddfb7841e1cda7de38ca487d0366a5ba59a3919ec3`.
Accept only after sequential pushed-HEAD and candidate public-CLI finder runs,
source review of every changed layout family, candidate/repeat determinism,
focused tests, `qpdf --check`, and independent agent review.

The authoritative A/B is 8,201 baseline panels versus 10,972 candidate panels:
2,771 net additions across exactly 483 Vishay PDFs. The other 1,996 PDFs are
byte-identical, with zero Nexperia, ROHM, or Littelfuse panel deltas. All 576
Vishay outputs are byte-identical to the separate precheck. The changed-list
SHA-256 is
`a1dcf87cc7db013f00c679cbdf2e3123f0e518e4f0bf0c51a18761aa98ccc5c2`;
the full summary SHA-256 is
`1bd9712cb66a9e08fe6af0f124f9953a7ceb84fc2bdc528c4c9b7f52b36d7e2a`.

The source audit reviewed all 3,335 exact new or moved panels. Each title is
24.78..50.29 pt below exactly one source frame; every output crop owns that
same single frame; no two panels claim one frame; and no unit-bearing `Qg`
axis label is promoted. The only baseline semantic removal without replacement
is the source-proven false SUD body panel above. The audit JSON SHA-256 is
`0bdedc215c439f51e9c3001267e9054b51e3b7f40728019fa0b6ffbc01fe38d9`.
The sole non-identical `scan_errors.json` is the pre-existing textless
`IXTK3N250L` refusal; only absolute source paths and traceback line numbers
changed.

All 483 changed PDFs were then run strictly one at a time through both baseline
and candidate public CLIs. Baseline JSON/CSV matched the frozen full baseline
483/483, candidate JSON/CSV matched the frozen full candidate 483/483, and all
483 retained the expected baseline-to-candidate delta. There were zero
mismatches. The sequential summary SHA-256 is
`ab72787be0a9de3aa713329d974fd60013bf5d666a3947606bc285adeb35edbe`.

The final independent artifact-first review is `AGENT-GREEN` with
`ready_to_merge=true`, no pending gates, and `human_verified=false`. Its
SHA-256 is
`dea7ea6883e98dfbebaff2bb08c66bf49f5e83e8d66c00df84a3471476567d85`.
The deterministic medoid annotated PDF SHA-256 is
`a972fac9e47c73b531812dea98cdc2c904612aca2ecf6c7e346fb7da794ad1e0`;
both candidate and repeat pass `qpdf --check`.
