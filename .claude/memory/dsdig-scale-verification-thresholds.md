---
name: dsdig scale verification thresholds
description: dsdig scale verified/unverified/FAIL thresholds and residual units clarified during AO RR batch review
created: 2026-07-16T21:29:31.769Z
metadata:
  node_type: memory
  generator: opencode-claude-memory
  type: project
  originSessionId: ses_0932c1368ffeJDytYEvO9IpSYb
---

dsdig chart-digitizer scale-verification rules (clarified during AO reverse-recovery batch-01 review on 2026-07-16; see `/Users/fab/dev/pv/ee/dsdig-verify-backlog/ao/*/digitized/reverse_recovery/fig*/values.json` and review HTML `/Users/fab/dev/pv/ee/dsdig-verify-backlog/review-html/ao-reverse-recovery-batch-01/ao-reverse-recovery-batch-01-001.html`):
- `scale: verified` requires at least one independent table/cross-panel anchor, complete curve identities, no integrity warning, and all |relative error| ≤ SCALE_TOL=25%.
- `scale: unverified` means no applicable independent anchor.
- `scale: FAIL` means any anchor >25% relative error or any integrity/axis-side warning.
- Axis `residual` is reported in native value units (e.g., 0.324 A/μs on a 0..1000 A/μs span), not pixels.
**Why:** Reviewers need to interpret `values.json` scale/residual fields consistently and must not override manifest FAIL/unverified verdicts.
**How to apply:** In viz-review MOSFET chart reviews, treat manifest `scale=FAIL` or `scale=unverified` as RED; only `scale=verified` can be GREEN unless the overlay itself shows a separate visual defect (e.g., duplicated tick labels).
