---
name: dsdig opposite-outer transfer label binding
description: close crossing transfer curves may use source labels outside opposite local curve envelopes, but physical hot-left validation remains mandatory
metadata:
  type: project
---

For two-temperature transfer charts, global label-to-curve distance can be
ambiguous when the curves nearly overlap and cross. A bounded fallback may
bind from source geometry only when each printed temperature label lies
visibly outside its two-curve envelope at that label's own Y/current, the
nearer curve wins by a visible margin, the labels occupy opposite sides, and
they bind distinct branches. Labels between curves, on the same side, or
inside either confidence margin still refuse.

This geometric binding does not establish physical identity by itself. The
label-bound hotter curve must remain visibly left/lower-threshold at shared
low current, and the pair may show at most one robust ZTC reversal. IRF100PW219
p8d7 is the positive control; BSB028N06NN3_G is the hot-left contradiction
that must continue to refuse. Frozen evidence lives at
`/private/tmp/dsdig-irf100pw219-transfer-binding-v1`; full transfer-corpus and
human review remain held.
