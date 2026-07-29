---
name: dsdig-orphaned-lane-salvage
description: "Before committing an abandoned agent's working-tree file, diff it against HEAD hunk by hunk — it may silently revert landed work"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: bee5643e-f421-442b-9742-8e9701d3c45b
  modified: 2026-07-29T06:53:09.546Z
---

When another agent's lane dies mid-flight and Fab asks whether its uncommitted work is
committable, never commit the file wholesale. Diff it against HEAD and classify EVERY
hunk as new-work / revert / unrelated. On 2026-07-29 the orphaned `capacitance_axis.py`
contained one genuinely valuable change (rect/quad grid rules + a vector-only 7 px
assignment window, which made six EPC panels trusted) plus two SILENT REVERTS of already
-landed improvements: the OCR ladder's duplicate-digit contradiction check with its
`plausible_anchors` range filter, and a widened rotated-title OCR strip. It also carried
zero tests.

**Why:** a dead lane's file is a snapshot of an arbitrary moment, not a reviewed patch;
mine was partly self-inflicted (I restored that file from a scratchpad copy during A/B
runs and clobbered refinements made after my own commit). Committing it would have
regressed guards while appearing to add a feature.

**How to apply:** reconstruct as `HEAD + only the new-work hunks` (splice
programmatically and `ast.parse` the result), write the missing tests before committing,
re-run the packet A/B for regressions, and say in the commit message which hunks were
dropped and why. Beware writing scratchpad copies back over shared files at all — prefer
an isolated worktree ([[dsdig-collateral-acceptance-discipline]]).
