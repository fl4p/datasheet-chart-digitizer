---
name: dsdig Vpl stdout refusal fixes
description: 7 gate-charge refusals from pwr-mosfet-lib stdout fixed via 6 mechanisms; landed as the 4-commit gate-charge lane; full-corpus A/B still pending
created: 2026-07-29T05:25:08.725Z
metadata:
  node_type: memory
  generator: opencode-claude-memory
  type: project
  originSessionId: 019fa9fe-40ca-766c-9385-f0ac468592d8
---

The 2026-07-28 slice fixed every gate-charge digitizer refusal in
`pwr-mosfet-lib/out/stdout.txt`, all overlay-verified against the source charts:
NCE0160G 5.16, NCEP25N10AK 3.20, SP010N07AGTQ/AGNK 3.92, IRFP4127PBF 4.48,
TPH5R60APL 3.74, plus TPH1500CNH 5.93. All are in
`test_gate_charge_bounded_axis_recovery.py` (25 subtests green).
IRFP4127PBF page-7 `gate_charge_definition` rejection is correct and kept.
IPT014N10N5ATMA1's "Vpl field out of range" is a dslib table-parser warning;
the digitizer serves 4.43 V, superseding it.

Mechanisms (see the "gate charge: consume printed axis evidence" commit):
recalibrate side-grouped column windows + sub-tick extrapolation slack;
leave-one-out `_drop_glyph_offset_tick` for endpoint labels printed off their
axis row; y-axis side chosen by evidenced x-tick ownership, not bbox
proximity; OCR duplicate-fragment dedupe ('10'+'10' -> '1010' guard); Toshiba
dual-y bands widened to the true right frame; value-aware column-split
retention; finder sibling-layout VETO for directionless gate-charge captions
(selector-style application regressed HXY Fig.4 - veto-only is validated);
plateau-bridge guard `left[0] >= 0.12*width` (BSP135 depletion restored).

**Why:** each refused chart had readable printed axes that a stale/wrong
first-pass plot box prevented from being consumed; refusals were correct
fail-closed behavior, the fix is consuming the evidence, not relaxing gates.

**How to apply:** landed as a 4-commit lane (OCR primitives -> gate charge ->
finder -> memory) verified in an isolated worktree closure; the other agent's
capacitance/transfer/SUP-EPC files remain uncommitted on the shared tree and
their `test_sup_epc_supported_chart_recovery.py` still needs their src to
pass. Full-corpus A/B via the authoritative harness is still required before
final acceptance per [[dsdig-collateral-acceptance-discipline]].
