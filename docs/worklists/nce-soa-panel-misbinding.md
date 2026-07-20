# NCE SOA-as-capacitance finder misbinding

**Status:** separate future finder item; no production change. Agents must not set
`human_verified`.

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

`NCE2010E`, page 4, is the served-value form of the same ownership failure and
is therefore the load-bearing positive fixture:

- the real capacitance Figure 8 (top right) is not owned by any selected panel;
- selected diagram 8 is labeled `capacitances` but owns Figure 10, the reverse-drain/body-diode plot;
- selected diagram 10 is labeled `capacitances` but owns Figure 13, the SOA plot;
- the SOA lines are assigned Ciss/Coss/Crss identities and currently pass trace validation.

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

## Acceptance

- Positive negatives: all three fail-closed SOA crops plus both wrong `NCE2010E`
  crops are rejected as non-capacitance, and the real Figure 8 is recovered.
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
