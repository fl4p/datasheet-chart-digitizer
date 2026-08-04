"""Source-owned condition-label extraction for RDS charts."""

from __future__ import annotations

import re
from pathlib import Path

from .find_charts import (
    group_words_into_lines,
    line_bbox,
    line_text,
    run_text_bbox,
    words_in_bbox,
)
from .finder_types import Word


VGS_RE = re.compile(r"V\s*GS\s*=\s*(\d+(?:\.\d+)?)\s*V", re.I)


def vgs_label_rows(panel) -> list[tuple[float, float, float]]:
    """Return distinct local ``(VGS, label_x0, row_y)`` source rows."""

    page_text = run_text_bbox(Path(panel.pdf))[panel.page - 1]
    words = words_in_bbox(page_text.words, panel.bbox_pt)
    rows: list[tuple[float, float, float]] = []
    for line in group_words_into_lines(words):
        before = len(rows)
        for index in range(len(line) - 4):
            label_words = line[index : index + 5]
            match = VGS_RE.fullmatch(" ".join(word.text for word in label_words))
            if match is None:
                continue
            bbox = line_bbox(label_words)
            rows.append(
                (float(match.group(1)), bbox[0], 0.5 * (bbox[1] + bbox[3]))
            )
        if len(rows) == before:
            text = line_text(line)
            for match in VGS_RE.finditer(text):
                bbox = line_bbox(line)
                rows.append(
                    (float(match.group(1)), bbox[0], 0.5 * (bbox[1] + bbox[3]))
                )
    rows.extend(_subscript_vgs_rows(words))
    return list(dict.fromkeys(rows))


def _subscript_vgs_rows(words: list[Word]) -> list[tuple[float, float, float]]:
    """Recover V/GS rows split by the source subscript's lower baseline."""

    def middle_y(word: Word) -> float:
        return 0.5 * (word.y0 + word.y1)

    rows: list[tuple[float, float, float]] = []
    for base in (word for word in words if word.text.strip().upper() == "V"):
        subscripts = [
            word
            for word in words
            if word.text.strip().upper() == "GS"
            and -0.75 <= word.x0 - base.x1 <= 2.5
            and 0.0 <= middle_y(word) - middle_y(base) <= 4.5
        ]
        for subscript in subscripts:
            cursor = subscript
            label_words = [base, subscript]
            for pattern in (r"=", r"\d+(?:\.\d+)?", r"V"):
                matches = [
                    word
                    for word in words
                    if re.fullmatch(pattern, word.text.strip(), re.I)
                    and -0.75 <= word.x0 - cursor.x1 <= 4.0
                    and abs(middle_y(word) - middle_y(base)) <= 4.5
                ]
                if len(matches) != 1:
                    break
                cursor = matches[0]
                label_words.append(cursor)
            if len(label_words) != 5:
                continue
            bbox = line_bbox(label_words)
            rows.append(
                (
                    float(label_words[3].text),
                    bbox[0],
                    0.5 * (bbox[1] + bbox[3]),
                )
            )
    return rows
