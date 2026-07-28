---
name: dsdig gate-charge vector extraction fix
description: dsdig gate_charge_vpl vector extraction fixed curve switching by using a single branch, no median blend, and trimming after the first upper-axis reach
created: 2026-07-16T22:57:31.866Z
metadata:
  node_type: memory
  generator: opencode-claude-memory
  type: project
  originSessionId: ses_092db6e79ffemd3NTIZ4qMZEjo
---

dsdig gate_charge_vpl vector extraction was fixed to satisfy the full-curve + Vpl deliverable. The fix: follow ONE branch (no median blend across branches) and trim the trace after the first highest consumed tick, preventing post-plateau jumps between V_DS/V_DD curves and top-edge switchbacks. Negative-score raster candidates (e.g., FDB045AN08A0, score ~-2.1) are fail-closed and excluded rather than promoted. IRF644 terminal gridline tail was also fail-closed.

**Why:** The deliverable is both the full Qg(VGS) curve_px provenance and the Vpl scalar, so any trace switching or deviation from the source curve is RED even if the plateau scalar is unchanged.

**How to apply:** When reviewing future gate_charge_vpl batches, verify the blue trace stays on a single source branch through the entire curve and ends cleanly at the first upper-axis consumption; flag any branching, switchbacks, or raster traces with negative confidence scores.
