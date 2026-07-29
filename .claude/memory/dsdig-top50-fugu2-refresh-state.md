---
name: dsdig-top50-fugu2-refresh-state
description: State of the top50-fugu2 refreshed capacitance review packet (2026-07-28) and its human feedback triage
metadata: 
  node_type: memory
  type: project
  originSessionId: bee5643e-f421-442b-9742-8e9701d3c45b
  modified: 2026-07-28T19:38:47.536Z
---

The refreshed top50-fugu2 capacitance packet lives at
`/Users/fab/dev/pv/pwr-mosfet-lib/out/fugu2-100v-LS1p/coss-review-top50-2026-07-28-refresh/`
(built at dsdig `db361e8` from a clean worktree; original 2026-07-28 dir is frozen with Fab's
first-round feedback). Fab reviewed the refresh on 2026-07-28: 18 green / 19 flagged /
6 rework / 3 pending; feedback persisted to the packet's
`MANIFEST.zz-human-feedback.top50-fugu2-ls-missing-curves.jsonl`.

Key facts: every flagged/rework item was already fail-closed (no wrong values servable);
all six extract_ok rows are human-GREEN. Triage with per-class root causes is committed at
`datasheet-chart-digitizer/docs/worklists/current-top50-fugu2-refresh-feedback.md` (674aab1).
Landed slices: class C axis-trust OCR recoveries (f3d877c; NCEP023N10 -> ok) and class B
black-grid structural trigger (fbeaa48; MCP100N10Y-TP recovered, ST/onsemi loud refusals;
the both-orientations interior-rule requirement exists because one orientation alone
matches a flat Ciss riding a decade line — XRS200N12T). Open: A wrong-panel caption
binding (HYG018N10NS1P, empty-panel-text guard candidate), goford frame-band ownership,
ST/onsemi thin-curve recovery, D early termination, E Vishay banding, F siliup finder gaps,
NCE linear-seating cases blocked on the other agent's in-flight seating slice.

**Why:** future sessions continuing dsdig capacitance recovery should start from this
worklist and packet rather than re-deriving the state.

**How to apply:** pick slices from the worklist doc; re-review via the packet's HTML
(review-html/top50-fugu2-ls-missing-curves/...-001.html, Import JSON re-seeds by id);
follow [[dsdig-collateral-acceptance-discipline]] and [[dsdig-review-packet-staleness]]
for any finder/digitizer change.
