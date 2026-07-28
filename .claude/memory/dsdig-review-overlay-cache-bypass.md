---
name: dsdig review overlay cache-bypass naming
description: viz-review updated dsdig overlays use unique suffix paths like overlay.review-v2.webp; base overlay.webp can return stale cached image content
created: 2026-07-16T21:55:08.499Z
metadata:
  node_type: memory
  generator: opencode-claude-memory
  type: project
  originSessionId: ses_093145321ffe2qBCDiPZxu6B8G
---

When an updated dsdig review overlay is re-submitted in viz-review, the implementation agent writes it to a unique path such as `overlay.review-v2.webp` and preserves the original as `overlay.full.webp`. Re-reading the base `overlay.webp` path can return stale cached image content.
**Why:** During Batch 01 v2 re-reviews, the read tool / image display pipeline appeared to cache by filename, so in-place overwrites of `overlay.webp` were not reliably reflected in subsequent reads. The agent had to redirect me to `overlay.review-v2.webp` to see the fixed crop.
**How to apply:** Always open the exact unique path the implementation agent names for re-review (e.g., `dsdig-verify-backlog/<mfr>/<part>/digitized/<chart>/overlay.review-v2.webp`). If the content looks inconsistent with the stated fix, verify the file size or SHA-256 and confirm the unique path was used.
