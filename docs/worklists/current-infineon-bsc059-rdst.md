# Infineon BSC normalized RDS(on) temperature routing

Status: bounded patch and Diagram 9 item independently AGENT-GREEN.
`human_verified=false`; no commit or push.

## Defect

`infineon/BSC059N04LS6ATMA1.pdf` Diagram 9 is normalized RDS(on) versus
junction temperature, but its title is only "Normalized drain-source on
resistance". Temperature direction is carried by the owned `Tj [°C]` x-axis
and `RDS(on)=f(Tj)` formula. The RDS-temperature selector required a title
clause such as "vs temperature", recognized only Figure/Fig prefixes, and did
not accept Infineon's bracketed Tj unit, so the supported panel was omitted.

## Bounded contract

- Preserve every explicit `... vs/as a function of/variation with temperature`
  title path.
- Admit a title-only fallback only for a normalized RDS(on) stem with no other
  direction clause.
- Recognize packed `Diagram N: ...` words without changing existing Figure/Fig
  parsing.
- Require the same owned chart region to contain both a bracketed or
  parenthesized Tj temperature axis and `RDS(on)=f(Tj)` before selecting the
  below-caption grid.
- Keep RDS(on)-vs-ID and RDS(on)-vs-VGS excluded; a relaxed title alone is
  insufficient.
- Re-assert the title/formula/temperature identity during physical validation.

## Frozen evidence

Packet: `/private/tmp/dsdig-inf-bsc059-rdst-v1`.

- Baseline emits zero BSC059 panels; candidate emits exactly page 8 Diagram 9.
- Candidate and repeat bounded trees are byte-identical (nine files).
- The new result is `ok`, one 10 V source-vector curve with 444 points,
  temperature -54.76..175.02 °C, normalized RDS(on) 0.778..1.899, and 0.997 at
  25 °C.
- TI `CSD19537Q3` and onsemi `NDB5060L` controls are byte-identical baseline to
  candidate.
- Candidate and repeat annotated PDFs are byte-identical, SHA-256
  `f69cdf168e0fe744786bef2ff7c2773fbe2cb3febec9174a7061b3ab7754e001`;
  `qpdf --check` passes.
- The exact isolated candidate test file passes 20 tests and 21 subtests.

The packet is bounded, not a full-corpus RDS-temperature A/B. The independent
microscopic review below covers frame ownership, tick centers, source seating,
origin/tail, formula/10 V identity, and control equality.

Independent review:
`/private/tmp/dsdig-inf-bsc059-rdst-v1/reviews/codex-bsc059-rdst-review.json`
(SHA-256
`0d3074be0e9b2d46b8db3ff58b2db5832bdd8bc578dbece19caf243e90b11d93`).
It found no blockers: the joint Tj/formula ownership and ID/VGS exclusions
hold, every tick is within 0.5 px of its source grid center, all 543 points in
the annotated 220 dpi trace sit directly on source-dark ink, controls are
unchanged, and both PDFs are deterministic and valid. Patch and item are
AGENT-GREEN within this bounded packet; full-corpus coverage remains unclaimed.

## Separate unsupported charts

RDS(on)-vs-VGS and threshold-voltage-vs-temperature still lack dedicated
quantity plugins and remain outside this slice.
