---
name: dsdig absolute RDS temperature axis kind
description: absolute mOhm RDS(Tj) must never be validated as normalized; typ/max serving requires two source-owned, noncrossing traces and local conditions
metadata:
  type: project
---

`rdson_temperature` must distinguish normalized RDS(on)/RDS(on,25C) from an
absolute RDS(on) axis before applying unity-at-25 °C. The bounded absolute path
serves `temperature_c,rdson_mohm` only when the panel owns an mΩ token, exactly
two full-span same-style traces, standalone typ/max labels, and one local VGS
and ID condition. Max must remain visibly above typ across the entire common
span; crossing or ambiguity refuses. Normalized controls must remain
byte-identical and keep the unity guard.

IPT007N06N p8d9 is the frozen positive control at
`/private/tmp/dsdig-ipt007-absolute-rds-tj-v1`. General absolute-ohm axes and
the full RDS(Tj) corpus remain held.
