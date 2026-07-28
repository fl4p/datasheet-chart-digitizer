---
name: dsdig fail-closed null scalars rule
description: dsdig refuse statuses must null all derived scalars and curves, not only set the status flag
created: 2026-07-17T13:32:07.062Z
metadata:
  node_type: memory
  generator: opencode-claude-memory
  type: project
  originSessionId: ses_0934d7306ffeWuem561YxSpCLH
---

In dsdig, a fail-closed extraction status (low_confidence, axis_assumed, unverified, rejected) is not sufficient protection by itself. The emitted values.json must also set every derived scalar to null and every curve_px to empty/omitted. A non-null Vpl/Qoss/etc. alongside a fail-closed status is a contract leak: a downstream consumer that keys on the scalar will ingest an untrusted plausible-looking number even if it checks the curve.

**Why:** Class-D v2 GAN7R0-150LBEZ (physical_output_available=false but output_charge_reference still held qoss_pc/coer_pf/cotr_pf) and retro-red17 v1 HY1001D + 3 panjit items (status=low_confidence/axis_assumed but vpl still non-null and curve_px non-empty) both showed the same anti-monotone failure mode. Both lanes now agree this is RED.

**How to apply:** When reviewing any dsdig extraction packet, verify that for every item with status not equal to "ok" (or its equivalent trusted state), the corresponding scalar fields (vpl, qoss_pc, coer_pf, cotr_pf, etc.) are null and curve_px is empty/absent. Do not accept "status is correct" as a GREEN if the scalar is still populated. When implementing fixes, gate scalar emission on the same condition that sets the fail-closed status.
