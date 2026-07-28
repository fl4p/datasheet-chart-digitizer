---
name: dsdig body_diode values.json list format
description: body_diode values.json is a list of per-temperature extraction entries, not a dict
created: 2026-07-16T21:47:00.954Z
metadata:
  node_type: memory
  generator: opencode-claude-memory
  type: reference
  originSessionId: ses_0931c45c4ffeHh0WT2O6Bq2fCh
---

For `body_diode` charts, `values.json` is a JSON array of per-temperature entries (one object per curve/temperature), not a single dict like `reverse_recovery` outputs. Each entry has `status`, `diagnostics`, `point_columns` (e.g., `["vsd_v","current_a"]`), `panel` metadata, `x_axis`, `y_axis`, and points. Scripts that inspect scale or status must iterate the array, not call `.get()` on the top-level object.
