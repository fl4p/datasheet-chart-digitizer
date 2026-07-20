# Infineon IRF3205 spaced Celsius temperature axis

Status: V1 item RED is superseded by an independently scoped-GREEN V2 target,
bounded mechanism, and controls. Full authoritative shared-consumer corpus A/B
and human review remain held. `human_verified=false`; no commit or push.

## Defect and contract

The IRF3205 normalized RDS(on)-versus-temperature chart prints the spaced axis
label `T J , Junction Temperature ( C)`. Its calibrated curve was recoverable,
but the shared grid snap consumed the outer edge of a thick left frame rather
than the frame's projection center. The first `-60 °C` tick was therefore about
2.87 crop pixels away from the source-vector rail center.

The shared grid detector now inspects a three-pixel halo around the raster hint
so a thick rail just outside that hint is not clipped. When a projection band is
present, its measured center remains authoritative; the hint edge is added only
when the projection missed the rail. Unique ownership, monotonicity, calibration
residuals, and all existing fail-closed consumers remain unchanged.

## Frozen evidence

Superseding packet: `/private/tmp/dsdig-infineon-irf3205-spaced-celsius-v2`.

- IRF3205 moves only the first consumed rail from x=31 to x=33; the source
  vector center is x=33.866652 and every other X tick remains within 0.933 px.
- All 497 trace pixels sit on source ink. The single VGS=10 V curve remains
  source-seated with normalized RDS(on) at 25 °C of 0.99669.
- The fresh NTMFS6H control consumes x=32/660 for source centers
  32.922/660.353; all 628 points sit on source ink and norm(25 °C)=0.99731.
- FDB035 points and overlay are byte-identical to the prior reviewed packet;
  STP45 remains honestly refused on its separately owned axis-identity gate.
- Candidate/repeat PDF, result, crop, and overlay are deterministic and both
  PDFs pass qpdf. The shared-consumer suite passes 211 tests, 25 subtests, with
  10 optional corpus skips.

V1 is preserved as the causal RED packet. This V2 does not replace the
authoritative full A/B across body-diode, transfer, RDS-current,
RDS-temperature, and breakdown consumers.

## Independent review

Review:
`/private/tmp/dsdig-infineon-irf3205-spaced-celsius-v2/reviews/codex-irf3205-centering-v2-independent-review.json`
(SHA-256
`8252d2d4458f83adffb1c60323464fa1ed86032a047f2366fa84ea5942814958`).
Verdict: `AGENT-GREEN_SCOPED_WITH_FULL_CORPUS_HELD`. The reviewer independently
verified target/control tick centers, exact source-ink seating, physical values,
determinism, and the projection-center mechanism. Full corpus and human verdict
remain held.
