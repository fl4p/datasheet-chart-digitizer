# TI CSD16342Q5A linear capacitance axis

Status: V1 independent review is RED; superseding V2 target and focused
mechanism are independently AGENT-GREEN. Full authoritative
capacitance-corpus A/B and human review remain held.
`human_verified=false`; no commit or push.

## Defect and contract

Figure 5-5 uses a genuinely arithmetic capacitance axis: 0.5, 1.0, 1.5,
and 2.0 nF. The established capacitance path only understood logarithmic pF
decades, so it correctly withheld physical values from otherwise source-seated
traces.

The position calibrator now has a fallback-only linear-Y path. It requires at
least four locally owned arithmetic labels, exactly one pF/nF unit, strict
value/pixel ordering, at most 5% step variation, and a fit residual below 3%
of one step. Existing log-decade calibration retains priority. Sparse,
irregular, duplicate, ambiguous, or unitless ladders remain untrusted.

## Frozen evidence

Packet: `/private/tmp/dsdig-ti-csd16342-linear-axis-v1`.

- The intended Y ticks are 500/1000/1500/2000 pF and X remains linear 0--25 V.
- All 1,641 source pixel rows are unchanged from baseline, while their physical
  columns become available. At 25 V, Ciss/Coss/Crss are approximately
  1077.95/536.53/45.48 pF.
- Candidate/repeat PDF, overlay, CSV, and finder output are deterministic and
  both PDFs pass qpdf.
- NTMFS011N15MC and NDB5060L log-Y controls retain byte-identical overlays and
  CSVs.
- The focused 80-test capacitance suite passes.
- This bounded packet does not replace the authoritative full capacitance
  corpus A/B required before landing.

Independent V1 review:
`/private/tmp/dsdig-ti-csd16342-linear-axis-v1/reviews/csd16342-linear-axis-independent-review.json`
(SHA-256
`62829af6ad4a08f6951c3ce7e2a289e9364dc447d579d5eb9b40ea46750415c1`).
The semantic nF-to-pF mapping and trace identities are GREEN, but all four
served Y tick centers are about 1.766 crop pixels below their source vector
gridlines, producing an approximately 11.1 pF systematic offset. V2 must seat
the fit on source grid centers and add explicit duplicate-ladder, log-priority,
and real-grid-center tests.

## Superseding V2 evidence

Packet: `/private/tmp/dsdig-ti-csd16342-linear-axis-v2`.

- The four selected source grid centers are 325.03662, 245.61567, 166.19480,
  and 86.77441 crop pixels. Label centers remain about 1.766 px away as
  provenance only; the served inverse fit lands on every selected grid center
  with maximum divergence 0.000196 px.
- All 1,641 source pixel rows are unchanged, and all 1,641 now have physical
  capacitance values. Ciss, Coss, and Crss remain source-seated with no branch
  swap or grid ride.
- Duplicate ladders refuse, log-decade calibration retains priority, and a
  label/grid divergence above the bounded gate refuses.
- Eighty-three focused tests pass. Candidate/repeat PDF, crop, overlay, debug
  image, embedded overlay, points, finder output, and canonical result are
  deterministic; both PDFs pass qpdf.
- NTMFS011N15MC and NDB5060L log-Y controls retain byte-identical points and
  overlays. Derived Qoss remains held and correctly withheld.

Independent V2 review:
`/private/tmp/dsdig-ti-csd16342-linear-axis-v2/reviews/csd16342-linear-axis-v2-independent-review.json`
(SHA-256
`d0171054ac6574a6a9e738c4d04963c9efb5bf14032ccd3a7d04960f741a7d33`).
The review preserves the V1 RED verdict and keeps the target, focused
mechanism, Qoss, full corpus, and human verdicts separate.
