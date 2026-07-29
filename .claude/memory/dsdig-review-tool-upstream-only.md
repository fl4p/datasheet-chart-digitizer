---
name: dsdig-review-tool-upstream-only
description: Every change Fab asks for in the review tooling goes upstream permanently — never a packet-local copy
metadata: 
  node_type: memory
  type: feedback
  originSessionId: bee5643e-f421-442b-9742-8e9701d3c45b
  modified: 2026-07-29T07:19:37.901Z
---

Changes to the review tooling (`dsdig-verify-backlog/tools/build_html_review_packets.py`
and its siblings) must be made and committed IN THAT REPO. Never copy the tool into a
packet output dir as a `tools-local/` variant, and never ship a packet built from such a
copy. Fab stated this on 2026-07-29 after I had twice patched a local copy (overlay-less
gap cards, then curves-drawn-above-the-detected-grid); both were upstreamed as `a315349`
and the local copies deleted.

**Why:** a packet-local fork means the next packet silently regresses to the old
behaviour, and the improvement is invisible to every other lane. The review artifact is
the gate ([[chart-review-checklist]]), so its generator is production code, not
packet scaffolding.

**How to apply:** edit `dsdig-verify-backlog/tools/…`, smoke-test it (note `--output-dir`
must be under `--root`), commit in that repo, then rebuild packets by invoking the
canonical path. If a change seems packet-specific, it is almost certainly still a general
improvement — generalize the comment and land it.
