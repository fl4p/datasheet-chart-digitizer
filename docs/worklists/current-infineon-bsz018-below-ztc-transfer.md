# Infineon BSZ018N04LS6 below-ZTC transfer assignment

Status: target and bounded mechanism/controls are independently AGENT-GREEN.
Full authoritative transfer-corpus A/B and human review remain held.
`human_verified=false`; no commit or push.

## Defect and fail-closed scope

Diagram 7 contains two positioned temperature labels: 175 °C owns the left,
hot branch and 25 °C owns the right, cold branch. The plotted 0--160 A range
ends before the two curves cross. The previous validator bound both identities
from their source labels and then discarded them because it required every
two-temperature transfer chart to show a ZTC order reversal.

The replacement keeps positioned source labels mandatory and validates curve
order in inverse/current space. The hot branch must be visibly left of the cold
branch at two shared low-current probes. A robust hot-left-only signature is a
valid below-ZTC chart; one subsequent clean reversal is a valid observed ZTC.
Hot-right evidence, weak low-current separation, missing/ambiguous labels, or
robust recrossing refuse. The return value reports whether a crossover was
actually source-resolved, so below-ZTC convergence cannot become a fabricated
ZTC claim.

## Frozen evidence

Packet: `/private/tmp/dsdig-infineon-bsz018-below-ztc-v1`.

- The target emits 427 points at 25 °C and 423 at 175 °C, remains
  `overlay-review-required`, and has `fit=null` with no crossover claim.
- Across shared-current probes at 19.112, 28.627, 79.372, and 126.947 A, hot
  minus cold VGS is -0.255052, -0.231059, -0.130052, and -0.067338 V.
- IPP024N08NF2S preserves its single clean in-range crossover; IRF6644
  preserves its log-current calibration and no robust crossover claim.
- Synthetic tests cover below-ZTC acceptance, one clean crossing, hot-right
  contradiction, weak separation, and robust recrossing. Sixty-one transfer
  tests plus two subtests and 26 shared diode tests pass.
- Candidate/repeat PDF, overlay, CSV, and charts index are byte-identical; the
  annotated PDF passes qpdf.

Independent review:
`/private/tmp/dsdig-infineon-bsz018-below-ztc-v1/reviews/codex-bsz018-below-ztc-independent-review.json`
(SHA-256
`5f32dcfcbcfe8643c67764ec51400ca7a51a71caaad6522d6f8da4d888caea45`).
It reports all 850 extracted points within 0.90 px of their own source vector
paths (95th percentile at most 0.54 px) and preserves the full-corpus/human
holds.
