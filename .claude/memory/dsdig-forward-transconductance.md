---
name: dsdig-forward-transconductance
description: "No direct gfs/gm-vs-Id digitizer exists; dedicated fail-closed plugin tracked by datasheet-chart-digitizer issue #9, distinct from Id-Vgs transfer issue #4"
metadata:
  type: project
---

The datasheet chart digitizer has no dedicated forward-transconductance
(`gfs`/`gm` versus drain current) extractor, finder kind, or focused test.
Transfer-characteristics issue #4 is related but provides indirect `gm` evidence
from `Id(Vgs)` and is not a substitute for the published `gfs(Id)` curve.

Implementation and human-overlay acceptance are tracked in
https://github.com/fl4p/datasheet-chart-digitizer/issues/9.
