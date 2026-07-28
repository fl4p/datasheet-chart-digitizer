---
name: dsdig crossing intersection check mandatory
description: For dsdig MOSFET chart reviews, microscopic 5x intersection-point inspection is mandatory before GREEN; normal-scale overlay inspection is insufficient for crossing curves.
created: 2026-07-17T11:51:35.027Z
metadata:
  node_type: memory
  generator: opencode-claude-memory
  type: feedback
  originSessionId: ses_0934d7306ffeWuem561YxSpCLH
---

When reviewing dsdig-extracted charts with intersecting curves (especially Ciss/Coss capacitance crossings), a normal-scale overlay inspection is **not sufficient**. I missed a branch-switching defect on PSMN5R3-25MLD because the label binding and shared-span status looked correct at full scale, but the blue Coss points snapped onto the red Ciss stroke and rode it through the crossing-approach region.

**Why:** The CHART-REVIEW-CHECKLIST.md already had §3 and §5 language about intersection inspection and branch-switching, but I treated it as optional guidance rather than a mandatory gate. Raster noise and correct top-level topology hid the neighbor-branch snap.

**How to apply:**
- For any chart with intersecting curves, crop the **approach and intersection region** at 5× (or higher) before calling GREEN.
- Verify that **each curve's extracted points stay on their own source stroke** through the crossing; a descending curve must not flatten out or wiggle along the neighbor curve.
- Check **monotonicity** of the centerline through the approach — a non-monotone notch near the other curve is a RED flag.
- If the intersection inspection is missing, the verdict must be **UNVERIFIED**, not GREEN.
