---
name: dsdig-review-packet-staleness
description: "Review packets are built with the dsdig version of their run date — reproduce each flagged item on the CURRENT tree before fixing, and attribute fixes committed-vs-in-flight"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: bee5643e-f421-442b-9742-8e9701d3c45b
  modified: 2026-07-28T15:43:52.068Z
---

Fixing feedback from a review packet (e.g. `top50-fugu2-ls-missing-curves-001.review.json`,
built 2026-07-28 in `pwr-mosfet-lib/out/fugu2-100v-LS1p/coss-review-top50-2026-07-28/`)
must start by re-running the current `dsdig find` on every flagged part: 9 of the 10
human-flagged items were already fixed by finder commits that landed after the packet's
extraction run; only STP310N10F7 still reproduced and needed new code (db361e8).

**Why:** the packet's `charts.json`/gap-crops freeze the behaviour of an older tree; "fixing"
already-fixed items wastes work and risks regressing the newer logic.

**How to apply:** for each flagged item, run the current finder on the part's PDF and compare
against the packet's recorded panel before writing any fix. When something IS still broken,
also run the same repro from a clean-HEAD worktree (`git worktree add … HEAD`, `PYTHONPATH=<wt>/src`)
to attribute it committed-vs-in-flight-uncommitted, since the dsdig tree usually carries another
agent's unfinished slices ([[dsdig-base-drift-local-main]]). A/B-validate shared-guard changes
back-to-back on the packet's own PDF list ([[dsdig-collateral-acceptance-discipline]]) — and
build that PDF list carefully: a missing trailing newline before `>>` merged two paths and
silently dropped both parts from the first A/B sweep.
