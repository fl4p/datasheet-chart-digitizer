# Gate-charge definition-waveform finder audit

**Status:** future bounded finder item; no production change. Agents must not set
`human_verified`, commit, or push from this worklist.

## Why this remains separate

The frozen HXY title-classifier A/B currently removes 1,750 captions that explicitly contain
`test circuit`; source renders show topology schematics and generic Qg/Qgs/Qgd definition
sketches rather than device-specific measurements. A follow-up corpus scan still finds 2,975
titles with waveform/definition wording, including 108 exact `Gate Charge Waveforms` and 46
`Gate Charge Waveform` titles. Some may be the separate generic waveform companion to a test
circuit, while others may be real device-specific Qg curves. Wording alone is therefore not a
safe rejection predicate.

These counts remain provisional until the current HXY candidate completes its final authoritative
finder A/B and the frozen packet receives dual independent review.

This item must not be folded into the frozen test-circuit fix: doing so would invalidate its
causal A/B and could silently remove measured charts.

## Required evidence and implementation constraints

1. Freeze the exact title/finder corpus, corpus-list hash, source hashes, environment, and
   baseline manifest under checklist §9.
2. Source-render every distinct waveform/definition title family and classify it as either:
   device-specific measured characteristic, non-data-bearing definition waveform/test
   schematic, or ambiguous.
3. Reject only with positive non-data-bearing evidence (schematic topology, definition arrows,
   missing numeric device conditions/axes, or a source-owned test-circuit association). Never
   blanket-reject the token `waveform`.
4. Fail closed on ambiguous panels; do not manufacture Vpl or curve data from an illustrative
   Miller sketch.
5. Run the complete authoritative finder corpus A/B. Every removed/changed panel needs a §1/§7
   source verdict; additions outside the evidenced class are over-fire. Existing measured
   gate-charge charts must remain byte-identical.
6. Include multi-vendor positives and negatives, including a real measured title containing
   `waveform` that must be preserved and generic Qg/Qgs/Qgd definition sketches that must be
   rejected.

## Acceptance

Dual independent agent GREEN on the frozen full-corpus packet, zero measured-title loss, and
Fab's landing decision. `human_verified` remains false unless Fab explicitly reviews an item.
