# TI 2N7002L dense non-decade capacitance axis

Status: target item, bounded dense-log axis mechanism, source-path selection,
determinism, and five controls are independently AGENT-GREEN with scope holds.
Full capacitance-corpus A/B, Qoss, and human review remain held.
`human_verified=false`; no commit or push.

## Defect and contract

Page 7 Figure 5-8 uses a log-log C(V) chart whose Y labels are the dense ladder
2--10 pF rather than exact powers of ten. The established decade parser could
not represent it, so physical values were withheld. All three source curves
also intentionally end near 6 V inside a frame that continues to 10 V, and the
red Ciss and cyan Coss strokes genuinely cross near 4.72 V.

The fallback position fit requires exactly one locally owned pF/nF unit, at
least four unique positive labels, at least a factor-five span, strict
value/pixel order, <=1 px log residual, <=0.02 decade value residual, and a log
fit at least four times better than linear. Missing/ambiguous units, noisy or
arithmetic ladders, and weak log evidence remain untrusted.

Vector recovery is similarly bounded: exactly three source color groups,
exactly one coherent candidate per group, and each source path must span at
least 80% of the plot width. It preserves the printed 0.25--6 V endpoints and
does not extrapolate to the 10 V frame.

## Frozen evidence

Packet: `/private/tmp/dsdig-ti-2n7002-dense-log-v2`.

- X is log 0.2--10 V from fourteen consumed ticks; Y is log 2--10 pF from nine
  consumed ticks. Maximum fits to source grid centers are 0.1341/0.4708 px.
- Ciss/Coss/Crss each contribute 496 rows. All 1,488 rows are within one pixel
  of their printed source strokes; 1,483 are exact source-mask hits.
- The microscopic crossing preserves red Ciss and cyan Coss before, through,
  and after the intersection with no hop, swap, grid ride, or interpolation.
- All traces start near 0.25 V and stop near 5.985 V on true source endpoints;
  no point is fabricated across 6--10 V.
- Candidate/repeat PDF, CSV, overlay, and debug overlay are deterministic and
  both PDFs pass qpdf. The focused suite passes 127 tests and 15 subtests.
- CSD16342Q5A, both CSD86330Q3D cap panels, NTMFS011N15MC, and NDB5060L retain
  byte-identical points and overlays from their prior independent reviews.

The review records one generalization limit: after three source color groups
are proven, the shared assignment still uses right-edge order rather than
binding RGB directly to the printed legend. This item is source-proven, but the
verdict does not certify a different end-order topology. Qoss remains withheld.

## Independent review

Review:
`/private/tmp/dsdig-ti-2n7002-dense-log-v2/reviews/codex-ti-2n7002-dense-log-independent-review.json`
(SHA-256
`0d41ce2fb94b5470a828f771560b3ee30ef10a052ce109905444d3da4870d46b`).
Verdict: `AGENT-GREEN_WITH_SCOPE_HOLDS`. The reviewer independently checked all
axes, every trace identity and endpoint, the mandatory crossing microscope,
all source seating, determinism, PDF integrity, fail-closed gates, and five
controls. Full corpus, Qoss, unrelated panels, and human verification remain
held.
