---
name: dsdig C(V) shared-collapse detector correction
description: Class-C C(V) crossings require source-seated bridge anchors: v4 was human-RED because PSMN5R3 blue drifted onto red before crossing; v5 fixes the notch and remains review-gated.
created: 2026-07-17T11:32:33.785Z
metadata:
  node_type: memory
  generator: opencode-claude-memory
  type: project
  originSessionId: ses_09028a47effeX71Y1GeOOOxEMQ
---

For NXP PSMN5R3-25MLD and PSMNR70-30YLH, the printed Ciss/Coss strokes genuinely cross once: labels on the right identify Ciss upper/Coss lower, while Coss is above Ciss at low VDS. The corrected extractor preserves this crossing, keeps the correct label binding, and emits `shared_collapse_spans=[]` because the curves re-diverge after the sign-changing crossing.

The initial Class-C v4 agent clearance was **retracted after Fab's human RED** on PSMN5R3. At microscopic scale, blue Coss had already drifted about 3 px toward red Ciss before the crossing repair chose its left identity anchor. The bridge therefore preserved a late, off-source anchor and produced a visible notch/neighbor snap. A merely distinct identity column is insufficient: the bridge anchor must also be seated on its selected source stroke. The v5 implementation requires <=1 px source seating, pins a progressively drifting known-bad, and removes the Coss y-pixel reversal. It is not human-GREEN until Fab reviews the new packet.

For NXP PSMN6R1-25MLD, the printed labels show Ciss = the **flat** upper curve and Coss = the **steep** lower curve. The v4 re-digitization repairs the prior identity swap using the flatness guard (`flatness_guard_repaired_single_crossing_swap`, `ciss_flatter_than_coss`), so the extracted Ciss is now the flat curve and Coss the steep curve. The v2/v3 long right-tail overlap was a **false shared span** — a tail-zoom of the source PDF shows the two strokes cross once and then **re-separate** as distinct labeled curves (Coss below Ciss at the high-VDS tail). v4 drops this span and emits `shared_collapse_spans=[]`.

The shared-collapse detector must require a **sustained single-stroke merge** before flagging a region as shared; a transient sign-changing crossing or a cross-and-re-separate pattern must emit **zero shared spans**.

2026-07-20 follow-up: the detector can also correctly record a bad sustained
merge while validation still incorrectly returns `pass`. `FDMS86200DC` p6d8,
`TK55S10N1` p6d88, and `STW70N60DM6-4` p6d7 contain a non-edge Ciss/Coss span
with established ordering before it and `separated_sign_after=null`; physical
Coss must fail closed or be recovered from separately owned source ink. Do not
globally reject every shared span: `BSZ086P03NS3_G`, `STD80N6F7`,
`STL170N4LF8`, and `TPH1R204PB` have low-V edge convergence followed by proven
separation. Authoritative slice:
`datasheet-chart-digitizer/docs/worklists/current-capacitance-unresolved-shared-collapse.md`.

Do not fold neighboring Crss/top-axis failures into that predicate.
Fab's final Batch 27 verdict marks `FDMS2572` p5d8 human-FLAGGED for confirmed
high-V Crss tail truncation (`x_span_fraction=0.9087`); it also records a top
decade caveat. That case, the human-flagged `IPB160N04S2L-03` low-V Crss source
divergence, and the other active Crss coverage cases are tracked by
`current-capacitance-trace-coverage-top-clip.md`.

**Why:** Enforcing Ciss>Coss or treating a sign-changing crossing as a shared collapse would falsify PSMN5R3/PSMNR70, but deleting the flatness guard would miss the PSMN6R1 swap. The source legend is authoritative for identity, and the guard must be calibrated against the known-bad fixture. A cross-and-re-separate fixture must not be labeled as a sustained merge.

**How to apply:** When reviewing capacitance extractions, first render the unoverlaid source PDF and check the printed labels against the extracted curves. Preserve genuine crossings (PSMN5R3/PSMNR70) by requiring a sustained single merged stroke before flagging shared-collapse. Fix genuine label swaps (PSMN6R1) before relaxing the `ciss_flatter_than_coss` guard. For any claimed shared span on a crossing fixture, zoom the source tail and verify the curves actually merge into one indistinguishable stroke; if they re-separate, the span is false and the item is RED.
