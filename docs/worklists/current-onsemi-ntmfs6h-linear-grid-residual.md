# Onsemi NTMFS6H864NLT1G dense linear grid seating

Status: bounded target and mechanism are independently GREEN with scope holds.
Full authoritative shared-axis corpus A/B and human review remain held.
`human_verified=false`; no commit or push.

Supersession note: the V1 packet below remains the historical evidence for the
dense 2.0--2.25 px grid-sequence exception, but its first-tick centering verdict
is superseded by the projection-center correction in
[the IRF3205 V2 slice](current-infineon-irf3205-spaced-celsius.md). The fresh V2
NTMFS6H control consumes x=32/660 for source centers 32.922/660.353, keeps all
628 points on source ink, and reports norm(25 °C)=0.99731. Do not carry forward
the older x=31 overlay as current tick-center evidence.

## Defect and contract

Page 3 Figure 5 is a clean single-curve normalized RDS(on)-versus-temperature
chart. Its ten labels are `-50..175 °C` in 25-degree steps and its ten source
rails form a regular vector ladder. The rasterized left frame is three pixels
wide, however, and the grid detector substitutes its one-sided hint edge for
the projection-band center. Endpoint interpolation therefore places the
second rail 2.111 px away, narrowly exceeding the 2.0 px sequence gate even
though the refitted ten-rail axis has only 0.649 px RMS residual.

The authoritative grid snap retains its existing 2.0 px path. A linear-only
exception up to 2.25 px now requires at least five bound ticks and no tick more
than 1.5 px from the refitted snapped axis. Unique, monotone, one-to-one
binding and the existing 1.5 px RMS gate remain mandatory. Sparse ladders and
dense ladders with one displaced false rail still refuse; the logarithmic path
is unchanged.

## Frozen evidence

Packet: `/private/tmp/dsdig-onsemi-ntmfs6h864-grid-v1`.

- The owned X rails are 31/103/172/242/312/381/450/520/590/660 px. The
  recovered X residual is 0.648545 px and the Y residual is 0.469044 px.
- One 10 V PDF-vector trace yields 628 points over -49.78--175.06 °C and
  normalized RDS(on) 0.602--2.334. Its value at 25 °C is 0.99648.
- The overlay is source-seated across the full trace with correct axes, tick
  centers, units, frame, and no grid ride or extrapolation.
- Candidate/repeat annotated PDFs, finder output, RDS(Tj) JSON, overlay, and
  the unchanged transfer/RDS-current artifacts are byte-identical; both PDFs
  pass `qpdf --check`.
- The target transfer and RDS-current outputs are byte-identical to the
  pre-change sample. Prior-reviewed NTMFS011N15MC and CSD19537Q3 RDS(Tj)
  overlays and physical fields remain unchanged, as do the STP38N65M5
  body-diode JSON and overlay.
- The shared-consumer suite passes: 127 tests, 25 subtests, and 10 optional
  corpus skips. It includes coherent thick-frame, sparse false-rail, dense
  single-displacement, ambiguity, duplicate, and bad-log cases.

The unrelated Figure 10 body-diode tick-reading error remains outside this
slice. This bounded packet does not replace the authoritative full A/B across
body-diode, transfer, RDS-current, and RDS-temperature consumers.

## Independent review

Review:
`/private/tmp/dsdig-onsemi-ntmfs6h864-grid-v1/reviews/codex-ntmfs6h-grid-independent-review.json`
(SHA-256
`f883961e7d015918e22770dc8046f5ff71724b0373c676346eae66bd6d905b0a`).
Verdict: `AGENT-GREEN_WITH_SCOPE_HOLDS` for the target item, bounded mechanism,
same-target and prior-reviewed controls, determinism, PDF validity, and the
shared-consumer suite. The reviewer independently confirmed all 628 trace
pixels sit on source ink and directly probed the >2.25 px, sparse, displaced,
ambiguous, duplicate, nonmonotone, and logarithmic refusal branches.

The packet recorded the earlier `tests/test_rdson_temperature.py` SHA, while
that shared dirty file changed after freezing. The reviewer reran the same
suite with the current file and reproduced the pass count; the implementation
and focused grid-test hashes still match the packet. The authoritative
full-corpus A/B and human verdict remain the landing gates.

Superseding centering review:
`/private/tmp/dsdig-infineon-irf3205-spaced-celsius-v2/reviews/codex-irf3205-centering-v2-independent-review.json`
(SHA-256
`8252d2d4458f83adffb1c60323464fa1ed86032a047f2366fa84ea5942814958`).
It leaves this dense-grid exception GREEN while replacing only the stale V1
frame-center consumption evidence.
