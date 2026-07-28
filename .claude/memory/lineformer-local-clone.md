---
name: LineFormer clone for chart line extraction
description: Local LineFormer clone at /Users/fab/dev/pv/LineFormer for instance-segmenting crossing/overlapping chart lines.
created: 2026-07-08T07:54:07.572Z
metadata:
  node_type: memory
  generator: opencode-claude-memory
  type: reference
  originSessionId: ses_0bf490835fferr8VE6J12OPQ3k
---

A local clone of LineFormer, a transformer-based line-chart data extractor, lives at `/Users/fab/dev/pv/LineFormer`. It is specifically trained to separate crossing/overlapping same-style lines by producing instance masks, so it can serve as either a second opinion or a primary branch extractor for the chart digitizer.

**Why:** It can handle the hard Ciss/Coss crossing case in MOSFET datasheet plots where the curves are visually black solid strokes and classical geometric tracers struggle.

**How to apply:** Run it in parallel with the geometric tracer on difficult C(V) pages, use its masks as an alternative branch input, and diff the two outputs. Pages where the two methods disagree become manual-review candidates or labeled training/eval samples.
