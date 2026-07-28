---
name: dsdig table-reference column ownership
description: Table-derived Qoss and capacitance references must be owned by a value/typ column, never a nearby condition token.
metadata:
  type: fact
---

For datasheet table references such as Qoss, Co(er), Co(tr), Ciss, Coss, and
Crss, the first nearby number is not a safe value. Conditions including VDS,
VGS, ID, frequency, and temperature can appear before the value column. Parse
from an evidenced value/typ cell and record exact source-cell/token ownership.

Numerical equality is not ownership proof: a real value may equal a condition.
If the parser proves that the legacy value came from a condition token but
cannot recover a distinct value, fail closed with a null and a visible
per-symbol diagnostic. Keep graph-vs-table inconsistency checks independent so
a parser repair cannot mute a genuine disagreement. Shared-parser changes use
the full-corpus same-environment A/B discipline in
[[dsdig full-corpus authoritative harness]].
