# Infineon IRF100PW219 transfer-label binding

Status: page-8 Diagram 7 item, bounded opposite-outer label fallback,
determinism, two byte-stable positive controls, one fail-closed adversarial
control, focused tests, and annotate embedding are independently AGENT-GREEN.
The authoritative full transfer-corpus A/B and human review remain held.
`human_verified=false`; no commit or push.

## Defect and bounded fallback

IRF100PW219 has two valid, close, crossing transfer curves. The original
Euclidean label binder correctly refused because the 175 °C label's distance
advantage missed its per-label confidence gate by about 0.22 px, even though
the source layout independently places the 175 °C label outside the left
branch and the 25 °C label outside the right branch.

The original distance binder remains first. Its ambiguous result may fall
back only when, at each label's own Y/current, the label is outside the local
two-curve envelope by `max(2 px, 1% plot width)` and the nearer source curve
wins by the same margin. The two labels must lie on opposite outer sides and
bind distinct curves. Labels between curves, on the same side, outside a
curve's Y support, or within either margin still refuse. The existing
low-current hot-left and single-reversal physical checks remain mandatory
after binding.

## Frozen evidence

Packet: `/private/tmp/dsdig-irf100pw219-transfer-binding-v1`.

- Same-host causal baseline, with only the new fallback disabled, reproduces
  `two-curve temperature label/curve binding is ambiguous`.
- Candidate serves 543 points at 25 °C and 563 points at 175 °C as
  `overlay-review-required`; no fit or coefficients are served.
- The hot 175 °C branch is left/lower-threshold at low current. Identities
  reverse exactly once near 440.4 A and remain source-consistent afterward.
- Independent pixel comparison finds 100% of 2073 red and 2335 blue overlay
  trace pixels within 1 px of dark source ink, with no grid ride, branch jump,
  or source-absent extension.
- X calibration is 0--7 V from eight ticks at 0.00003 px residual; Y is
  0--800 A from nine ticks at 0.00006 px residual.
- Candidate/repeat charts, canonical result, CSV, and overlay are identical.
  A real `annotate --include-review-required` run exits 0, embeds the owned
  page-8 overlay, and produces byte-identical candidate/repeat PDFs.
- BSZ018N04LS6 below-ZTC and IRF6644 log-ID outputs are byte-identical across
  baseline/candidate/repeat. BSB028N06NN3_G still refuses its physically
  contradictory hot-right label assignment.
- The focused suite independently passes 64 tests and four subtests.

## Independent review

Review:
`/private/tmp/dsdig-irf100pw219-transfer-binding-v1/reviews/independent-review.json`
(SHA-256
`836366b134ace018751b7f8ee6f05a9dd216ece263470f462a18ad929575245b`).
Verdict: `AGENT_GREEN_SCOPED_FULL_CORPUS_HELD`. The reviewer kept the item,
bounded mechanism, corpus, dirty-tree, causal-baseline, and human-review
verdicts separate.
