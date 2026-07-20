# STP38N65M5 split-half body-diode curves

Status: target item and bounded pairing mechanism independently AGENT-GREEN.
The authoritative full body-diode corpus A/B and human review remain held.
`human_verified=false`; no commit or push.

## Defect and contract

STP38N65M5 Figure 13 draws each of its three body-diode curves as two vector
path halves. The former last-group-only greedy join cross-paired the middle
25 °C halves with neighboring branches, collapsed three curves to two, and
correctly refused the panel because the curve count no longer matched the
three temperature labels.

The recovery replaces that order-sensitive join with mutually unambiguous
global endpoint pairing. Candidate halves must have matching style and width,
a tightly bounded endpoint gap and backtrack, and a separation margin from
the next pairing. Count or pairing ambiguity remains a refusal. A separately
gated boundary-exit rule serves only source-backed points when a monotone curve
leaves the labeled plot range; it does not extrapolate along the frame.

## Frozen evidence

Packet: `/private/tmp/dsdig-stp38-body-diode-split-halves-v1`.

- Three curves recover with 208/374/374 points at -50/25/150 °C and preserve
  `VSD(-50) > VSD(25) > VSD(150)` throughout their common current range.
- The -50 °C trace stops at the source exit at 1.200 V and 5.632 A. Source
  ink continues above the labeled 1.2 V range, but no value is served there;
  the trace neither rides the frame nor extrapolates.
- The 25 and 150 °C traces remain source-seated through 10 A. The panel box,
  tick centers, axis orientation, and units are correct.
- Candidate and repeat body-diode JSON, source crop, and overlay are
  byte-identical. All 29 focused tests pass.
- The focused packet does not establish the authoritative full-corpus A/B.

Independent review:
`/private/tmp/dsdig-stp38-body-diode-split-halves-v1/reviews/stp38-body-diode-independent-review.json`
(SHA-256
`3f0ada6f385a45ddeed0584b3c8e2a8d0f568d526ada28fe4bb539c77b1199b3`).
The reviewer kept the item and patch-mechanism verdicts separate and did not
carry forward any human verdict.
