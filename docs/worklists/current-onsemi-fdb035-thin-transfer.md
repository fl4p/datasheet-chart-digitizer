# onsemi FDB035N10A thin transfer strokes

Status: V1 thin-path recovery mechanism independently GREEN but physical-axis
item RED; superseding V2 target and bounded source-grid snap mechanism are
independently AGENT-GREEN. Full transfer corpus and human review remain held.
`human_verified=false`; no commit or push.

## Recovery and retained blocker

FDB035N10A Figure 2 draws its three transfer curves with 0.624 pt strokes,
below the established 0.8 pt vector floor. A last-resort retry admits only
0.4--0.8 pt source objects after all normal paths fail. Every object needs at
least eight endpoint-contiguous edges; cross-object joins require matching
width, a 0.05 pt endpoint gap, and no backtrack. Full candidates must meet
source span and current-monotonicity gates, and their count and width cohort
must exactly equal the printed temperature count. Dotted or competing gray
paths remain refusals.

Packet: `/private/tmp/dsdig-onsemi-fdb035-thin-transfer-v1`.

- The mechanism recovers 437/441/435 source-seated points at -55/25/150 °C.
  Candidate/repeat CSV, overlay, and annotated PDF are deterministic; the PDF
  passes qpdf. IRF6644 and CSD19537Q3 frozen controls retain byte-identical CSV
  and overlays. Fifty focused transfer tests passed at freeze.
- Independent inspection found the physical axis is not yet reviewable:
  native text-box centers are 1.0--2.5 px right of the true X grid and
  2.5--2.9 px above the true Y grid. The fitted grid evaluates to VGS about
  0.012--0.015 V low and ID about 4% below printed values.
- Consequently the thin curve recovery is mechanism-GREEN, but the target
  physical item stays RED until transfer ticks are seated on source grid
  centers and a superseding packet is reviewed.

Independent review:
`/private/tmp/dsdig-onsemi-fdb035-thin-transfer-v1/reviews/codex-fdb035-thin-transfer-review.json`
(SHA-256
`a02fc6971d632ffb4c9c98df8a5c7ee2c4162cc079f23178388dcee7a71f6dc1`).

## Superseding V2 source-grid evidence

Packet: `/private/tmp/dsdig-onsemi-fdb035-thin-transfer-v2`.

- VGS ticks 2/3/4/5/6 V are seated at source grid centers
  131/252/373/494/615 px with a zero-pixel fitted residual. ID ticks
  300/100/10/1 A are seated at 33/118/295/473 px with 0.20932 px residual.
- Independent vector-space checks report maximum consumed-tick deviations of
  0.31 px (VGS) and 0.71 px (ID); physical values at exact source centers are
  within 2.54 mV and about 0.52%.
- The 437/441/435 pixel traces, -55/25/150 °C identities, crossings, and
  endpoints are unchanged. Endpoint-sequence binding prevents a nearer
  spurious full-span line from stealing an interior major; equal choices,
  duplicate bindings, and non-monotone sequences refuse.
- IRF6644 and CSD19537Q3 controls retain their pixel curves and identities
  while their physical axes receive the same expected grid-center correction.
  Eighty focused tests pass with ten skips and two subtests. Candidate/repeat
  PDF, CSV, and overlay are byte-identical; qpdf passes.

Independent V2 review:
`/private/tmp/dsdig-onsemi-fdb035-thin-transfer-v2/reviews/codex-fdb035-grid-v2-independent-review.json`
(SHA-256
`fcb49d4a2ed43e5707762b5a97bc6c5880ab6823a031bf3655619c968bba5c04`).
The review preserves the V1 RED and keeps the target, bounded shared mechanism,
full corpus, and human verdicts separate.
