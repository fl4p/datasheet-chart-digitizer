"""Grid-row ownership helpers for chart-panel discovery."""

from __future__ import annotations

import re
from typing import Protocol


class WordLike(Protocol):
    text: str
    x0: float
    y0: float
    x1: float
    y1: float


def grid_rows_belong_to_same_panel(
    words: list[WordLike],
    previous_y: float,
    current_y: float,
    x0: float,
    x1: float,
) -> bool:
    """Bridge one missing grid row without crossing a figure caption."""

    gap = current_y - previous_y
    if gap <= 28.0:
        return True
    if gap > 74.0:
        return False
    caption_pad = min(42.0, max(16.0, 0.20 * (x1 - x0)))
    for index, word in enumerate(words):
        center_x = 0.5 * (word.x0 + word.x1)
        center_y = 0.5 * (word.y0 + word.y1)
        token = word.text.lower().rstrip(".:")
        if (
            x0 - caption_pad <= center_x <= x1 + caption_pad
            and previous_y < center_y < current_y
        ):
            if token in {"figure", "fig", "diagram"} or re.match(
                r"^(?:fig(?:ure)?|diagram)\.?\d", token
            ):
                return False
            if re.fullmatch(r"\d+(?:[.\-]\d+)*", token) and index:
                prefix = words[index - 1]
                prefix_token = prefix.text.lower().rstrip(".:")
                prefix_center_y = 0.5 * (prefix.y0 + prefix.y1)
                if (
                    prefix_token in {"figure", "fig", "diagram"}
                    and -2.0 <= word.x0 - prefix.x1 <= 14.0
                    and abs(prefix_center_y - center_y) <= 4.0
                ):
                    return False
    return True


def grid_rule_widths_are_compatible(first: float, second: float) -> bool:
    """Keep wide enclosing cell rails out of a narrower plot-grid group."""
    return max(first, second) / max(1.0, min(first, second)) <= 1.8
