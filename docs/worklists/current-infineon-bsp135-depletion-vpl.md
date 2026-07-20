# Infineon BSP135 depletion-mode Vpl diagnostic

Status: target item, diagnostic-only mechanism, determinism, and bounded
enhancement control are independently AGENT-GREEN. Full authoritative
gate-charge corpus A/B and human review remain held. `human_verified=false`;
no commit or push.

## Defect and bounded correction

BSP135 page 7 diagram 15 is a depletion-mode gate-charge chart with a signed
VGS axis (-4 to +8 V). The source trace starts near -2.8 V and has a clear
Miller plateau at 0.20 V. Extraction was correct and status was `ok`, but the
fixed enhancement-mode plausibility band emitted
`vpl_outside_expected_range` for the source-seated value.

The warning is now suppressed only when all source conditions agree: the
measured axis spans negative and positive voltage, the calibrated trace starts
below -0.25 V, Vpl remains inside the measured axis, and one contiguous run
within two source pixels of `vpl_y_px` spans at least 5% of the plot width.
Selection, scoring, retry acceptance, status, serialized values, and the
ordinary enhancement-mode plausibility function are unchanged.

## Frozen evidence and review

Packet: `/private/tmp/dsdig-infineon-bsp135-depletion-vpl-v1`.

- Baseline and candidate both serve Vpl `0.2006517666 V`, score `10.190344`,
  and the same 412-point trace. Only the false diagnostic is removed.
- Candidate/repeat overlay SHA-256 is
  `74a45682db662bf5c0d57fcadebd552f3e7fd027d292bbdd8d8b15a88e39b195`;
  canonical physical manifest SHA-256 is
  `2b899ae63e44140396b7faacbc02245e1dc0ff049ed4fd6adce817b161ed0ecc`.
- The focused suite passes 65 tests and four subtests, with 18 optional corpus
  skips. An all-nonnegative enhancement axis cannot enter the relaxation.

Independent review:
`/private/tmp/dsdig-infineon-bsp135-depletion-vpl-v1/reviews/bsp135-depletion-vpl-independent-review.json`
(SHA-256
`35944c6aa6224fb3a0c0530b51d648069a1233b9ab60fe3164031ed483bffa88`).
Verdict: `AGENT-GREEN_SCOPED_FULL_CORPUS_HELD`. Item seating, bounded mechanism,
determinism, baseline emulation, and the enhancement negative are GREEN; full
corpus and human review remain held.
