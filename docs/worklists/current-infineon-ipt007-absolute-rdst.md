# Infineon IPT007N06N absolute RDS(on)-temperature curves

Status: page-8 Diagram 9 item, bounded absolute-milliohm typ/max mechanism,
determinism, three byte-stable normalized controls, focused/shared RDS tests,
and annotate embedding are independently AGENT-GREEN. Full authoritative
RDS(Tj) corpus A/B, absolute-ohm/general curve families, and human review
remain held. `human_verified=false`; no commit or push.

## Defect and bounded support

IPT007N06N publishes absolute RDS(on) in mΩ versus junction temperature. The
normalized-only path collapsed two same-style source curves to one, called its
absolute 0.748 mΩ value “normalized,” and refused it for not equaling unity at
25 °C.

The new branch activates only for an owned panel containing an mΩ axis token.
It preserves same-style components only on that branch and requires exactly
two full-span traces, standalone `typ` and `max` source tokens, exactly one
local VGS value, and exactly one local ID value. `max` must remain visibly
above `typ` across their complete common X span by at least
`max(2 px, 0.5% plot height)`. Missing/extra traces, missing labels,
ambiguous conditions, crossing, or insufficient separation still refuse.

## Frozen evidence

Packet: `/private/tmp/dsdig-ipt007-absolute-rds-tj-v1`.

- Same-host causal baseline reproduces the one-curve normalized-unity refusal.
- Candidate serves `temperature_c,rdson_mohm`, labels the axis kind
  `absolute_rds_on`, and never fabricates a normalized value.
- Both source traces contain 634 points over -54.91 to 175.27 °C. At 25 °C,
  typ is 0.65870 mΩ and max is 0.74783 mΩ. Both are monotone and source-seated;
  max stays 33--79 px above typ with no crossing, grid ride, or extension.
- The consumed X ticks are -60..180 °C and Y ticks are 0.0..1.6 mΩ, with
  sub-pixel residuals. Local conditions are VGS=10 V and ID=150 A.
- Candidate/repeat plugin JSON, overlay, annotated PDF, and embedded overlay
  are identical. Raw annotate manifests differ only in their absolute output
  paths; normalized physical content is identical. Annotate embeds the target
  as accepted; its exit 1 is solely the separate p8d12 body-diode error.
- IRF3205, NTMFS6H864NLT1G, and FDB047N10 normalized results and overlays are
  byte-identical baseline/candidate/repeat and retain unity-at-25 °C checks.
- Focused tests pass 34 plus 21 subtests; the shared RDS suites pass 46 plus 31
  subtests. A broader annotate run had one stale finder-count expectation
  (7 versus current 8 panels) in unrelated STL260N4F7 coverage.

## Independent review

Review:
`/private/tmp/dsdig-ipt007-absolute-rds-tj-v1/reviews/independent-review.json`
(SHA-256
`56dc668b29d3f9d1126de906802895a17deaeaa69f48bddd4689d28ece78ac32`).
Verdict: `AGENT_GREEN_SCOPED_FULL_CORPUS_HELD`. The reviewer independently
reran both test gates, exercised the count/label/condition/crossing failure
cases, verified every served point against source ink, and retained the
corpus, dirty-tree, positional-axis-token, broader-family, and human holds.
