# AGENTS.md — datasheet-chart-digitizer

## Persistent project memory (read this first)

Durable knowledge about this project lives in `.claude/memory/`. It is checked into
this repo and shared bidirectionally with Claude Code, whose memory directory
(`~/.claude/projects/<hashed-path>/memory/`) is a **symlink to `.claude/memory/`**.
Edits you make here are Claude Code's memory, and vice versa.

### Recall — at the start of every session

1. Read `.claude/memory/MEMORY.md`. It is the index: one line per note,
   `- [Title](<name>.md) — hook`. Links are relative to `.claude/memory/`.
2. Open the individual `.claude/memory/<name>.md` notes whose hooks touch the task
   at hand. Do not work from the hook alone — the hook is a pointer, the note is
   the fact.
3. Follow `[[name]]` cross-references inside a note to `.claude/memory/<name>.md`.

Keyword recall when the hooks are ambiguous:

```bash
rg -il '<term>' .claude/memory/          # which notes mention it
rg -n '<term>' .claude/memory/MEMORY.md  # index hooks only
```

A memory records what was true **when it was written**. If a note names a file,
function, flag or threshold, verify it still exists at HEAD before acting on it —
several notes predate refactors.

### Write-back — when you learn something durable

One fact per file, kebab-case slug, frontmatter:

```markdown
---
name: <short-kebab-case-slug>
description: <one-line summary — this is what recall matches against>
metadata:
  type: user | feedback | project | reference
---

<the fact; for feedback/project follow with **Why:** and **How to apply:** lines.
Link related notes with [[their-name]].>
```

- `user` — who Fab is (role, expertise, preferences).
- `feedback` — guidance on how to work, corrections and confirmed approaches;
  always include the *why*.
- `project` — ongoing work, goals, constraints not derivable from code or git
  history. Convert relative dates to absolute.
- `reference` — pointers to external resources (URLs, dashboards, issues).

Rules:

- After writing the note, append its one-line pointer to `.claude/memory/MEMORY.md`.
  `MEMORY.md` is an index only — never put memory content in it.
- Check for an existing note that already covers the fact and **update that file**
  rather than adding a near-duplicate. Delete notes that turn out to be wrong.
- Do not record what the repo already states (code structure, past fixes, git
  history) or what only matters inside one conversation.
- `MEMORY` is reserved — never name a note `MEMORY.md`.

## Instructions that live outside this git root

Codex stops at the git root, so these are **not** auto-loaded here — read them when
the task involves chart digitization or extraction review:

- `../CLAUDE.md` — pv/ee project rules: use `dsdig` (never hand-roll PDF/raster
  extraction); the dsdig review checklist at
  `../dsdig-verify-backlog/CHART-REVIEW-CHECKLIST.md`; agent review **never** sets
  `human_verified`; settle curve-identity ambiguity by rendering the source PDF
  (`pdftoppm -png -r 200 -f <page> -l <page> <part>.pdf <out>`).
- `../AGENTS.md` — coding guidelines: max 1500 lines/file, no copy-paste code
  (migrate reusable code to a shared location), no commit over 1000 lines unless
  it is a single document.

## This repo

Python library + `dsdig` CLI, own `.venv`. Tests in `tests/`, one-off and
regression harnesses in `tools/`.
