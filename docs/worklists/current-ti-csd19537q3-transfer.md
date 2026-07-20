# TI CSD19537Q3 converging transfer paths

Status: focused implementation and deterministic packet frozen; independent
agent review GREEN for path identity and source seating. `human_verified=false`;
no commit or push.

## Defect

`ti/CSD19537Q3.pdf` Figure 5-3 contains three complete vector transfer curves
for -55, 25, and 125 °C. The three paths are distinct near threshold but
converge at the 80 A top frame. The pooled endpoint chainer joins two source
paths there and reports only two curves, so the annotator refuses the real
panel.

## Bounded contract

- Preserve the established pooled/component and monotone-run paths first.
- Only when their curve count does not equal the printed temperature count,
  inspect each PDF drawing independently.
- One drawing may contribute only one complete, structurally valid transfer
  candidate. Accept the rescue only when that exact source-object count equals
  the temperature-label count.
- A three-label panel with only two valid source drawings must refuse; never
  split noise or synthesize a phantom branch.
- Preserve the legacy two-temperature path byte-for-byte.
- Bind temperature identities from the source-proven low-current ordering and
  retain review-required status.

## Focused evidence

Frozen packet: `/private/tmp/dsdig-ti-csd19537-transfer-v1`.

- Source PDF SHA-256:
  `70c5c32100d1d36e32727949bef2859d09c792a60d97080ab010fac52f638abf`.
- Candidate and repeat annotated PDF SHA-256:
  `7ee75cd4ca7c1e04292df50c2056a650205b950fa1b846cc8c756ff8d488f4f5`;
  `qpdf --check` passes.
- Candidate and repeat overlay SHA-256:
  `a668afaf46a515ccc432b9c8a3ab8b262cf655719faa93911b143f7166ae1340`;
  CSV SHA-256:
  `b719961d9a438dcb68d9652b0b675a35fa35509cc9016647ac15a68ca5025309`.
- The result contains exactly -55/25/125 °C, one equal-length sampled branch
  per source path, six VGS ticks, nine ID ticks, and sub-0.1 px fit residuals.
- The full transfer test file passes 35 tests, including the load-bearing real
  PDF and a two-path/three-label refusal fixture.

Independent review:
`/private/tmp/dsdig-ti-csd19537-transfer-v1/reviews/opus-ti-transfer-review.json`
(SHA-256
`7b5c8f22c67588c863b85bcd646d94ecb9008380cb083729693e9d5820ae5d99`).
The reviewer verified all three source paths and temperature identities, the
low-current Vth ordering, source seating, and convergence without branch swap.
The review remains agent-only (`human_verified=false`). The source-vs-extraction
overlay remains `overlay-review-required`, and there is no authoritative
full-transfer-corpus harness in the current verification tools, so
shared-extractor landing is not claimed regression-free from this focused
packet alone.

## Separate follow-up

Figure 5-7 is RDS(on) versus VGS. It is a real digitizable chart but has no
supported plugin; it must remain unpainted until a dedicated quantity contract
and extractor exist.
