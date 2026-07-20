# STP45N60DM2AG thin RDS(Tj) trace and diagnostics

Status: thin-trace recovery and bounded structured-diagnostic mechanism are
independently AGENT-GREEN. The target physical item remains REFUSED_NOT_SERVED
on separate temperature-axis identity evidence. Full authoritative RDS corpora
A/B and human review remain held. `human_verified=false`; no commit or push.

## Defect and scope

Diagram 9 draws one normalized RDS(on)-versus-temperature curve at 0.510236 pt.
The shared 0.8 pt vector floor produced no trace, after which every
`CurveBindingError` was mislabeled `legend_curve_binding_ambiguous` even though
the actual cause was missing vector evidence.

Only the temperature family now opts into a 0.4 pt floor; `rds_on_current`
retains its 0.8 pt default. Curve-binding failures carry a required structured
reason separate from free text: no full-span curve, missing local VGS label,
true legend ambiguity, and legend/trace style mismatch remain distinct.

## Frozen evidence

Packet: `/private/tmp/dsdig-stp45-rdst-thin-diagnostics-v1`.

- The 0.8 pt path finds zero edges. The 0.4 pt path finds exactly four edges
  from one black source drawing, one component spanning 99.8% by 95.4%, and a
  326-point VGS=10 V trace. All 326 points have source ink within two pixels;
  the trace is monotone and normalized RDS at 25 °C is 0.9930385.
- The item stays refused solely on `temperature_axis_identity_unverified`:
  its crop truncates the owned temperature-unit evidence. Trace recovery is
  not physical-item approval.
- FDD390N15ALZ and IPT020N13NM6 current controls retain the 0.8 pt floor and
  now report their actual causes, `no_full_span_vector_curve` and
  `legend_vgs_label_missing`. CSD19537Q3 remains an OK two-curve temperature
  control and is deterministic within the packet; the packet does not contain
  a historical pre-change baseline, so historical byte identity is not
  independently claimed.
- Candidate/repeat PDF, target JSON/overlay, and CSD control JSON/overlay are
  byte-identical; qpdf passes. Thirty focused tests and 23 subtests pass.

Independent review:
`/private/tmp/dsdig-stp45-rdst-thin-diagnostics-v1/reviews/codex-stp45-thin-diagnostic-independent-review.json`
(SHA-256
`a7e60dd52ef6f8a64e6596f5703d6f8b503b420c206af6cfb2a775fb8f3b2d89`).
