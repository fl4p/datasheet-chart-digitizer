---
name: viz-review-own-overlay-pass-first
description: "Never hand Fab a review batch on the automation's trace_validation label; run your own overlay pass first and present YOUR verdict"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: b8a11ad6-b8c5-4093-9f87-a79c119074e1
  modified: 2026-07-20T20:17:06.464Z
---

In batch 26 (fresh authorized-5 capacitance) I presented 25 extractions labeled "agent-unverified"
and leaned on `trace_validation=pass` instead of looking at the overlays. Fab immediately found 3
Coss-snap / Crss defects; asked "how did you miss these?" I hadn't reviewed them — I forwarded the
automation's self-report.

**Why:** `trace_validation=pass` is the anti-monotone false-PASS (see the guard checklist). For
capacitance it passes Coss→Ciss snap-through even while the overlay renders the pipeline's own
"Ciss=Coss shared" annotation. Computable signals (point-count, bbox truncation, shared_collapse
spans) are necessary but NOT sufficient — they gave 6 false positives (legit low-V convergence) and
MISSED IPB160N04S2L's Crss-below-1V error entirely (full point count, visual-only). The overlay look
is the gate.

**How to apply:** As the review/A-B lane, before handing Fab any batch, do the microscopic overlay
pass MYSELF and present my per-card verdict (clean vs FLAG+reason), so he spot-checks me rather than
doing the review. Keep [[crossing-approach-snap-check]] front-of-mind for Ciss/Coss/Crss. human_verified
stays false regardless. Artifact export must use copy-paste (sandbox blocks downloads). See
[[dsdig-trace-fidelity-visual-gate]], [[chart-review-checklist]].

**Batch-27 update — check BOX/FRAME COVERAGE, not just in-box tracking.** I reviewed all 18 myself
(good — caught FDMS2572 Crss truncation; source-verified FDMS86202ET120 clean via raw crop). But Fab
still flagged one I passed: IPD50N10S3L-16 — the orange plot box was drawn too narrow on the RIGHT
(ended ~85V while the real frame + labels + 100V tick sat OUTSIDE the box), truncating all three
curves ~15V short. I checked identity/tracking INSIDE the box but never asked "does the box capture the
full chart frame?". Add to the pass: verify the crop box reaches every real frame edge — look for black
curve stubs / axis ticks / curve labels sitting OUTSIDE the orange box (cf. [[dsdig-capacitance-closed-bottom-frame]]).
Calibration from Fab: high-V curve UNDER-COVERAGE is a FLAG whether it comes from box crop
(IPD50N10S3L-16, all curves cut ~15V short) or trace fade-out (FDMS2572, Crss ~9% tail). Fab first
GREEN'd FDMS2572 then reconsidered and flagged it — "tail truncation, you are right." So my original
flag was correct; do NOT treat a Crss tail truncation as acceptable-minor. Batch-27 final: 16 GREEN, 2 flagged.

**Batch-28 update — MANDATORY microscopic crossing check (I missed one again).** I passed STB80N20M5
as clean; Fab flagged "Coss looses track". Zoom showed Coss CUTS BELOW the true black Coss curve THROUGH
the Ciss crossing (corner-cutting) then rejoins — a subtler snap that does NOT show in coverage signals
(Coss reaches full x-span; it's a mid-span deviation). Lesson: at EVERY Coss/Ciss crossing, zoom in and
verify Coss follows its own black curve faithfully through the corner — a coarse "it drops through and
continues" is not enough ([[crossing-approach-snap-check]]). Coverage signal catches truncation snaps
(Coss ends early); the microscopic look catches loses-track snaps (Coss deviates mid-span). Also: Fab
wants review overlays rendered WITHOUT the thick yellow/orange plot-box frame (it obscures edge features
like the 10000pF crosshair) — render frameless (thin outline or none). Batch-28 final: 21 GREEN, 6 not-clean;
Coss-snap on sharp-cliff SJ/FDMS parts is the dominant capacitance defect across batches 26-28.

**Overlay format (batch-28/29 clarified): DO NOT go frameless / drop crosshairs.** The `+` axis tick
crosshairs are the axis-calibration review gate (Fab caught STL110N8F8's 10000pF crosshair off with them).
Keep dsdig's framed overlay with crosshairs + curves; the only wanted change is thinning the heavy plot-box
BORDER so it doesn't overrun/obscure the edge crosshairs. Fab: "leave everything as-is, just make sure the
chart border you draw doesn't interfere with detection/review."

**Batch-29 update — check BOTH edges; my checks were right-biased.** Passed IPD30N06S2-23 and FDB0190N807L;
Fab flagged both as LEFT-edge (low-V) truncations — a class distinct from right/mid truncation and the snap.
IPD (linear 0-30V): all 3 curves rise near-vertically at x->0 but digitizer stops where the steep rise begins,
missing the low-V high-C portion. FDB (log): Ciss starts ~0.15V vs the 0.1V frame (differential, one trace short).
My right-edge coverage signal is blind to this, AND my left-signal literally returned "?" on linear axes
(log10(0) undefined = fail-to-flag, the anti-monotone gap). Lessons: (a) visually check the LEFT frame edge
for uncovered black curve stubs / clipped steep low-V rises, not just the right; (b) any coverage signal must
handle linear axes (frac from xmin, never log10(0)) and flag DIFFERENTIAL starts/ends (one trace shorter than
the others), not just an absolute threshold. Recurring meta-pattern: I keep UNDER-flagging edge/coverage defects
while catching identity/snap — bias toward flagging borderline edge cases.

**Recurring specific miss — STEEP LOW-V RISE at x→0 (linear axes).** Missed it 3x now (IPD30N06S2-23,
BSC320N20NS3_G, and nearly others): on a linear VDS axis the black curves rise near-vertically right at x→0,
and the digitizer starts the colored trace AFTER that rise, leaving a black near-vertical stub uncovered. The
L-coverage signal CAN'T catch it (the missed x-extent is ~2% of span, under any sane threshold) — it is
visual-only. On EVERY linear-axis cap chart, look at x→0 for a black stub going up above where the colored
curve begins. Calibration from Fab (batch 34): a lone ~7% Coss RIGHT-trunc at the natural data end is
acceptable/GREEN (he GREEN'd TK7R2E15Q5 over my flag); but a steep-low-V-rise LEFT miss is a FLAG. When
human-reviewing my loop batches, agreement has been near-perfect EXCEPT this steep-rise class.

**Montage misses subtle IDENTITY errors — zoom every 'Ciss=Coss shared' + faint/gray trace.** Batch 36:
Fab rework'd ti/CSD17575Q3 which I passed in the montage. Source legend drew Coss=black, Crss=GRAY (faint);
dsdig put its green Crss label on the BLACK Coss curve, never digitized the faint GRAY real Crss, and blue
Coss snapped onto the legend-box border. The montage resolution hid the gray curve and I hadn't zoomed a
'Ciss=Coss shared' TI chart (I'd only been zooming FDMS/FDBL shared ones). Rule: the montage is ONLY for
gross checks; ALWAYS full-res-zoom any chart with (a) a 'Ciss=Coss shared' annotation, (b) a faint/gray/
light-colored source trace, or (c) a source legend with non-standard trace colors — regardless of vendor.
A faint gray Crss is a trap: dsdig can miss it entirely and mis-assign its label to Coss.
