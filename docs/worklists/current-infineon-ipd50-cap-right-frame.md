# Infineon IPD50N10S3L-16 capacitance right-frame coverage

Status: Fab human-FLAGGED target; mechanism triage and repro packet pending.
`human_verified=false`; no implementation, commit, or push authorized by this
document.

## Defect

Infineon `IPD50N10S3L-16` p6d10 has a capacitance plot box that ends near
85 V although the source chart continues to its real 100 V right frame. Black
curve tails, the Ciss/Coss/Crss labels, and the printed 100 V tick lie outside
the orange detected box. All three extracted traces are therefore truncated by
roughly 15 V even though their behavior inside the selected box appears
plausible.

Authoritative review state is in
`/Users/fab/dev/pv/ee/dsdig-verify-backlog/MANIFEST.opus-cap-batch27.jsonl`:
`human_review_status=flagged`, `extract_ok=false`, and
`human_verified=false`.

## Relationship to existing frame work

This is the same observable failure class as
[NDB5060L right-frame recovery](current-onsemi-ndb5060l-cap-right-frame.md):
an interior vertical is consumed as the plot's right boundary while the owned
closed frame continues farther right. The exact IPD mechanism is not yet
pinned, so do not assume that NDB5060L's 96%-crop-edge condition is causal.

The general positive-evidence rule from the closed-frame work remains binding:
extend only to a right rail that closes against the owned top and bottom rails
while preserving the left/top/bottom box. Never extend to a neighbor-panel
rail, crop border, label stroke, or whitespace.

## Bounded contract

- Freeze the native source crop, current overlay/values, page drawing evidence,
  and candidate/repeat artifacts before editing the detector.
- Prove the actual right frame from mutually closing rails and the 100 V
  endpoint tick; the tick alone is not sufficient frame evidence.
- The candidate box must contain all three printed curve tails and their source
  labels through the owned 100 V boundary.
- Preserve caption binding, left/top/bottom edges, axis model, tick identities,
  Ciss/Coss/Crss identities, and any Qoss/reference status not causally changed
  by the added voltage interval.
- Every recovered tail must be source-seated; a longer trace that rides the
  frame or a horizontal grid line is RED.

## Controls and acceptance

1. `NDB5060L` Figure 9 remains physically byte-identical and human-GREEN.
2. Include the NCE4080/NCE60P28AK bounded right-frame controls from the
   NDB5060L packet, retaining their existing unserved/refused states.
3. Add a negative with a visually tempting right-side neighbor rail that must
   not extend.
4. Candidate equals repeat for box, points, overlay, values, and annotated PDF.
5. Run the authoritative full capacitance-corpus A/B and inspect every changed
   frame; zero unexplained box deltas are allowed.
