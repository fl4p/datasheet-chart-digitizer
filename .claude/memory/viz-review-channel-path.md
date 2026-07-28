---
name: viz-review shared channel path
description: Location of the shared viz-review channel file used by agent reviewers
created: 2026-07-17T09:16:23.310Z
metadata:
  node_type: memory
  generator: opencode-claude-memory
  type: reference
  originSessionId: ses_090a521d2fferbVB2brKNZQzE1
---

The `viz-review` shared channel for dsdig chart-review coordination is the line-delimited JSON file at `/tmp/claude-channels/viz-review.ndjson`. Post review verdicts and status messages by appending JSON lines to this file; read the tail for new activity from peer reviewers and the implementation owner.
