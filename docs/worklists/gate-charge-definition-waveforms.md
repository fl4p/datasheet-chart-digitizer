# Gate-charge definition-waveform finder audit

**Status:** the source-gated ROHM paired-definition slice is agent-GREEN on the
11-document layout cluster. The broader waveform-title corpus remains held and
full-corpus behavior was not evaluated. `human_verified=false`.

## Why the broader audit remains separate

The frozen HXY title-classifier A/B currently removes 1,750 captions that explicitly contain
`test circuit`; source renders show topology schematics and generic Qg/Qgs/Qgd definition
sketches rather than device-specific measurements. A follow-up corpus scan still finds 2,975
titles with waveform/definition wording, including 108 exact `Gate Charge Waveforms` and 46
`Gate Charge Waveform` titles. Some may be the separate generic waveform companion to a test
circuit, while others may be real device-specific Qg curves. Wording alone is therefore not a
safe rejection predicate.

These counts remain provisional until the current HXY candidate completes its final authoritative
finder A/B and the frozen packet receives dual independent review.

The ROHM fix does not blanket-reject waveform wording. It requires a same-page
gate-charge measurement/test-circuit association and fewer than two standalone
numeric tick tokens in the candidate. Measured waveform panels with numeric
axes therefore remain gate-charge candidates. The remaining title families
still require their own source audit and must not be folded into this slice.

## ROHM layout-cluster slice

Layout cluster `doc-00071` contains all 11 ROHM PDFs in the canonical
Nexperia/ROHM/Vishay/Littelfuse clustering output. Every sampled baseline had
the same seven panels and the same three false gate-charge candidates:

- page 3 diagram 901 owns rows from the electrical-characteristics table, not
  a plot;
- page 9 diagram 21 is a gate-charge measurement circuit;
- page 9 diagram 22 is the paired generic Qg/Qgs/Qgd definition waveform.

The genuine measured panel is page 8 diagram 902, `Typical Gate Charge`.
After the scoped change, every cluster member has five finder rows: the four
unrelated supported panels are unchanged, page 8 diagram 902 is the sole
`gate_charge`, page 9 diagram 22 remains generic `chart` audit evidence, and
the table/circuit false candidates no longer enter the gate digitizer.

The table guard now also recognizes two distinct capacitance parameter rows
combined with a switching-time row. The circuit classifier recognizes both
`test circuit` and `measurement circuit`. The paired waveform rule requires an
adjacent sibling circuit caption, or the immediately preceding numbered circuit
caption on the same page, plus the absence of a numeric tick ladder. The word
`waveform` alone is never a rejection predicate.

Frozen packet: `/private/tmp/dsdig-rohm-rx3p10-ownership-v1/`.

- source SHA-256:
  `bf1e09feaaa3ebb425cd9542a45ac162904a2412744b258325df3d0533a5e60b`;
- candidate/repeat annotated PDF SHA-256:
  `4d62e76e56b4f23aa3f267b8769bb6785cb555511ec9127fab6210a019294abf`;
- candidate/repeat finder JSON SHA-256:
  `75dc363f7b0d22564c4e8712d0e07f0b2894e5a85c7a2fd595d4a2f38543732d`;
- candidate/repeat genuine gate overlay SHA-256:
  `de94db9ce1ce4611a948020b24c376cd579f08a739dd04448c18f0eba3d01608`.
- independent review:
  `/private/tmp/dsdig-rohm-rx3p10-ownership-v1/reviews/independent-review.json`,
  SHA-256 `6a211aa0e844dc86b6517589bfbd646dcee574c80dce84d7598b0cf48c2d210c`;
  item and bounded 11-member mechanism GREEN, full corpus `NOT_EVALUATED`.

The aggregate annotation run now completes and passes `qpdf --check`. The
genuine gate panel remains an honest `unresolved` result because its `nC` unit
is not yet owned; no Vpl is served. Independent review passed 127 tests with six
optional skips, 33 subtests, and the real ROHM regression. Full-corpus A/B is
not claimed.

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

## Broader acceptance

Dual independent agent GREEN on the frozen full-corpus packet, zero measured-title loss, and
Fab's landing decision. `human_verified` remains false unless Fab explicitly reviews an item.
