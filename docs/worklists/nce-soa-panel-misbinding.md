# SOA/body-diode as capacitance panel misbinding

**Status:** bounded safety candidate implemented; focused tests and frozen
800-panel metadata A/B are green. Real capacitance-panel recovery and fresh
source-to-result finder A/B remain held by unavailable Git-LFS PDFs.
`human_verified=false`.

## Defect

The frozen 800-panel capacitance collateral run identifies three NCE candidates
whose selected crops are Safe Operating Area charts rather than capacitance
charts:

- `NCEP050N12D`, page 4, diagram 7;
- `NCEP065N10GU`, page 4, diagram 7;
- `NCEP60ND60G`, page 5, diagram 7.

All three currently fail closed (`status=unverified`,
`physical_output_available=false`), so no wrong capacitance value is served.
The FDPF190 closed-frame prototype changes their bottom box edge to the SOA
panel's true frame but correctly leaves them refused. This finder defect is not
part of the frame patch.

`NCE2010E`, page 4, is the load-bearing two-family fixture:

- the real capacitance Figure 8 (top right) is not owned by any selected panel;
- selected diagram 8 is labeled `capacitances` but owns Figure 10, the reverse-drain/body-diode plot;
- selected diagram 10 is labeled `capacitances` but owns Figure 13, the SOA plot;
- the reverse-diode crop and SOA crop are both passed to the capacitance
  extractor; the reverse-diode trace retains `trace_validation_status=pass` in
  the frozen result, although the current axis contract withholds physical
  values.

`NCE20P45Q` is the known-clean multi-panel negative: its transfer, gate-charge,
body-diode, and capacitance panels must retain their own source figures.

## Required direction

1. Reject SOA panels as capacitance candidates using source semantics and panel
   ownership, not a part-specific denylist. SOA axes (`I_D` vs `V_DS`) and labels
   such as `DC`, `10us`, `100us`, `1ms`, and `10ms` are strong non-capacitance
   evidence.
2. Preserve the fail-closed contract on ambiguous panels: no Ciss/Coss/Crss
   points, Qoss reference, or physical-output flag may leak.
3. If a real capacitance panel exists elsewhere in the same datasheet, recover
   only after rendering the source and proving the panel/page identity.
4. When a validator guard refuses the panel, enforce the
   [capacitance fail-closed point contract](cap-fail-closed-contract.md): raw pixel
   diagnostics may remain, but calibrated VDS/pF points must not stay consumable.

## Bounded safety implementation

The finder now lets decisive owned panel semantics override a misbound
capacitance caption. It recognizes only two contradictions in this slice:

- explicit Safe Operation/Operating Area text; and
- explicit source-drain/body-diode forward text, or the co-owned reverse drain
  current and voltage axes.

The override is vetoed when the owned panel contains Ciss/Coss/Crss identities
or a capacitance axis with F/pF/nF/mF/uF units. This preserves legitimate
capacitance charts whose crop also contains a following SOA caption. The
capacitance digitizer applies the same semantic guard before reading the crop,
so a stale pre-fix `charts.json` cannot bypass finder classification.

Frozen packet: `/private/tmp/dsdig-noncap-panel-v1/`.

- Source chart index: 800 capacitance rows, SHA-256
  `3ccf98f2b359ee7c0011ef9db6e0f38af664f0dedfdcdc217940ec3b63e470b3`.
- Exactly 38 rows have decisive non-capacitance semantics: 34 SOA and four
  reverse/body-diode panels. Every crop is source-reviewed in
  `source-contact.png`; none is a capacitance chart.
- Fourteen rows previously serialized results and 24 already errored. Two of
  the serialized rows retained trace-validation pass, but zero retained
  physical output under the current baseline.
- Candidate and repeat refuse all 38 before trace extraction. Their error JSON
  is byte-identical at SHA-256
  `0b49181f5b40f3a77a15a1ed61f3f8886b20c188d8e9b9bc4be5e44177212f4f`.
- Metadata A/B SHA-256:
  `232f8b1872fe370cfb82f8f74e19652a322b3502310f0ab07e8842fdd79da890`.

## Acceptance

- Positive negatives: all three original fail-closed SOA crops plus both wrong
  `NCE2010E` crops are rejected as non-capacitance. The broader frozen set's 38
  decisive SOA/body-diode panels must also remain excluded.
- `NCE2010E` must cease serving the SOA-derived Ciss/Coss/Crss points; a classifier
  fix alone does not waive the validator's rising/SOA-shaped trace refusal gate.
- Legitimate capacitance charts containing `V_DS` remain accepted when their
  y-axis is capacitance and Ciss/Coss/Crss source strokes are present.
- `NCE20P45Q` remains byte-identical unless a source-reviewed crop-boundary
  correction is explicitly classified.
- Full finder corpus A/B under checklist §9; every panel/page/crop selection
  delta receives source review.
- No change to the closed-frame patch's verified boxes or to any frozen
  cap-anchor/Qoss packet.

## Remaining recovery gate

The safety slice does not recover `NCE2010E` Figure 8. Its real capacitance
panel was never present in the frozen selected crops, and the canonical source
PDF is currently a 131-byte Git-LFS pointer. Rebinding that caption requires a
fresh source page plus one-to-one frame/axis ownership proof; a neighboring
crop or generated PDF is not an acceptable substitute.
