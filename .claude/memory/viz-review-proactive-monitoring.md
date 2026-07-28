---
name: Proactive viz-review channel monitoring
description: In viz-review sessions, keep watching the channel and review incoming batches as they arrive rather than waiting for a per-batch prompt
created: 2026-07-17T09:16:22.173Z
metadata:
  node_type: memory
  generator: opencode-claude-memory
  type: feedback
  originSessionId: ses_090a521d2fferbVB2brKNZQzE1
---

In viz-review chart-review sessions, actively monitor the channel and review incoming batches as they arrive instead of waiting for a separate prompt each time.

**Why:** User said explicitly, "keep watching channel dsdig and help with the reviews" — they want the agent to stay engaged and not drop out between batches (this session resumed after a prior drop-out where later batches stayed one_green_pending_second).

**How to apply:** When in a viz-review session, keep the channel watcher active, acknowledge new batch posts promptly, and begin independent review immediately. Post itemized GREEN/RED verdicts and write the per-packet agent-review JSON as each batch lands.
