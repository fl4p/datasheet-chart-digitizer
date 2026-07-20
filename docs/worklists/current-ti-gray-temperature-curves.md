# TI gray temperature-curve recovery

Status: the two transfer items, one body-diode item, and bounded shared
mechanism are independently AGENT-GREEN. Authoritative full transfer and
body-diode corpus A/B and human review remain held. `human_verified=false`;
no commit or push.

## Defect and guarded recovery

Several TI charts use a neutral-gray temperature branch which the normal
curve-color classifier deliberately excludes with gridlines. The recovery is
panel-local and fallback-only: it runs after exactly `N-1` normal full-span
curves were found under `N` printed temperature labels, requires one unique
neutral-gray source drawing with the same stroke width as the confirmed data
curves, and retains every downstream count, identity, monotonicity, and
source-seating gate. Zero or multiple qualifying gray drawings still refuse.

## Frozen evidence

Packet: `/private/tmp/dsdig-ti-gray-temperature-curves-v1`.

- CSD17573Q5B and CSD18512Q5B transfer recover the printed -55/25/125 °C
  branches with 378 and 405 points per branch. Independent source-object
  checks found median source distance 0 px and maxima 0.605/0.001 px, with at
  least 4.88 px separation from the other branches.
- CSD17573Q5B body diode recovers gray 25 °C and red 125 °C. All 378 pixel
  points per branch are source-seated, the other-branch margin exceeds
  43.84 px, and consumed ticks are within about one pixel of source grid
  centers.
- Candidate/repeat target PDFs, CSVs, overlays, and JSON are byte-identical;
  the PDFs pass qpdf. The prior-GREEN CSD18512Q5B body-diode result/overlay and
  CSD19537Q3 transfer CSV/overlay remain byte-identical.
- Independent focused execution reports 80 passed, 25 skipped, and 2
  subtests. Transfer has a direct competing-gray refusal test. The equivalent
  diode refusal is code-proven by the unique-candidate and exact-count gates,
  but a dedicated adversarial unit test remains a non-blocking gap.

Independent review:
`/private/tmp/dsdig-ti-gray-temperature-curves-v1/reviews/codex-independent-gray-temperature-review.json`
(SHA-256
`3fa56780fb1858b13f46c46a158a6419a4f0fae3b831eb50522ddddf2b05e3a9`).
