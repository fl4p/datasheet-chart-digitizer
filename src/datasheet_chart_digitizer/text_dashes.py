"""Fold typographic dash variants to ASCII '-' before title text is matched.

Vendor PDFs print "drain-source" with U+2011 (non-breaking hyphen), U+2010,
en/em dashes, U+2212 (minus) or a soft hyphen (U+00AD).  A title regex written
with ASCII '-' then silently fails and a finder-identified panel is dropped
(IPP050N03LF2S, ISC040N10NM8 "Normalized drain‑source on resistance").
Title matching therefore runs on dash-folded text.  Numeric tick parsing keeps
its own handling and is not routed through here.
"""

from __future__ import annotations

import re

# U+00AD soft hyphen, U+2010..U+2015 hyphen..horizontal bar, U+2212 minus,
# U+FE63 small hyphen-minus, U+FF0D fullwidth hyphen-minus.
DASH_VARIANTS_RE = re.compile("[­‐-―−﹣－]")


def normalize_dashes(text: str) -> str:
    """Return *text* with every dash variant replaced by ASCII '-'."""
    return DASH_VARIANTS_RE.sub("-", text)
