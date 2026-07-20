# TI CSD86330Q3D subunit log capacitance axes

Status: bounded target, mechanism, and prior-reviewed controls independently
AGENT-GREEN. Full authoritative capacitance-corpus A/B, Qoss, and human review
remain held. `human_verified=false`; no commit or push.

## Defect and contract

The Control and Sync MOSFET capacitance charts on page 8 use logarithmic nF
axes. Diagram 16 labels `10, 1, .1, .01, .001 nF`; Diagram 17 labels
`10, 1, .1, .01 nF`. The established plain-decade position path deliberately
accepted only raw labels at or above one, so it retained only `10` and `1`.
The endpoint-coverage guard then correctly withheld physical values from all
3,426 otherwise source-seated trace rows.

The position calibrator now has a fallback-only subunit-log path. It requires
exactly one locally owned pF/nF unit, at least one label below that unit, at
least three unique positive labels, exact powers of ten after conversion to
pF, consecutive exponents in top-to-bottom position order, and a position-fit
residual below 0.03 decade. Free-text order and inline formula annotations do
not participate. Missing, non-power, non-consecutive, duplicate, unitless, or
position-inconsistent ladders remain untrusted. The existing K-suffix log and
arithmetic-nF paths retain priority and behavior.

## Frozen evidence

Packet: `/private/tmp/dsdig-ti-csd86330-subunit-log-v1`.

- Diagram 16 recovers 0--25 V and 10,000--1 pF; Diagram 17 recovers 0--25 V
  and 10,000--10 pF. Y-fit residuals are 0.001743 and 0.001200 decade.
- Both items are `ok`, physical-output available, and trace-validation pass.
  Ciss/Coss/Crss each contribute 571 points per chart.
- All 3,426 source pixel rows are byte-equal to the baseline pixel columns;
  only the previously blank voltage/capacitance columns become populated.
- At 25 V, Diagram 16 Ciss/Coss/Crss are approximately
  721.80/284.11/9.59 pF; Diagram 17 values are
  1277.14/553.74/19.57 pF. The overlays remain source-seated with no branch
  swap or grid ride.
- Candidate/repeat annotated PDFs, overlays, points CSVs, and physical JSON are
  deterministic, and all target/control PDFs pass `qpdf --check`.
- The prior-reviewed TI CSD16342Q5A arithmetic-nF and onsemi NTMFS011N15MC
  K-suffix log controls retain byte-identical overlays and CSVs.
- The focused capacitance suite passes: 86 tests and 8 subtests.

This bounded packet does not replace the authoritative full capacitance-corpus
A/B required before landing.

Independent review:
`/private/tmp/dsdig-ti-csd86330-subunit-log-v1/reviews/codex-csd86330-subunit-log-independent-review.json`
(SHA-256
`f4fb29e09f9d279b244b8b8aeb208671305ebca7b6a81c60f4b6cbaaecb5e7a4`).
The review keeps both target items, the positioned-ladder mechanism, bounded
controls, Qoss, the full-corpus gate, and the human verdict separate.
