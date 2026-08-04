---
name: dsdig-worktree-pytest-imports-main-tree
description: "dsdig .venv is an editable install pinned to the main checkout, so pytest in a git worktree silently tests the MAIN tree unless PYTHONPATH is set"
metadata:
  node_type: memory
  type: reference
  originSessionId: 5698d8f9-5a82-419b-a9e6-7324f1b09db0
  modified: 2026-07-29T11:00:01.234Z
---

`datasheet-chart-digitizer/.venv` holds an **editable** install
(`__editable__.datasheet_chart_digitizer-0.1.0.pth`) pinned to
`/Users/fab/dev/pv/ee/datasheet-chart-digitizer/src`. Any `pytest` run from a
`git worktree` that shares that venv therefore imports the **main checkout's**
source, not the worktree's — silently, with no error.

**How to get a real baseline:** `PYTHONPATH=<worktree>/src .venv/bin/python -m
pytest …` (verified to take precedence over the `.pth` entry), or set
`sys.path.insert(0, '<worktree>/src')` inside the driver script.

**Why it matters:** it makes "does this failure pre-exist at clean HEAD?" answer
wrongly. On 2026-07-29 it produced a reading of 3 pre-existing suite failures
when the true clean-HEAD baseline was 5. A/B drivers that insert `sys.path`
explicitly are unaffected — only plain `pytest`-in-a-worktree is.

Related: [[dsdig-gate-collateral-env-drift]] (same-host A/B is the only causal
test), [[dsdig-full-corpus-authoritative-harness]].
