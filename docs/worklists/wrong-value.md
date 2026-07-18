# Worklist — 23 wrong-VALUE dsdig extractions (priority re-digitization)

From the 4-agent review sweep of 475 overlays. These are the RED items where the extractor
**produced a value that is wrong** (as opposed to honest fail-closed refusals or over-rejections).
Full per-item data: `agent-sweep-reports/worklist-wrong-value.json`.

## A. Vpl misplaced on a real chart (looks fine, value WRONG — top priority)
1. **DI110N15PQ** (gate_charge_vpl) — src `DI110N15PQ.pdf` p4
   - Vpl red line ~1.85V sits far BELOW the true Miller plateau (~3.3V); Vpl scalar landed on the initial rise, not the plateau
2. **DI110N15PQ-AQ** (gate_charge_vpl) — src `DI110N15PQ-AQ.pdf` p4
   - Vpl red line ~1.85V sits far BELOW the true Miller plateau (~3.3V); Vpl scalar landed on the initial rise, not the plateau
3. **DI110N15PQ-AQ.pdf.gs** (gate_charge_vpl) — src `DI110N15PQ-AQ.pdf.gs.pdf` p4
   - Vpl red line ~1.88V sits far BELOW the true Miller plateau (~3.3V); Vpl on initial rise, not plateau
4. **DI110N15PQ.pdf.gs** (gate_charge_vpl) — src `DI110N15PQ.pdf.gs.pdf` p4
   - Vpl red line ~1.88V sits far BELOW the true Miller plateau (~3.3V); Vpl on initial rise, not plateau
5. **MCG35N04A-TP** (gate_charge_vpl) — src `MCG35N04A-TP.pdf` p3
   - Vpl 4.78V sits on the rising slope, not the true plateau (~3V); left crop clipped (x starts at 10nC)
6. **MCACL120N10Y-TP** (gate_charge_vpl) — src `MCACL120N10Y-TP.pdf` p3
   - Vpl 7.01V sits ~2V ABOVE the true ~5V plateau; crop x-axis starts at 20nC, clipping the low-Q region (score 10.99 lowest of ok-set)

## B. False-pass / wrong axis (positive-ish score but bogus value)
7. **DMN3009LFVQ-13-HXY** (gate_charge_vpl) — src `DMN3009LFVQ-13-HXY.pdf` p4
   - status=axis_assumed; POSITIVE score(15.4) is a FALSE PASS - crop spans Fig.9 Power Dissipation + Fig.10 SOA; blue follows the Pd derating curve, not gate charge; Vpl 4.70 bogus
8. **XR100N02F** (gate_charge_vpl) — src `XR100N02F.pdf` p4
   - status=axis_grid_inferred; WRONG axis calibration - assumed 0-10V grid but real y-axis is 0-4.5V, so Vpl=4.90 grossly overstated (real plateau ~1V) (S0/S2)

## C. Capacitance Ciss/Coss identity crossing (Coss read >= Ciss)
9. **PSMN5R3-25MLD** (capacitance) — src `PSMN5R3-25MLD.pdf` p9
   - Coss (blue) read ABOVE Ciss (red) across low-V region (x~0.1-0.8) — identity crossing/swap in coincidence zone; trace suspect, rank_swap=1 (§4/§5).
10. **PSMNR70-30YLH** (capacitance) — src `PSMNR70-30YLH.pdf` p8
   - Coss (blue) read ABOVE Ciss (red) across low-V region — identity crossing/swap; trace suspect, rank_swap=1 (§4/§5).
11. **PSMN6R1-25MLD** (capacitance) — src `PSMN6R1-25MLD.pdf` p9
   - Ciss (red) steeper than Coss and converging with it at high V (ciss_flatter_than_coss=False) — Ciss>Coss ordering at risk; trace suspect (§4/§5).

## D. Capacitance truncated / pinned-flat / wrong panel
12. **BUK753R8-80E,127** (capacitance) — src `BUK753R8-80E,127.pdf` p8
   - Axis untrusted AND traces truncated (~stop at 60-80V of a 0.1-1000V span); short x-span suspect (§0/§2/§5).
13. **GAN7R0-150LBEZ** (capacitance) — src `GAN7R0-150LBEZ.pdf` p8
   - Ciss/Coss extracted as flat lines pinned mid-chart, truncated at ~10V while source descends to 200V; axis untrusted (§0/§5).
14. **IXTQ130N20T** (capacitance) — src `IXTQ130N20T.pdf` p4
   - Axis untrusted AND Ciss (red) pinned as flat line at axis-top, not tracking the source Ciss stroke (§0/§5).
15. **PSMN013-100BS** (capacitance) — src `PSMN013-100BS.pdf` p6
   - Wrong/linear panel: source curves RISE with VDS; extraction pinned flat, does not track; axis untrusted (§0/§1/§5).

## E. Non-single-valued fold-back / negative-score blob
16. **DMTH47M2LFVWQ-7-HXY.unicoded** (gate_charge_vpl) — src `DMTH47M2LFVWQ-7-HXY.unicoded.pdf` p3
   - status=axis_assumed, neg score; trace has a V-shaped fold-back near 0-1nC (non-single-valued, snapped off curve); axis assumed; left-neighbor bleed
17. **FDB3652** (gate_charge_vpl) — src `FDB3652.pdf` p5
   - status=ok but score=-2.64 (negative); visible fold-back/thick blob artifact at plateau ~5.8V (S0/S5)

## F. Trace truncated / misses ramp -> wrong Vpl
18. **AMIF100S201** (gate_charge_vpl) — src `AMIF100S201.pdf` p4
   - low_confidence, negative score; blue misses initial ramp (starts at Qg~18nC); Vpl above the flat segment (§0/§5).
19. **CR13N50FA9K.pdf.r600** (gate_charge_vpl) — src `CR13N50FA9K.pdf.r600.pdf` p6
   - axis_assumed, negative score; blue extracts only upper ramp (Qg~24-32nC), misses ramp+plateau; Vpl 7.76 far above true plateau ~5V (§0/§5).
20. **XR10G10S** (gate_charge_vpl) — src `XR10G10S.pdf` p5
   - status=low_confidence, score=-1e9; trace truncated (~17nC of 25nC span), axis/trace unverified (S0/S5)

## G. Schematic/inferred-axis extraction giving an out-of-range value
21. **FDB2614** (gate_charge_vpl) — src `FDB2614.pdf` p6
   - status=axis_assumed, vpl_outside_expected_range; schematic circuit/waveform figure, not a chart (S0/S1)
22. **AGM012N10LL** (gate_charge_vpl) — src `AGM012N10LL.pdf` p4
   - axis_assumed, negative score; neighbor C(pF) figure bleed in crop; blue misses initial 0-5V ramp (pinned at plateau); Vpl above true plateau (§0/§1/§5/§7).
23. **HY029N10B** (gate_charge_vpl) — src `HY029N10B.pdf` p5
   - status=axis_grid_inferred, neg score; y-axis inferred (max ~9V, likely truncated); Vpl unverified (shape near-miss)
