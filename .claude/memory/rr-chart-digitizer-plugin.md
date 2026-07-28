---
name: rr-chart-digitizer-plugin
description: "datasheet-chart-digitizer reverse-recovery plugin (AO Qrr/Irm/trr/S charts, 25/125C) — the N_TAU datasheet-data source; scale verification + fail-loud manifest; AOT414 human-verified"
metadata: 
  node_type: memory
  created: 2026-07-13
  type: project
  originSessionId: a2bd660c-69f6-46d4-b732-87e26b271d90
---

`datasheet-chart-digitizer` (repo /Users/fab/dev/pv/ee/datasheet-chart-digitizer, own `.venv`, CLI `dsdig`) has a **reverse-recovery plugin** since `dedb19f` (2026-07-13): `dsdig digitize-reverse-recovery PDF... --out DIR`.

- **Purpose**: AO datasheets chart Qrr/Irm (fig17/19-style) and trr/S (fig18/20) at **25 AND 125 °C** — the only known datasheet data for the Qrr temperature axis (`dslib/qrr_model.py` N_TAU); all Infineon/onsemi two-point tables are 25 °C only ([[fetlib-qrr-curve-issue]]).
- **Extraction quirks solved** (`reverse_recovery.py`): AO curves are FILLED outline polygons (stroke-based vector path sees nothing) split into ~30 pt segments, some painted twice; centerline = x-binned (min+max)/2 of the thin outline, chained tight + tail-slope merge, deduped. Doubled text layers (AOB414: "Figure Figure 17: 17:") collapse via consecutive-word dedup. Infineon-style `\x03` nbsp does NOT occur here, but side-by-side captions share one text line (split at "Figure" tokens); wrapped caption line vertically OVERLAPS the caption bbox.
- **Validation** (`reverse_recovery_validation.py`, mirrors capacitance_validation.py): spec-table trr/Qrr anchors (geometry-parse; text stream scrambles columns — use vertical-CENTER row bands, ±5.5 pt), cross-panel consistency (same physical point in two charts), 125>25 °C physics invariants, y-axis unit-label check, three-state verdict `verified`/`FAIL`/`unverified`. Overlays draw every consumed tick as a green crosshair + parsed value (Fab requires this visual scale verification).
- **Human-verified sample**: AOT414 (2026-07-13, artifact 30366b06). Table anchor −6.3 %, cross-panel Qrr −10.4 %/−0.5 %. fig18/20 FAIL correctly on the S-softness identity defect (S pairs overlap ~exactly — known limitation). AOT418L (stroked grids) unsupported, fails loud. Regression tests pin the numbers (`tests/test_reverse_recovery.py`, needs the local datasheet library).
- **Headline physics result (BATCH-CONFIRMED 2026-07-13, committed dedb19f+7e2a6f6, pushed)**: all 434 AO PDFs batched → 7 Qrr-vs-di/dt panels → **3 unique dies** (AOT414/AOB414/AON6452 + AOD/AOTF4126 groups), ALL scale-verified: Qrr(125)/Qrr(25) = **1.32–1.33**, raw empirical **n_tau = 0.56** each; contamination bracket 0.3–0.9 — well below dslib N_TAU=1.2 (over-predicts these parts' hot Qrr +39%). Self-consistent offset q0≈19 nC reproduces the full Qrr(di/dt) curve to ±4.9 % (raw ±18.8 %) — second-vendor confirmation of [[fetlib-qrr-curve-issue]]'s decontamination model. Die groups' curves suspiciously similar (shared characterization?) → treat as ~1–3 independent points. **N_TAU=1.2 deliberately NOT changed** (single vendor, may not transfer to Infineon OptiMOS, errs conservative; ~10–15% of the Fugu2 Qrr bucket at 80 °C); bench double-pulse decides — expect the hot multiplier LOW. Evidence posted on fetlib#37.
- Per Fab's global CLAUDE.md rule: ALWAYS use this lib for chart digitization; propose detection-code extensions; human-verified samples before batching a new chart category.
