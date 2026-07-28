---
name: Chart Review Checklist
description: Use CHART-REVIEW-CHECKLIST.md in dsdig-verify-backlog as the fail-closed discipline for every dsdig chart review
created: 2026-07-17T08:45:03.273Z
metadata:
  node_type: memory
  generator: opencode-claude-memory
  type: project
  originSessionId: ses_090c1c1a8ffe35tLV0EAODs4IC
---

Use the `CHART-REVIEW-CHECKLIST.md` in `/Users/fab/dev/pv/ee/dsdig-verify-backlog/` as the governing checklist for every digitized MOSFET/diode chart review. The source-vs-extracted overlay is the gate; checks run top-to-bottom and fail-closed (unverified ≠ OK).

**Why:** The checklist was explicitly introduced in this session as the canonical review discipline; it codifies the ordering and depth that matter for detecting real extraction defects (panel identity, axis decade span, dual-axis binding, curve identity, trace single-valuedness, feature-region fidelity, neighbor-figure bleed, etc.).

**How to apply:** For each overlay batch, open the checklist first and verify every item against its sections: §0 fail-closed, §1 panel/chart type, §2 axes & scales (including decade span and dual-axis), §3 5× crosshair inspection, §4 curve/series identity (render source PDF when ambiguous), §5 single-physical-curve fidelity, §6 physical plausibility, §7 crop/provenance, then post item-specific GREEN/RED with concrete defects and an agent-review JSON. Never set `human_verified`.
