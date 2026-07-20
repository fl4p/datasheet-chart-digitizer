# STW46NF30 transposed body-diode Figure 13

Status: target item, bounded finder/axis mechanism, determinism, and three
controls are independently AGENT-GREEN with scope holds. Full authoritative
finder/body-diode corpus A/B and human review remain held.
`human_verified=false`; no commit or push.

## Defect and contract

STW46NF30 page 7 Figure 13 puts current `ISD (A)` on X and voltage `VSD (V)`
on Y. The finder assumed a voltage-X diode layout, could not establish the
caption-leading direction, and emitted no body-diode panel. After a bounded
crop was injected, the diode Y selector admitted the below-frame X-origin `0`
into the left label gutter and rejected the otherwise coherent 0.3--1.0 V
ladder.

Current-X direction and crop ownership are now atomic. `ISD`/`IS`/`IF`, an
ampere unit, at least three aligned numeric ticks, and a tick span of at least
60 points are all required. Current tokens remain outside the generic caption
direction set, so a condition such as `IF = 10 A` cannot reverse a caption or
emit a synthetic crop. The crop is bounded to the caption's own chart column.

The axis selector keeps its established path first. Only when normal Y
selection refuses does it retry after excluding numeric label centers below
the evidenced frame at `hint.y1 + 2`; the X-origin can no longer corrupt the Y
ladder. Failure or ambiguity remains a refusal.

## Frozen evidence

Packet: `/private/tmp/dsdig-stw46nf30-transposed-body-diode-v1`.

- Baseline finds no Figure-13 body-diode panel. Candidate owns page 7 diagram
  13 at `[41.108, 116.974, 297.041, 292.940]` without Figure-14 bleed.
- The consumed axes are current 0--30 A and VSD 0.3--1.0 V. Grid-fit residuals
  are 0.247/0.259 px.
- Three -50/25/175 °C traces retain their physical ordering. All 250 source
  pixels per trace sit on source ink; 247 calibrated points per trace are
  served through 29.898 A.
- Each served trace stops at the labeled 30 A boundary. The printed black
  continuation in the next unlabeled interval remains withheld; no endpoint
  extrapolation or grid/frame ride occurs.
- Candidate/repeat crop, result, and overlay are byte-identical. ST8L65N044M9
  transposed, IPP024N08NF2S voltage-X, and ISC024N08NM7 typ/max controls retain
  byte-identical results and overlays under a same-host causal A/B.
- The focused regression run passes 111 tests and 13 subtests, with 11 optional
  corpus skips. One unrelated dirty-tree finder assertion was explicitly
  deselected because it currently sees an extra page-6 gate-charge panel.

## Independent review

Review:
`/private/tmp/dsdig-stw46nf30-transposed-body-diode-v1/reviews/stw46-transposed-body-diode-independent-review.json`
(SHA-256
`7139dfce555c1aace9db1867ba8608ffaeb1ad25930b0e80c5bb557523672961`).
Verdict: `AGENT_GREEN_SCOPED_FULL_CORPUS_HELD`. The reviewer independently
verified target fidelity, atomic false-bind prevention, axis retry, exact
determinism, all three bounded controls, and the focused tests. Full corpus and
human review remain held.

The preceding read-only contract is preserved at
`/private/tmp/codex-stw46nf30-audit/reviews/stw46-independent-contract.json`
(SHA-256
`4e07250c189313a19507666f0463cbffd23eda53924d384e10b43b0ef16f4dcb`).
