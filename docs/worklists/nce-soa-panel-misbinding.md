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

## Required direction

1. Reject SOA panels as capacitance candidates using source semantics and panel
   ownership, not a part-specific denylist. SOA axes (`I_D` vs `V_DS`) and labels
   such as `DC`, `10us`, `100us`, `1ms`, and `10ms` are strong non-capacitance
   evidence.
2. Preserve the fail-closed contract on ambiguous panels: no Ciss/Coss/Crss
   points, Qoss reference, or physical-output flag may leak.
3. If a real capacitance panel exists elsewhere in the same datasheet, recover
   only after rendering the source and proving the panel/page identity.

## Acceptance

- Positive negatives: all three SOA crops are rejected as non-capacitance.
- Legitimate capacitance charts containing `V_DS` remain accepted when their
  y-axis is capacitance and Ciss/Coss/Crss source strokes are present.
- Full finder corpus A/B under checklist §9; every panel/page/crop selection
  delta receives source review.
- No change to the closed-frame patch's verified boxes or to any frozen
  cap-anchor/Qoss packet.
