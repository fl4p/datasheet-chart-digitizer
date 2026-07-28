---
name: dsdig-human-verify-backlog
description: "Manufacturer-scale dsdig review queue uses strict READY/needs_annotation/gap states; numeric output alone never proves chart fidelity"
metadata:
  type: fact
---

The local human-review queue lives at `dsdig-verify-backlog/`. Each agent owns an
append-only `MANIFEST.<agent>.<manufacturer>.jsonl` plus its manufacturer subtree;
`SUMMARY.md` is an aggregate and all artifacts remain local/unpushed until Fab
reviews them.

State contract:

- `unverified` means READY for human review only when the overlay is
  self-verifying: every consumed tick has value+unit, crosshairs are centered on
  the calibrated physical ticks, and the source curves remain readable. Agents
  inspect 8x local crosshair crops internally per [[chart-overlay-tick-labels]],
  but those crops are scratch diagnostics and are not part of the human queue.
- `needs_annotation` means the numeric extraction looks faithful but the native
  overlay lacks that review contract. This is the normal state for Vpl and many
  capacitance/transfer outputs.
- `gap` means extraction refused, is partial, or failed visual fidelity. A
  returned array or empty error list (`[]`) is never success by itself: compare
  colored traces with the printed source curve, because frame/grid capture,
  wrong-panel caption binding, and partial curve coverage can all look
  numerically plausible.

Always build contact sheets before promotion, keep direct source crops, and
replace empty diagnostics with either a concrete visual-fidelity gap or an
explicit `needs_annotation` caveat. READY counts must exclude Vpl until its
renderer emits calibration metadata and exact crosshairs.
