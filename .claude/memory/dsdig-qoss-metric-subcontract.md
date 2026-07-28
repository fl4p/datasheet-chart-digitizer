---
name: dsdig Qoss metric sub-contract
description: C(V) curve availability and derived Qoss-metric availability are separate; unsafe Qoss calculations stay diagnostic-only with explicit reasons
metadata:
  node_type: memory
  created: 2026-07-17
  type: project
---

A source-faithful, physically available Ciss/Coss/Crss extraction does not make
its integrated Qoss/Eoss/Co(er)/Co(tr) bundle consumer-safe. The derived bundle
needs its own boolean availability contract, gated by the parent chart plus an
accepted graph/table, vendor-tail, or validated clipped-completion status.

When that sub-contract fails, `qoss_metrics` must be null. Preserve the computed
numbers only in an explicitly diagnostic-only field and emit concrete status
reasons; never force consumers to infer safety from a status string or the parent
chart flag. A missing table reference is `reference_unavailable` (or
`chart_clipped_reference_unavailable`), not `graph_table_inconsistent` and never
`chart_clipped_table_authoritative`.

The full-corpus acceptance invariant for a serialization-only fix is zero movement
outside the Qoss contract fields, including byte-identical C(V) points, identities,
axes, plot boxes, overlays, and exception manifests. Related:
[[dsdig-fail-closed-null-scalars]], [[dsdig-full-corpus-authoritative-harness]],
[[chart-review-checklist]].
