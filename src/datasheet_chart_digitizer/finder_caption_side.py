"""Which side of a caption its plot is on, when no grid region decides it.

``choose_caption_synthetic_bbox`` is the finder's last resort for a caption
whose plot produced no grid region (dotted/dashed/absent grids leave too few
solid horizontal rules). It used PAGE POSITION alone: a caption in the top
35 % of the page was assumed to lead a plot BELOW it. That is wrong for the
common "plot, then ``Figure N.`` caption" layout in a page's top row, where it
cropped the empty space (or the next row's panel) under the caption.

The side is decided here from positive evidence instead: the caption's own
x-axis title above it (``caption_axis_direction``), or an x tick-label row
directly above it. Either is accepted only when no OTHER caption sits above
in the same column within one panel height -- in a caption-leads layout that
earlier caption owns the plot above, and the tick row belongs to it.
"""

from __future__ import annotations

import re
from typing import Callable, Iterable

BBox = tuple[float, float, float, float]

# A tick-label row ends at most this far above the caption (tick labels,
# then the axis title line, then the caption).
_TICK_ROW_MAX_GAP_PT = 45.0
# Another caption this far above, in the same column, owns the plot above.
_PANEL_HEIGHT_PT = 260.0
# Half-width of the caption's column, as a fraction of the page width.
_COLUMN_HALF_WIDTH_FRACTION = 0.18
_MIN_TICKS = 2
_TICK_RE = re.compile(
    r"[−\-+]?\d+(?:[.,]\d+)?(?:[Ee][+\-−]?\d+)?[kKmMuµ]?|[−\-+]?\d*10[⁰¹²³⁴⁵⁶⁷⁸⁹⁻]+"
)


def _in_column(bbox: BBox, center: float, half_width: float) -> bool:
    return min(bbox[2], center + half_width) - max(bbox[0], center - half_width) > 0.0


def _is_tick_row(text: str) -> bool:
    tokens = text.split()
    ticks = [token for token in tokens if _TICK_RE.fullmatch(token)]
    return len(ticks) >= _MIN_TICKS and len(ticks) >= 0.6 * len(tokens)


def caption_owns_plot_above(
    title_bbox: BBox,
    lines: Iterable[tuple[str, BBox]],
    page_width: float,
    axis_direction: str | None,
    is_caption_line: Callable[[str], bool],
) -> bool:
    """True when the evidence puts this caption's plot ABOVE it."""
    if axis_direction == "below":
        return False
    tx0, ty0, tx1, _ty1 = title_bbox
    center = 0.5 * (tx0 + tx1)
    half = _COLUMN_HALF_WIDTH_FRACTION * page_width
    rows = [(text, bbox) for text, bbox in lines if _in_column(bbox, center, half)]
    for text, bbox in rows:
        if (
            bbox[3] <= ty0 - 1.0
            and ty0 - bbox[3] <= _PANEL_HEIGHT_PT
            and is_caption_line(text.strip())
        ):
            return False
    if axis_direction == "above":
        return True
    return any(
        ty0 - _TICK_ROW_MAX_GAP_PT <= bbox[3] <= ty0 - 1.0 and _is_tick_row(text)
        for text, bbox in rows
    )


_CAPTION_WORD_RE = re.compile(r"(?i)^(?:fig(?:ure)?\.?|diagram)(\d+[.:]?)?$")
_NUMBER_WORD_RE = re.compile(r"^\d+[.:]?$")
# Words of one caption are set closer than this; a wider gap ends it.
_CAPTION_WORD_GAP_PT = 12.0


def _row_caption_spans(
    words: list[tuple[str, BBox]], ty0: float, ty1: float
) -> list[tuple[float, float]]:
    """x-extents of every caption that starts on this text row."""
    row = sorted(
        (w for w in words if min(ty1, w[1][3]) - max(ty0, w[1][1]) > 0.0),
        key=lambda w: w[1][0],
    )
    spans: list[tuple[float, float]] = []
    for index, (text, box) in enumerate(row):
        match = _CAPTION_WORD_RE.match(text)
        if match is None:
            continue
        numbered = match.group(1) is not None or (
            index + 1 < len(row) and _NUMBER_WORD_RE.match(row[index + 1][0])
        )
        if not numbered:
            continue
        end = box[2]
        for _text, nxt in row[index + 1 :]:
            if nxt[0] - end > _CAPTION_WORD_GAP_PT:
                break
            end = nxt[2]
        spans.append((box[0], end))
    return spans


def bound_to_caption_column(
    bbox: BBox,
    title_bbox: BBox,
    words: list[tuple[str, BBox]],
    half_width: float,
    page_width: float,
) -> BBox:
    """Trim a synthetic crop's x-range at the column of a same-row caption.

    Side-by-side panels print their captions on one text row, and the caption
    parser can append the neighbour's words to this title (synthetic page 146:
    ``11 Typ. capacitances B [mT]``, a bbox reaching into the next column).
    The column boundary is halfway between the two captions' centres, each
    caption measured from its own ``Fig./Figure/Diagram N`` start to the first
    wide word gap, so the crop never spans the neighbouring panel. When the
    title bbox runs past its own caption (appended neighbour words), the
    window is first re-centred on the caption itself, which is where the
    caller's *half_width* was meant to be applied.
    """
    x0, y0, x1, y1 = bbox
    tx0, ty0, _tx1, ty1 = title_bbox
    spans = _row_caption_spans(words, ty0, ty1)
    own = min(spans, key=lambda span: abs(span[0] - tx0), default=None)
    if own is None or abs(own[0] - tx0) > 40.0:
        return bbox
    own_center = 0.5 * (own[0] + own[1])
    if len(spans) > 1 and own[1] < _tx1 - _CAPTION_WORD_GAP_PT:
        x0 = max(0.0, own_center - half_width)
        x1 = min(page_width, own_center + half_width)
    for span in spans:
        if span is own:
            continue
        boundary = 0.5 * (own_center + 0.5 * (span[0] + span[1]))
        if span[0] > own[0]:
            x1 = min(x1, boundary)
        else:
            x0 = max(x0, boundary)
    return (x0, y0, x1, y1)
