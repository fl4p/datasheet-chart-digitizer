---
name: dsdig-collateral-acceptance-discipline
description: Accepting a dsdig extractor code change needs a FULL-corpus re-extract with the authoritative selection tool + box-ownership check on every box delta — not a bounded sample or a substituted method
metadata:
  node_type: memory
  type: feedback
  originSessionId: b8a11ad6-b8c5-4093-9f87-a79c119074e1
---

When accepting a change to shared/data-dependent dsdig extractor code (e.g. the
gate-charge frame/box-terminus logic), a bounded sample or a home-rolled re-run does NOT
prove the change is regression-free. Blast radius is not bounded by the size of the code diff
— a one-function `_aligned_frame_improves_axis_binding` change touched 10 parts across 304,
7 of them beyond the 3 intended fixes.

**Why (three real misses this session, all caught by codex-ee-8ae6, the implementer):**
1. I called a collateral gate "cleared" on a 3-part bounded sample. A data-dependent function
   has no bounded blast radius; you must re-extract the WHOLE corpus.
2. My full-corpus re-run used `sorted(res, _result_sort_key)[0]` for result selection, but the
   authoritative baseline + CLI use USABLE-FIRST (`tools/run_gate_charge_collateral.py::
   _selected_result`: first result with vpl!=None, since `digitize_gate_charge` already returns
   sort-key order; else min by `_review_overlay_key`). Non-comparable selection = apples-to-oranges
   diff on exactly the fail-closed parts. Conclusions invalid.
3. Both review lanes classified box-changed items as "neutral" by checking the unchanged Vpl,
   not the box — missing 3 box-ownership regressions (a box grown into a neighbor panel /
   whitespace). See [[dsdig-sweep-green-axis-integrity-retro]] and CHART-REVIEW-CHECKLIST §3.

**How to apply:** To accept a gate-charge extractor change: (a) run the AUTHORITATIVE tool
`dsdig-verify-backlog/tools/run_gate_charge_collateral.py --pdf-list <authoritative 304 list>
--dpi 220` at the new source, producing a tool-format `machine.json`; (b) diff vs the stored
baselines in the SAME tool format (`/private/tmp/gate-collateral-head-38cd-v1/machine.json` =
pushed, `.../pre-8f67.../machine.json` = pre-finder) — the load-bearing gate is vs the pushed
head; (c) §3/§7-inspect EVERY status/vpl/plot_box delta, checking box ownership even when the
value is unchanged; (d) distinguish fail-closed/no-value deltas (non-blocking) from
value-serving regressions. Use the authoritative pdf list + tool + selection, never a substitute.
The implementer adversarially auditing the reviewer's method is the two-lane process working — welcome it.
