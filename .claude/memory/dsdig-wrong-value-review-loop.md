---
name: dsdig active review loop for wrong-value re-digitizations
description: The dsdig shared channel is now the active review lane for the 23 wrong-value dsdig extractions from the 4-agent sweep, with a two-lane independent-review protocol.
created: 2026-07-17T09:48:41.990Z
metadata:
  node_type: memory
  generator: opencode-claude-memory
  type: project
  originSessionId: ses_090868c57ffeODxndTNV3nLbA3
---

The dsdig shared channel (`/tmp/claude-channels/dsdig.ndjson`) is the active re-digitization review loop for the 23 wrong-value dsdig extractions catalogued in `dsdig-verify-backlog/agent-sweep-reports/WORKLIST-wrong-value.md`. After the viz-review autopilot closed on 2026-07-16, remaining wrong-value items are being fixed by the implementation owner and reviewed independently by two agent lanes before Fab human-verifies.

**Why:** The wrong-value worklist (class-A through class-G) requires coordinated fix → review → human-verify cycles; the viz-review autopilot queue is finished, so dsdig became the working lane for this cleanup.

**How to apply:** When participating on dsdig, inspect the posted overlay, values.json, provenance.json, and source PDF first, then post your own item-wise GREEN/RED with concrete defects **before** reading the other lane's verdict. A packet clears only when both lanes agree GREEN. Always gate on `dsdig-verify-backlog/CHART-REVIEW-CHECKLIST.md`; agent review never sets `human_verified`.
