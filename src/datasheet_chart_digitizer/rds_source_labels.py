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


_NUMBER_WORD_RE = re.compile(r"\d+(?:\.\d+)?")
_NUMBER_V_WORD_RE = re.compile(r"(\d+(?:\.\d+)?)V")
_SYMBOL_SUBSCRIPT_RE = re.compile(r"(GS|DS|TH|SD|DD|F|\(BR\))\b", re.I)
_ADJACENT_GAP_PT = 4.0


def bare_voltage_labels(panel) -> list[float]:
    """Curve labels printed as a bare voltage ("3 V", "2.6V") in the panel.

    Word geometry, not joined line text: the number and its "V" must touch
    (<= 4 pt gap), and a "V" that starts a symbol (V GS, V DS, ...) is not a
    unit. A token glued to "=" ("=10V") is a condition, not a curve label.
    Used to prove a panel names more curves than the one VGS row a
    count-only binding would use.
    """

    page_text = run_text_bbox(Path(panel.pdf))[panel.page - 1]
    words = sorted(words_in_bbox(page_text.words, panel.bbox_pt), key=lambda w: (w.y0, w.x0))
    found: list[float] = []
    for line in group_words_into_lines(words):
        line = sorted(line, key=lambda w: w.x0)
        for index, word in enumerate(line):
            text = word.text.strip().rstrip(",;")
            if index and line[index - 1].text.strip().endswith("="):
                continue  # "VGS = 10 V": a condition value, not a curve label
            glued = _NUMBER_V_WORD_RE.fullmatch(text)
            if glued:
                found.append(float(glued.group(1)))
                continue
            if not _NUMBER_WORD_RE.fullmatch(text) or index + 1 >= len(line):
                continue
            unit = line[index + 1]
            if unit.text.strip().rstrip(",;") != "V" or unit.x0 - word.x1 > _ADJACENT_GAP_PT:
                continue
            after = line[index + 2] if index + 2 < len(line) else None
            if after is not None and after.x0 - unit.x1 <= 2.0 and _SYMBOL_SUBSCRIPT_RE.match(after.text):
                continue
            found.append(float(text))
    return found


def count_only_binding_refusal(panel, vgs: float) -> str | None:
    """Why one VGS row + one trace must not bind by count alone, or None.

    That binding has no geometric evidence; it is safe only when the panel
    names no other curve. PSMN0R7-25YLD p7 prints "2.6 V .. 4.5 V" labels
    beside its "VGS = 10 V" row; the flat 10 V curve was dropped upstream and
    the 3 V curve was served as VGS = 10 V (2-7x off, status ok).
    """

    others = sorted({v for v in bare_voltage_labels(panel) if v != vgs})
    if not others:
        return None
    return (
        f"legend: one VGS row ({vgs:g} V) and one trace, but the panel labels "
        f"other curves {others} V; count-only binding refused"
    )
