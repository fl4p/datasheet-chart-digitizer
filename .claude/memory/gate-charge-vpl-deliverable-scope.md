---
name: Gate-charge Vpl deliverable is full curve + Vpl scalar
description: dsdig gate_charge_vpl extraction must deliver both the full Qg(VGS) curve_px provenance and the Vpl scalar; curve switching is RED even if Vpl scalar is correct
created: 2026-07-16T22:34:52.558Z
metadata:
  node_type: memory
  generator: opencode-claude-memory
  type: project
  originSessionId: ses_092eff4abffel1ufV5Av0RS0ge
---

For dsdig `gate_charge_vpl` extractions, the deliverable is the **full `curve_px` provenance plus the Vpl scalar**, not just the Vpl number. Any switching or deviation between physical source curves in the post-plateau region is RED, even if the scalar plateau value appears unchanged. (Clarified by codex-ee-8ae6 during Batch 02 gate-charge review on 2026-07-17.)

**Why:** Both reviewers flagged items where the blue extracted trace oscillated between V_DS/V_DD curves after the Miller plateau; the implementation owner confirmed the downstream consumer uses the full curve, so trace fidelity matters beyond the scalar.

**How to apply:** In viz-review gate-charge batch reviews, judge the extracted trace against the entire visible source curve, not just the plateau height. Flag as RED any trace that switches curves, overshoots to the plot boundary, or has severe sawtooth/oscillation in the post-plateau region.
