# STP15NK50Z detached Figure-number caption ownership

Status: finder mechanism independently AGENT-GREEN; Figure 12 extraction
remains blocked on outlined X ticks. `human_verified=false`; no commit or push.

## Defect and contract

On page 6, the word `Figure` and the number `12` are separate PDF text rows.
The caption row previously failed to own the detached number, and the resulting
breakdown crop spanned both Figures 10 and 12. The finder now joins a detached
numeric token only when it is immediately preceded by `Figure`, `Fig`, or
`Diagram`, shares its baseline within 4 pt, and has a bounded -2..14 pt gap.
Arbitrary nearby numbers remain barriers.

## Frozen evidence

Packet: `/private/tmp/dsdig-stp15nk50z-detached-caption-v1`.

- Page 6 Diagram 12 shrinks from a 383 pt two-row crop to the single 167 pt
  Figure 12 row.
- There are no panel additions or removals. Exactly three causally equivalent
  detached-caption crops shrink.
- STF7N60M2, STD14N50, and STH310N10F7 breakdown controls are byte-identical.
- Candidate/repeat finder outputs are deterministic.
- The downstream refusal is still honest: `X axis (Tj): only 0 tick labels,
  need >=4`. The tick labels are outlined glyphs and this slice does not invent
  their values.

Independent review:
`/private/tmp/dsdig-stp15nk50z-detached-caption-v1/reviews/stp15nk50z-detached-caption-independent-review.json`
(SHA-256
`758b3ff65d229180c42bbcf04a065451353064182674bd40fc84b29f61a1ad8a`).
