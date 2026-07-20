# Infineon IRF6644 two-temperature log transfer

Status: V3 frozen and independently AGENT-GREEN. Full authoritative
transfer-corpus A/B and human review remain held. `human_verified=false`; no
commit or push.

## Defect and contract

IRF6644 Figure 5 is a two-temperature `ID=f(VGS)` chart with a logarithmic
1/10/100/1000 A current axis. The two-curve path previously called the
breakdown-voltage linear axis fitter, producing a misleading `V(BR)DSS`
diagnostic and rejecting the valid log scale. A first recovery then exposed a
second defect: heuristic curve ordering swapped the printed 25 °C and 150 °C
identities.

All transfer panels now use the transfer-specific auto linear/log fitter.
Exactly two curves require exactly two source-positioned temperature labels,
bounded per-label distance, and unambiguous individual and joint assignment
margins. There is no physical-order fallback for a two-curve chart. The strict
label grammar also accepts TI's private-use degree glyph plus a shared exact
`, VDS=... V` suffix; the condition is excluded from label geometry, and mixed
conditions refuse.

## Frozen evidence

Packet: `/private/tmp/dsdig-infineon-irf6644-log-transfer-v3`.

- Figure 5 recovers 292 points at 25 °C and 362 at 150 °C with eight linear
  VGS ticks and four log ID ticks; residuals are 0.00189 px and 0.05021 px.
- At 10 A, VGS is 5.02452 V at 25 °C and 4.38115 V at 150 °C. At 100 A it is
  6.29602 V and 6.03619 V. The hotter printed branch is therefore correctly
  the lower-VGS branch rather than the swapped V1 assignment.
- Candidate/repeat annotated PDF, CSV, overlay, and finder output are
  byte-identical; both PDFs pass qpdf.
- All 47 transfer tests pass.
- On 23 frozen exact crops across Infineon/onsemi/TI/ST, all eight previously
  served controls retain byte-identical CSV and overlay output. All 15 prior
  refusals remain refusals; the three-label/two-path FDBL panel and the distant
  2N7002L legend remain explicitly fail-closed.
- This bounded packet is not a substitute for the authoritative full transfer
  corpus A/B. That landing gate remains open.

Independent review:
`/private/tmp/dsdig-infineon-irf6644-log-transfer-v3/reviews/irf6644-log-transfer-v3-independent-review.json`
(SHA-256
`6b4ea8248795c21beaefd7cfe664c2c4bcf2ad7956c13d2423f179ae96d1f78b`).
The reviewer also corrected the source layout description: Figure 5 is the
bottom-left panel on page 6; its owned crop correctly excludes Figure 6.
