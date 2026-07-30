---
name: dsdig Vpl stdout refusal fixes
description: stdout Vpl audits fixed the original seven plus GSFT7R515; HY1915P axis OCR and XPQ1R00AQB curve identity remain independent REDs
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

The 2026-07-29 `out/stdoe_loss` audit found three unique out-of-range
gate-charge results. GSFT7R515's 1.4 V was an adjacent-panel ownership error:
its unreadable 0..10 V raster labels let Figure 4's 0.4..2.4 axis, 79 pt beyond
the gate-chart frame, calibrate the correct trace. Limiting local y-axis
columns to 48 pt and extending the narrowly triggered bounded bottom OCR band
recovers the printed 0/6/10 V axis and serves 5.4801 V. The authoritative
same-host sequential 304-PDF A/B is byte-identical.

Two independent REDs remain. HY1915P traces the correct curve but calibrates
it from false 25 V and 7 V OCR anchors, serving 10.2044 V instead of the
source's roughly 5.5 V plateau. XPQ1R00AQB correctly calibrates the Toshiba
right-side 0..10 V VGS axis but follows a VDS branch and then condition text,
serving 1.3386 V instead of the roughly 5.5 V plateau.

**Why:** all three values look numerically precise and self-report `status=ok`,
yet source renders and full overlays prove axis or curve ownership is wrong.

**How to apply:** keep HY1915P as an axis-OCR ownership case and XPQ1R00AQB as
a dual-y curve-identity case; do not treat either as collateral from the
GSFT7R515 neighbor-column fix. Agent review does not set `human_verified`.
