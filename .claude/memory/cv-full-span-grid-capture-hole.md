---
name: cv-full-span-grid-capture-hole
description: The C(V) grid-capture guard measured its approach window inside the capture, so a trace BORN on a rule scored as perfect agreement; fixed with off-rule-only approach + abandoned-stroke fallback
metadata:
  type: project
---

Found 2026-08-08 on GT020N10T, whose Crss was the plot's bottom FRAME for all
495 columns while `trace_validation_status` said `pass`.

**The hole.** `capacitance_source_support._approach_deviation_px` asked "how far
is the rule from where this trace was heading?" over a +-14 px window around the
captured run. When the trace rides rule ink across its whole span, every sample
in that window is ALSO on the rule, so the extrapolation lands on the rule and
the deviation measures 2.0-4.1 px -- under the 5.0 px floor, hence clean. The
discriminator got weaker as the capture got worse and went silent at the far
tail. It also returned `0.0` when no window existed at all, conflating
"unmeasurable" with "agrees perfectly".

**Why nothing else caught it.** `y_range_px = 3` missed the flat-span gate's
`<= 1`. `_rule_evidence_outside_trace` cannot help by construction: a fully
captured trace covers the rule row everywhere, leaving no trace-free column to
evidence the rule from. Anchor agreement is structurally blind here -- 110 pF on
a 38.7 pF/px linear axis is 2.84 px, so a trace lying ON the axis read -9.0 %.
Only the exporter's `crss_below_axis_resolution` stopped it, and that gate keys
on the ANCHOR's size, not on the trace: a larger anchor would have shipped it.

**The fix.** Approach samples are taken only from columns where the trace is NOT
on ANY candidate rule row (a frame stroke is 4-6 px thick, so excluding just the
row under study is not enough); with none, the function returns `None`. `None`
is then decided on what the trace LEFT BEHIND -- columns holding curve ink (rule
rows excluded) that no served trace claims. Separation is wide: legitimate flat
traces coinciding with a rule scored 0.00-0.03 (PSMN1R4-100ASE, PSMN1R0-100ASF,
IAUTN12S5N018TATMA1, AONS66916, CRST030N10N), real captures 0.38 (XP10N3R8P
Ciss) and 0.99-1.00 (GT020N10T, GT023N10T). Floor 0.25.

**Attribution matters.** Only columns where all three traces are served count:
GT045N10D5's Coss stops at 57 V and leaves its own tail unclaimed, which first
put a capture label on the one trace that was correct. Those columns belong to
the peer-span gates.

`undecidable_runs` was reported by the diagnostics and consumed by nobody, so an
UNDECIDABLE capture check read as a clean one; `capacitance_validation` now
emits `{name}_grid_rule_capture_undecidable`.

**Still open:** the trace itself is not repaired. GT020N10T's panel has FOUR
strokes per column (Ciss, Coss, real Crss, frame) and the band finder takes the
lowest three, so the frame wins and the real Crss is dropped. The overlay looks
identical after this fix; what changed is that the tool now says so and the
panel is rejected. Fixing the band assignment is a separate slice.
