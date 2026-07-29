"""Grid-row ownership helpers for chart-panel discovery."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Protocol


# A caption opens its column; in-plot text sits close to its neighbours.
CAPTION_LEFT_CLEARANCE_PT = 40.0
# The same phrase set ``find_charts._caption_starts`` uses to admit a bare
# numbered caption.  Geometry alone cannot tell "12 Typ. avalanche
# characteristics" from a Y tick label that happens to sit beside an in-plot
# annotation ("50" next to "VDD = 100V", "1" inside "f = 1 MHz"), so the
# convention's own vocabulary has to be part of the test.
CAPTION_TITLE_PHRASES = (
    "typ",
    "typical",
    "gate",
    "charge",
    "capacitance",
    "avalanche",
    "breakdown",
    "transfer",
    "waveforms",
    "dynamic",
    "diode",
    "forward",
    "threshold",
    "safe",
    "thermal",
    "output",
    "on-resistance",
    "drain-source",
)
CAPTION_TITLE_LOOKAHEAD_WORDS = 4
# Gaps at or below this width used to short-circuit to "same panel" unscanned.
SHORT_GAP_PT = 28.0


class WordLike(Protocol):
    text: str
    x0: float
    y0: float
    x1: float
    y1: float


def _opens_its_line(words: Sequence[WordLike], index: int) -> bool:
    """True when nothing sits on the same text line just left of this word.

    A caption opens its column; an embedded cross-reference does not.
    """

    word = words[index]
    center_y = 0.5 * (word.y0 + word.y1)
    for other in words:
        if other is word:
            continue
        if abs(0.5 * (other.y0 + other.y1) - center_y) > 3.0:
            continue
        if other.x1 <= word.x0 + 0.5 and word.x0 - other.x1 < CAPTION_LEFT_CLEARANCE_PT:
            return False
    return True


def _starts_bare_numbered_caption(words: Sequence[WordLike], index: int) -> bool:
    """Recognise the ``12 Typ. avalanche characteristics`` caption convention.

    Infineon (and several others) number panels without a ``Figure``/``Diagram``
    keyword, so the keyword rules below cannot see those captions at all.  The
    discriminator against in-plot text is positional, not lexical:

    * the title word must follow within 14 pt — that separates a caption from a
      Y tick label sharing a text line with a far-right legend entry
      (``10`` … ``Ciss``);
    * nothing may sit on the same text line within 40 pt to the LEFT.  A
      caption opens its column; a condition line's embedded number does not;
    * the following words must contain a chart-caption phrase.

    Two purely geometric attempts both regressed real panels, because a Y tick
    label beside an in-plot annotation is geometrically indistinguishable from
    a caption: ``f = 1 MHz`` truncated 34 onsemi capacitance panels, then ``50``
    next to ``VDD = 100V`` truncated an FDB15N50 transfer plot and an IRF644S
    gate-charge plot.  The convention's own vocabulary is the discriminator,
    which is exactly how ``find_charts._caption_starts`` admits these captions.
    """

    word = words[index]
    token = word.text.strip().rstrip(".:,")
    if not re.fullmatch(r"\d{1,2}", token) or not 1 <= int(token) <= 50:
        return False
    center_y = 0.5 * (word.y0 + word.y1)
    if not _opens_its_line(words, index):
        return False
    if index + 1 >= len(words):
        return False
    follower = words[index + 1]
    if abs(0.5 * (follower.y0 + follower.y1) - center_y) > 4.0:
        return False
    if not -2.0 <= follower.x0 - word.x1 <= 14.0:
        return False
    if not re.match(r"^[A-Za-z][A-Za-z\-]{2,}", follower.text.strip()):
        return False
    tail = " ".join(
        item.text
        for item in words[index + 1 : index + 1 + CAPTION_TITLE_LOOKAHEAD_WORDS]
    ).lower()
    return any(phrase in tail for phrase in CAPTION_TITLE_PHRASES)


def grid_rows_belong_to_same_panel(
    words: Sequence[WordLike],
    previous_y: float,
    current_y: float,
    x0: float,
    x1: float,
) -> bool:
    """Bridge one missing grid row without crossing a figure caption.

    The caption scan runs for EVERY gap this side of the distance cutoff.  A
    short gap used to short-circuit to "same panel" without looking, and in a
    2x2 Infineon layout the caption between two stacked panels sits inside a
    27 pt gap — bracketed by the two panels' own outer frame rules — so both
    panels merged into one region and the capacitance caption could bind to the
    avalanche plot below it (IAUTN12S5N018G/T).
    """

    gap = current_y - previous_y
    if gap > 74.0:
        return False
    # Gaps wider than 28 pt were already scanned before this change and keep
    # their exact behaviour.  Only the previously short-circuited short gaps get
    # the new tests, so this can add splits there and can change nothing else.
    newly_scanned = gap <= SHORT_GAP_PT
    caption_pad = min(42.0, max(16.0, 0.20 * (x1 - x0)))
    column_x0, column_x1 = x0 - caption_pad, x1 + caption_pad
    for index, word in enumerate(words):
        center_x = 0.5 * (word.x0 + word.x1)
        center_y = 0.5 * (word.y0 + word.y1)
        token = word.text.lower().rstrip(".:")
        if (
            column_x0 <= center_x <= column_x1
            and previous_y < center_y < current_y
        ):
            if token in {"figure", "fig", "diagram"} or re.match(
                r"^(?:fig(?:ure)?|diagram)\.?\d", token
            ):
                # In a short gap the keyword may be an in-plot cross-reference
                # ("For test circuit see figure 13" inside IRF644S figure 6), so
                # there it must actually open its line to count as a caption.
                if not newly_scanned or _opens_its_line(words, index):
                    return False
            if re.fullmatch(r"\d+(?:[.\-]\d+)*", token) and index:
                prefix = words[index - 1]
                prefix_token = prefix.text.lower().rstrip(".:")
                prefix_center_y = 0.5 * (prefix.y0 + prefix.y1)
                if (
                    prefix_token in {"figure", "fig", "diagram"}
                    and -2.0 <= word.x0 - prefix.x1 <= 14.0
                    and abs(prefix_center_y - center_y) <= 4.0
                    and (not newly_scanned or _opens_its_line(words, index - 1))
                ):
                    return False
            if newly_scanned and _starts_bare_numbered_caption(words, index):
                return False
    return True


def grid_rule_widths_are_compatible(first: float, second: float) -> bool:
    """Keep wide enclosing cell rails out of a narrower plot-grid group."""
    return max(first, second) / max(1.0, min(first, second)) <= 1.8
