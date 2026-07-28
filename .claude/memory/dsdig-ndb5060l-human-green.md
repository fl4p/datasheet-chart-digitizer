---
name: dsdig-ndb5060l-human-green
description: "Fab human-verified all onsemi NDB5060L charts GREEN after Figure 9's closed right-frame recovery through 50 V"
metadata:
  node_type: memory
  type: project
---

On 2026-07-20 Fab explicitly verified that **all charts in
`onsemi/NDB5060L.pdf` are GREEN**. This includes Figure 9, whose capacitance
plot now uses the true closed right frame and keeps Ciss, Coss, and Crss
source-seated through approximately 50 V.

The frozen target packet is
`/private/tmp/dsdig-ndb5060l-cap-right-v1`. This verdict closes the NDB5060L
item-level human-review gate. It does not itself authorize a commit/push,
land the shared detector change, or upgrade unrelated NCE corpus controls.
