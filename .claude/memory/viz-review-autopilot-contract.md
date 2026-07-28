---
name: viz-review autopilot chart-review contract
description: viz-review channel is the review lane for Fab's 7-hour autopilot: 25-overlay batches, dual-agent GREEN/RED contract, source-curve overlay required, agent review never marks human_verified
created: 2026-07-16T20:59:01.020Z
metadata:
  node_type: memory
  generator: opencode-claude-memory
  type: project
  originSessionId: ses_09346bf7affeHOsXITwjsD5lF4
---

viz-review channel is the review lane for Fab's 7-hour autopilot. The implementation owner (codex-ee-8ae6) submits 25-overlay batches. Review contract per overlay: source-curve fidelity (source-vs-extracted point overlay REQUIRED — guards+tick-centering alone do not prove fidelity), plot-box extent, axis/tick values and units, crosshair centering (8x local crops are internal-only), curve identity/completeness, annotation legibility. Each item gets a concrete GREEN or RED defect. Agent review NEVER marks `human_verified`. Any RED is fixed and resubmitted until both reviewers clear the batch.
**Why:** This is the live coordination contract the two reviewer agents and the implementation owner agreed on in the channel.
**How to apply:** When asked to review chart overlays on viz-review, apply this checklist, return item-specific GREEN/RED with concrete defect, and never mark `human_verified` yourself. Related: [[dsdig-trace-fidelity-visual-gate]], [[dsdig-human-verify-backlog]], [[chart-overlay-tick-labels]].

**2026-07-16 session outcome (opus second lane, batches 01–23):** completed; canonical human queue = 697 cards / 28 packets; handoff at `dsdig-verify-backlog/AUTOPILOT-2026-07-16.md`. Kimi (codex-ee-q1oe) went unresponsive after batch 02 → later packets stay `one_green_pending_second`. Recurring **defect taxonomy** worth checking first on any dsdig chart review (all found+fixed this run): (1) **axis DECADE / dual-axis miscalibration** — capacitance Y read as 1 decade when it's 3–4 (fail-close <3 y-decade anchors); Renesas "Dynamic Input Characteristics" charts have VDS-left + VGS-right dual axes and the gate-charge extractor bound VGS to the wrong axis (Vpl ~3× high) → fail-close `unsupported_axis`. (2) **temperature-label inversion** on transfer curves (see [[transfer-temp-assignment-check]]). (3) **curve/branch switching** — trace oscillates between VDS/VDD branches or has a top-edge W-switchback (needs terminal-rise trim). (4) **low-confidence trace not flat-seated** — raster Vpl on a rising segment; negative/low score corroborates. (5) **crop bleeds** — neighbor figure titles / adjacent axes (Ct, BVDSS, "Normalized", "Figure N ...") in a too-wide/tall crop. Method that repeatedly resolved ambiguity: **render the source datasheet PDF** (`pdftoppm -png -r 200 -f <pg> -l <pg>`) when the overlay alone can't settle curve identity/axis/temperature.
