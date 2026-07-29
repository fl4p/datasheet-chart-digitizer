---
name: codex-instruction-loading-git-root
description: "Codex stops at the git root when loading AGENTS.md, so pv/ee instructions are invisible inside sub-repos; verify prompt loading with `codex debug prompt-input`"
metadata: 
  node_type: memory
  type: reference
  originSessionId: d7eee6ba-e386-43f4-93ec-ffbbb52bec3f
  modified: 2026-07-29T08:16:54.593Z
---

Codex assembles its instruction context from `~/.codex/AGENTS.md` plus `AGENTS.md`
files walked up **only as far as the git root**. `datasheet-chart-digitizer` is its
own git repo, so `/Users/fab/dev/pv/ee/AGENTS.md` and `../CLAUDE.md` are NOT loaded
when Codex works inside it — unlike Claude Code, which walks past the git root.
Fixed 2026-07-29 by committing a repo-root `AGENTS.md` (`e8fa5e8`) that carries the
memory recall/write-back protocol and names the parent files explicitly.

**Verification without spending an API call:**

```bash
codex debug prompt-input   # renders the model-visible prompt as JSON, no model call
```

Grep the JSON for a distinctive phrase from the instruction file. Calibrate it as a
real check by moving the file away and confirming the phrase goes MISSING, then
restoring it — presence alone doesn't prove *which* file supplied it (see
[[dsdig-review-tool-upstream-only]] for the same upstream-not-local discipline).

**How to apply:** any per-repo agent instruction under `pv/ee/` that must reach Codex
belongs in that repo's own root `AGENTS.md`, not only in the `pv/ee` parent. The
memory store itself is fine — `.claude/memory/` is git-tracked in the repo and
Claude Code's `~/.claude/projects/<hash>/memory` is a symlink into it, so both agents
read and write one shared store.
