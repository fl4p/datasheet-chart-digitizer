"""Geometry helpers for caption-discovered chart panels."""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from collections.abc import Callable
from typing import Protocol

import pymupdf


BBox = tuple[float, float, float, float]
_SPEC_TABLE_MARKERS = {
    "min", "typ", "max", "typical", "unit", "units", "parameter", "symbol",
    "conditions", "value",
}
_SPEC_TABLE_FAMILIES = {
    "leakage", "threshold", "capacitance", "charge", "resistance",
    "transconductance", "recovery",
}
_SWITCHING_TIME_PHRASES = {
    "turnonrisetime", "turnondelaytime", "turnoffdelaytime", "turnofffalltime",
}
_CHARGE_ROW_PHRASES = {
    "totalgatecharge", "gatesourcecharge", "gatedraincharge",
    # TI tables spell the description before the terminal qualifier.
    "gatechargetotal", "gatechargegatetosource", "gatechargegatetodrain",
}
_CAPACITANCE_ROW_PHRASES = {"inputcapacitance", "outputcapacitance", "reversetransfercapacitance"}
_BODY_DIODE_CURRENT_AXIS_TOKENS = {"isd", "isda", "is", "isa", "if", "ifa"}
_CAPTION_AXIS_TOKENS = {
    "transfer": {"vgs", "vgsv", "vge", "vgev"},
    "body_diode": {"vsd", "vsdv", "vfd", "vfdv", "vf", "vfv"},
    "capacitances": {"vds", "vdsv"},
    "gate_charge": {"qg", "qgnc", "qgate", "qgatenc"},
    "breakdown_voltage": {
        "tj", "tjc", "tjv", "junction", "junctiontemperature",
    },
    "rds_on": {
        "id", "ida", "tj", "tjc", "tc", "tcc", "case", "casetemperature",
        "junction", "junctiontemperature",
    },
}
_ALL_CAPTION_AXIS_TOKENS = (
    set().union(*_CAPTION_AXIS_TOKENS.values())
    | _BODY_DIODE_CURRENT_AXIS_TOKENS
)


def grid_rows_belong_to_same_panel(
    words: list[_WordLike],
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


def caption_continuation(
    lines: list[list[_WordLike]], line_idx: int, segment: list[_WordLike]
) -> str:
    """Return a short wrapped caption continuation below a title segment."""

    if line_idx + 1 >= len(lines):
        return ""
    sx0 = min(word.x0 for word in segment)
    sx1 = max(word.x1 for word in segment)
    sy1 = max(word.y1 for word in segment)
    segment_width = sx1 - sx0
    continuation: list[str] = []
    for next_line in lines[line_idx + 1 : line_idx + 3]:
        pad = max(16.0, min(48.0, 0.30 * segment_width))
        own_column = [
            word
            for word in next_line
            if sx0 - pad <= 0.5 * (word.x0 + word.x1) <= sx1 + pad
        ]
        if not own_column:
            continue
        nx0 = min(word.x0 for word in own_column)
        ny0 = min(word.y0 for word in own_column)
        nx1 = max(word.x1 for word in own_column)
        if ny0 - sy1 > 18:
            break
        overlap = max(0.0, min(sx1, nx1) - max(sx0, nx0))
        if overlap < max(8.0, min(segment_width, nx1 - nx0) * 0.25):
            continue
        text = " ".join(word.text for word in own_column)
        if not re.search(r"[A-Za-z]", text):
            continue
        continuation.append(text)
    return " ".join(continuation).strip()


def detached_numbered_caption_title(
    page: _PageLike,
    lines: list[list[_WordLike]],
    line_idx: int,
    segment: list[_WordLike],
) -> tuple[str, BBox, int] | None:
    """Join a number-only ``Fig. N`` row to its next-line column title.

    Older two-column datasheets place both figure numbers on one row and both
    titles on the next.  A number alone is not chart evidence, so this helper
    only returns same-half-page alphabetic text within one line gap.  The
    caller still has to classify that text as a supported chart family.
    """

    if line_idx + 1 >= len(lines):
        return None
    text = " ".join(word.text for word in segment).strip()
    if not re.fullmatch(r"(?i)(?:figure|fig\.?)\s+\d+(?:[.\-]\d+)?[.,:]?", text):
        return None
    sx0 = min(word.x0 for word in segment)
    sy0 = min(word.y0 for word in segment)
    sx1 = max(word.x1 for word in segment)
    sy1 = max(word.y1 for word in segment)
    next_line = lines[line_idx + 1]
    if not next_line or min(word.y0 for word in next_line) - sy1 > 22.0:
        return None
    midpoint = 0.5 * page.width_pt
    left_column = 0.5 * (sx0 + sx1) < midpoint
    own_column = [
        word
        for word in next_line
        if (0.5 * (word.x0 + word.x1) < midpoint) == left_column
    ]
    if not own_column:
        return None
    title = " ".join(word.text for word in own_column).strip()
    if not re.search(r"[A-Za-z]", title):
        return None
    return (
        title,
        (
            min(sx0, min(word.x0 for word in own_column)),
            min(sy0, min(word.y0 for word in own_column)),
            max(sx1, max(word.x1 for word in own_column)),
            max(sy1, max(word.y1 for word in own_column)),
        ),
        line_idx + 1,
    )


class _WordLike(Protocol):
    text: str
    x0: float
    y0: float
    x1: float
    y1: float


class _PageLike(Protocol):
    width_pt: float
    height_pt: float
    words: list[_WordLike]


class _TitleLike(Protocol):
    bbox_pt: BBox


def frame_bound_short_caption_segments(
    page: _PageLike,
    lines: list[list[_WordLike]],
    frames: list[BBox],
    classify: Callable[[str, str], str],
    reject: Callable[[str], bool],
) -> list[tuple[str, BBox]]:
    """Recover short unnumbered titles only from an evidenced chart row."""
    supported = {
        "gate_charge", "breakdown_voltage", "body_diode", "transfer",
        "capacitances", "rds_on",
    }
    candidates: dict[BBox, str] = {}
    for frame in frames:
        fx0, fy0, fx1, _ = frame
        for line in lines:
            if not line:
                continue
            ly1 = max(word.y1 for word in line)
            if not 0.0 <= fy0 - ly1 <= 24.0:
                continue
            own = [
                word for word in line
                if fx0 - 42.0 <= 0.5 * (word.x0 + word.x1) <= fx1 + 24.0
            ]
            if not 1 <= len(own) <= 6:
                continue
            text = " ".join(word.text for word in own).strip()
            if classify(text, "") not in supported or reject(text):
                continue
            bbox = (
                min(word.x0 for word in own), min(word.y0 for word in own),
                max(word.x1 for word in own), max(word.y1 for word in own),
            )
            candidates[bbox] = text
    return sorted(((text, bbox) for bbox, text in candidates.items()), key=lambda item: (item[1][1], item[1][0]))


def extend_wrapped_caption_titles(
    page: _PageLike,
    titles: list[_TitleLike],
    lines: list[list[_WordLike]],
) -> list[_TitleLike]:
    """Add short same-column continuation text to wrapped captions."""
    out: list[_TitleLike] = []
    for title in titles:
        # The production defect is specific to incomplete Gate Charge diagram
        # titles.  Keeping the continuation repair scoped avoids attaching
        # arbitrary axis prose to every other chart family in the corpus.
        if "gate" not in title.title.lower():
            out.append(title)
            continue
        tx0, _, tx1, ty1 = title.bbox_pt
        continuation: list[str] = []
        for line in lines:
            if any(word.text.lower() == "diagram" for word in line):
                continue
            lx0 = min(word.x0 for word in line)
            ly0 = min(word.y0 for word in line)
            lx1 = max(word.x1 for word in line)
            line_cx = 0.5 * (lx0 + lx1)
            if ly0 <= ty1 or ly0 > ty1 + 22:
                continue
            if not tx0 - 10 <= line_cx <= tx1 + 80:
                continue
            text = " ".join(word.text for word in line)
            if not re.search(r"[A-Za-z]", text) or "=" in text or "[" in text:
                continue
            continuation.append(text)
        if continuation:
            suffix = " ".join(continuation)
            title = replace(
                title,
                title=f"{title.title} {suffix}".strip(),
                line_text=f"{title.line_text} {suffix}".strip(),
            )
        out.append(title)
    return out


def _nearest_aligned_bottom_run(words: list[_WordLike]) -> list[_WordLike]:
    """Return the first horizontal numeric tick row, never a later panel's."""
    runs: list[list[_WordLike]] = []
    for word in sorted(words, key=lambda item: 0.5 * (item.y0 + item.y1)):
        center = 0.5 * (word.y0 + word.y1)
        for run in runs:
            run_center = 0.5 * (run[0].y0 + run[0].y1)
            if abs(center - run_center) <= 7.0:
                run.append(word)
                break
        else:
            runs.append([word])
    aligned = [run for run in runs if len(run) >= 2]
    return min(aligned, key=lambda run: min(word.y0 for word in run), default=[])


def _combined_axis_token(
    words: list[_WordLike],
    index: int,
    normalize: Callable[[str], str],
) -> str:
    """Join split V/GS, T/J, and I/D axis glyph words without changing line order."""
    word = words[index]
    token = normalize(word.text)
    if token not in {"i", "t", "v"}:
        return token
    # ``pdftotext`` can interleave words from two same-row panels because
    # their baselines differ by a fraction of a point.  Do not rely on list
    # adjacency here: find the geometrically adjacent suffix on this glyph's
    # own baseline.  The tight horizontal gap prevents joining labels across
    # columns.
    suffixes = sorted(
        (
            suffix
            for suffix in words
            if suffix is not word
            and word.x1 - 1.0 <= suffix.x0 <= word.x1 + 8.0
        ),
        key=lambda suffix: (suffix.x0, abs(0.5 * (suffix.y0 + suffix.y1 - word.y0 - word.y1))),
    )
    for suffix in suffixes:
        if abs(0.5 * (suffix.y0 + suffix.y1 - word.y0 - word.y1)) > 6.0:
            continue
        combined = token + normalize(suffix.text)
        if combined in _ALL_CAPTION_AXIS_TOKENS:
            return combined
        # Some vector PDFs split a subscript into two glyph words, e.g.
        # ``V`` + ``D`` + ``s``.  The one-suffix join above intentionally
        # remains the fast path; this second bounded join handles only the
        # immediately adjacent third glyph on the same baseline.
        second_suffixes = sorted(
            (
                second
                for second in words
                if second is not word
                and second is not suffix
                and suffix.x1 - 1.0 <= second.x0 <= suffix.x1 + 8.0
                and abs(
                    0.5
                    * (
                        second.y0
                        + second.y1
                        - suffix.y0
                        - suffix.y1
                    )
                )
                <= 6.0
            ),
            key=lambda second: second.x0,
        )
        for second in second_suffixes:
            combined = token + normalize(suffix.text) + normalize(second.text)
            if combined in _ALL_CAPTION_AXIS_TOKENS:
                return combined
    return token


def _axis_unit_evidenced(
    words: list[_WordLike],
    index: int,
    kind: str,
) -> bool:
    """Require a local unit-bearing axis label, not a condition token.

    ``VGS = 0`` and ``TJ = 25 degC`` occur frequently in nearby condition
    callouts.  The semantic token alone therefore cannot establish which side
    of a caption owns the plot.  A real x-axis label carries a parenthesized
    voltage/temperature unit on the same baseline without an intervening
    number or equals sign.
    """
    anchor = words[index]
    anchor_cy = 0.5 * (anchor.y0 + anchor.y1)
    same_line = sorted(
        (
            word
            for word in words
            if abs(0.5 * (word.y0 + word.y1) - anchor_cy) <= 6.0
            and anchor.x0 - 2.0 <= word.x0 <= anchor.x1 + 145.0
        ),
        key=lambda word: word.x0,
    )
    if not same_line:
        return False
    compact = "".join(word.text for word in same_line).lower().replace(" ", "")
    if kind == "breakdown_voltage":
        unit = re.search(r"(?:\([°5]?c\)|℃|junctiontemperature,?c)", compact)
    elif kind == "rds_on":
        unit = re.search(
            r"(?:\(a\)|\[a\]|\([°5]?c\)|℃|junctiontemperature,?c|[-:](?:°|º|q)?c)",
            compact,
        )
    elif kind == "gate_charge":
        unit = re.search(r"(?:\(nc\)|nanocoulombs?)", compact)
    elif kind == "body_diode":
        unit = re.search(r"(?:\((?:m?v|a)\)|\[(?:m?v|a)\])", compact)
    else:
        unit = re.search(r"(?:\(v\)|\[v\]|[-:]v(?:$|[^a-z]))", compact)
    if unit is None and kind == "breakdown_voltage":
        # Infineon sometimes stacks ``°C`` immediately above-left of the
        # split T/J glyph instead of placing the unit on the same baseline.
        # The tight edge-to-edge geometry distinguishes that axis label from
        # a remote ``TJ = 25 °C`` condition callout.
        unit_left = any(
            re.fullmatch(r"(?:°?c|℃)", word.text.strip(), re.IGNORECASE)
            and -1.0 <= anchor.x0 - word.x1 <= 8.0
            and -22.0 <= word.y0 - anchor.y0 <= 6.0
            for word in words
        )
        if unit_left:
            return True
    if unit is None and kind == "body_diode":
        unit_below = any(
            re.fullmatch(
                r"(?:\((?:m?v|a)\)|\[(?:m?v|a)\])",
                word.text.strip(),
                re.IGNORECASE,
            )
            and abs(0.5 * (word.x0 + word.x1 - anchor.x0 - anchor.x1)) <= 18.0
            and -2.0 <= word.y0 - anchor.y1 <= 9.0
            for word in words
        )
        if unit_below:
            return True
    if unit is None and kind == "capacitances":
        # Some vector plots spell only ``VDS`` under a dense row of numeric
        # ticks.  Three aligned tick values are independent axis evidence;
        # a lone ``VDS = 10 V`` condition cannot satisfy this fallback.
        nearby_ticks = [
            word
            for word in words
            if re.fullmatch(r"[+-]?\d+(?:\.\d+)?", word.text.strip())
            and abs(0.5 * (word.y0 + word.y1) - anchor_cy) <= 22.0
            and anchor.x0 - 190.0 <= word.x0 <= anchor.x1 + 40.0
        ]
        if len(nearby_ticks) >= 3:
            return True
    if unit is None:
        return False
    prefix = compact[: unit.start()]
    return "=" not in prefix and not re.search(r"\d", prefix)


def trim_adjacent_chart_caption(
    words: list[_WordLike],
    page_width: float,
    classify: Callable[[str, str], str],
) -> list[_WordLike]:
    """Drop a second-column caption fused onto a numbered caption line.

    Some two-column Infineon pages place an unnumbered chart caption on the
    same text baseline as ``Diagram N: ...``.  PDF text extraction merges both
    columns into one line.  A large gap alone is not enough evidence to split
    a legitimate title, so require independently recognized chart families on
    both sides of the gap.
    """
    minimum_gap = max(24.0, 0.05 * page_width)
    for index in range(2, len(words)):
        if words[index].x0 - words[index - 1].x1 < minimum_gap:
            continue
        prefix = " ".join(word.text for word in words[:index])
        suffix = " ".join(word.text for word in words[index:])
        explicit_figure = bool(
            re.match(r"(?i)^(?:fig(?:ure)?\.?\s*)?\d+(?:[.\-]\d+)?[.,:]?\b", suffix)
        )
        if explicit_figure or (
            classify(prefix, "") != "chart" and classify(suffix, "") != "chart"
        ):
            return words[:index]
    return words


def bbox_iou(a: BBox, b: BBox) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    overlap_x = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    overlap_y = max(0.0, min(ay1, by1) - max(ay0, by0))
    inter = overlap_x * overlap_y
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    return inter / max(1e-9, area_a + area_b - inter)


def bbox_overlap_fraction_of_smaller(a: BBox, b: BBox) -> float:
    """Return intersection area divided by the smaller candidate area."""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    overlap_x = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    overlap_y = max(0.0, min(ay1, by1) - max(ay0, by0))
    area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    smaller = min(area_a, area_b)
    return overlap_x * overlap_y / smaller if smaller > 0.0 else 0.0


def synthetic_bbox_has_plot_evidence(
    page: _PageLike,
    pdf: Path,
    page_num: int,
    bbox: BBox,
    *,
    grids: list[BBox],
    horizontal_rules: list[BBox],
    vertical_rules: list[BBox],
) -> bool:
    """Require local plot evidence before emitting a synthetic panel.

    Axis-label recovery is deliberately permissive, but marketing prose such
    as ``Low QG and Capacitance`` can resemble a Qg label. A synthetic crop is
    owned only when its region contains a frame/image/grid, a family of long
    parallel grid rules, or two aligned numeric tick labels.
    """
    structural_evidence = (
        *grids,
        *page_vector_plot_frames(pdf, page_num, page),
    )
    image_evidence = page_image_rects(pdf, page_num)
    if any(
        bbox_overlap_fraction_of_smaller(bbox, item) >= 0.55
        for item in structural_evidence
    ) or any(
        bbox_overlap_fraction_of_smaller(bbox, item) >= 0.80
        for item in image_evidence
    ):
        return True
    x0, y0, x1, y1 = bbox
    width, height = x1 - x0, y1 - y0
    long_h = [
        rule for rule in horizontal_rules
        if rule[2] - rule[0] >= 0.30 * width
        and x0 <= 0.5 * (rule[0] + rule[2]) <= x1
        and y0 <= 0.5 * (rule[1] + rule[3]) <= y1
    ]
    long_v = [
        rule for rule in vertical_rules
        if rule[3] - rule[1] >= 0.30 * height
        and x0 <= 0.5 * (rule[0] + rule[2]) <= x1
        and y0 <= 0.5 * (rule[1] + rule[3]) <= y1
    ]
    if len(long_h) >= 3 or len(long_v) >= 3:
        return True
    numeric = [
        word for word in page.words
        if re.fullmatch(r"[+-]?\d+(?:\.\d+)?", word.text.strip())
        and x0 <= 0.5 * (word.x0 + word.x1) <= x1
        and y0 <= 0.5 * (word.y0 + word.y1) <= y1
    ]
    for first_index, first in enumerate(numeric):
        for second in numeric[first_index + 1 :]:
            aligned_x = abs(0.5 * (first.x0 + first.x1 - second.x0 - second.x1)) <= 8.0
            aligned_y = abs(0.5 * (first.y0 + first.y1 - second.y0 - second.y1)) <= 8.0
            separated = max(abs(first.x0 - second.x0), abs(first.y0 - second.y0)) >= 18.0
            pair_x = 0.25 * (first.x0 + first.x1 + second.x0 + second.x1)
            pair_y = 0.25 * (first.y0 + first.y1 + second.y0 + second.y1)
            owns_axis_edge = (
                aligned_y and pair_y >= y0 + 0.68 * height
            ) or (
                aligned_x and (pair_x <= x0 + 0.25 * width or pair_x >= x0 + 0.75 * width)
            )
            if separated and owns_axis_edge:
                return True
    return False


def words_in_bbox(words: list[_WordLike], bbox: BBox) -> list[_WordLike]:
    x0, y0, x1, y1 = bbox
    return [
        word
        for word in words
        if x0 <= (word.x0 + word.x1) / 2 <= x1
        and y0 <= (word.y0 + word.y1) / 2 <= y1
    ]


def bbox_looks_like_spec_table(
    words: list[_WordLike],
    bbox: BBox,
    own_families: frozenset[str],
    normalize: Callable[[str], str],
) -> bool:
    """Return whether a candidate region carries spec-table evidence."""
    selected = words_in_bbox(words, bbox)
    tokens = {normalize(word.text) for word in selected}
    if len(_SPEC_TABLE_MARKERS & tokens) >= 3:
        return True
    compact = "".join(
        normalize(word.text) for word in sorted(selected, key=lambda word: (word.y0, word.x0))
    )
    if "productsummary" in compact and {"value", "unit"} <= tokens:
        return True
    section_headers = sum(
        marker in compact
        for marker in (
            "offcharacteristics",
            "oncharacteristics",
            "dynamiccharacteristics",
            "switchingcharacteristics",
        )
    )
    if section_headers >= 2:
        return True
    if any(phrase in compact for phrase in _SWITCHING_TIME_PHRASES) and any(
        phrase in compact for phrase in _CHARGE_ROW_PHRASES
    ):
        return True
    if any(phrase in compact for phrase in _CHARGE_ROW_PHRASES) and sum(
        phrase in compact for phrase in _CAPACITANCE_ROW_PHRASES
    ) >= 2:
        return True
    return len((_SPEC_TABLE_FAMILIES - own_families) & tokens) >= 4


def bbox_evidences_breakdown(
    words: list[_WordLike], bbox: BBox, normalize: Callable[[str], str]
) -> bool:
    x0, y0, x1, y1 = bbox
    # Plot-region morphology starts at the grid, while the V(BR)DSS label is
    # printed in the plot's left axis gutter.  Include only that local gutter;
    # expanding vertically or to the right would admit neighboring panels.
    compact = "".join(
        normalize(word.text)
        for word in words_in_bbox(words, (max(0.0, x0 - 42.0), y0, x1, y1))
    )
    return "breakdown" in compact or "bvdss" in compact or "vbrdss" in compact


def expand_breakdown_bbox_to_axis_label(
    words: list[_WordLike], bbox: BBox, normalize: Callable[[str], str]
) -> BBox:
    """Include a source-owned V(BR)DSS label in the local left gutter."""
    x0, y0, x1, y1 = bbox
    gutter = [
        word for word in words
        if x0 - 46.0 <= word.x0 <= x0 + 12.0
        and y0 <= 0.5 * (word.y0 + word.y1) <= y1
    ]
    if not any(normalize(word.text) in {"brdss", "vbrdss"} for word in gutter):
        return bbox
    owned = [
        word for word in gutter
        if normalize(word.text) in {"v", "brdss", "vbrdss", "normalized"}
    ]
    return max(0.0, min(word.x0 for word in owned) - 2.0), y0, x1, y1


def revision_history_region(
    words: list[_WordLike], normalize: Callable[[str], str]
) -> BBox | None:
    """Return the contiguous revision-table band, not the whole page.

    Revision tables often quote chart titles.  Treating the mere presence of
    a revision header as a page-wide veto loses real charts on mixed pages.
    The table itself is a compact run of text rows headed by ``Revision
    history`` and ``Date / Revision / Changes``; stop at the first substantial
    vertical gap so an unrelated chart below remains eligible.
    """
    if not words:
        return None
    compact = "".join(normalize(word.text) for word in words)
    if not (
        "documentrevisionhistory" in compact
        or "revisionhistorydaterevisionchanges" in compact
    ):
        return None
    rows: list[list[_WordLike]] = []
    for word in sorted(words, key=lambda item: (item.y0, item.x0)):
        center = 0.5 * (word.y0 + word.y1)
        for row in reversed(rows[-6:]):
            row_center = sum(0.5 * (item.y0 + item.y1) for item in row) / len(row)
            if abs(center - row_center) <= 5.0:
                row.append(word)
                break
        else:
            rows.append([word])
    header_index = next(
        (
            index
            for index, row in enumerate(rows)
            if "revisionhistory" in "".join(normalize(item.text) for item in row)
        ),
        None,
    )
    if header_index is None:
        return None
    selected = [rows[header_index]]
    last_bottom = max(word.y1 for word in selected[-1])
    for row in rows[header_index + 1 :]:
        row_top = min(word.y0 for word in row)
        if row_top - last_bottom > 36.0:
            break
        selected.append(row)
        last_bottom = max(word.y1 for word in row)
    band = [word for row in selected for word in row]
    return (
        min(word.x0 for word in band),
        min(word.y0 for word in band),
        max(word.x1 for word in band),
        max(word.y1 for word in band),
    )


def page_looks_like_revision_history(
    words: list[_WordLike], normalize: Callable[[str], str]
) -> bool:
    """Report whether a source contains a revision-table region."""
    return revision_history_region(words, normalize) is not None


def caption_axis_direction(
    page: _PageLike,
    title: _TitleLike,
    kind: str,
    normalize: Callable[[str], str],
) -> str | None:
    """Infer whether a transfer/diode caption leads or follows its plot.

    Numbered captions are commonly printed below a plot, but some two-column
    datasheets place them immediately above it.  Only flip that legacy
    direction when the claimed chart's own x-axis label is found in the same
    column; a nearby foreign plot is not sufficient evidence.
    """
    expected = _CAPTION_AXIS_TOKENS.get(kind)
    if expected is None:
        return None
    if kind == "body_diode" and _body_diode_current_axis_bbox(
        page, title, normalize
    ) is not None:
        return "below"
    tx0, ty0, tx1, ty1 = title.bbox_pt
    tcx = 0.5 * (tx0 + tx1)
    tcy = 0.5 * (ty0 + ty1)
    max_axis_gap = 340.0 if kind in {"breakdown_voltage", "capacitances"} else 260.0
    candidates: list[tuple[float, float]] = []
    # Caption prose can spell the same semantic token as its axis (for
    # example ``... versus junction temperature``).  Its own words are not
    # independent direction evidence: self-matching masks a real axis on the
    # other side. Exclude every caption-overlapping word before joining split
    # glyphs or ranking candidates.
    words = sorted(
        (
            word
            for word in page.words
            if bbox_iou(
                (word.x0, word.y0, word.x1, word.y1), title.bbox_pt
            )
            == 0.0
        ),
        key=lambda word: (word.y0, word.x0),
    )
    for index, word in enumerate(words):
        token = _combined_axis_token(words, index, normalize)
        if token not in expected:
            continue
        if not _axis_unit_evidenced(words, index, kind):
            continue
        wcx = 0.5 * (word.x0 + word.x1)
        wcy = 0.5 * (word.y0 + word.y1)
        if abs(wcx - tcx) > page.width_pt * 0.22 or abs(wcy - tcy) > max_axis_gap:
            continue
        candidates.append((abs(wcy - tcy), wcy))
    if not candidates:
        return None
    axis_y = min(candidates)[1]
    if axis_y > ty1:
        return "below"
    if axis_y < ty0:
        return "above"
    return None


def caption_leads_nearer_grid(
    candidates: list[tuple[float, float, BBox]], title_top: float, title_bottom: float
) -> bool:
    """Return true when one following grid is clearly the caption's own.

    This is a geometry fallback for raster/vector panels whose axis glyphs are
    not text-extractable.  It requires a single grid immediately below the
    caption and a material distance advantage over every preceding grid.
    """
    below = [item for item in candidates if item[2][1] >= title_bottom]
    above = [item for item in candidates if item[2][3] <= title_top]
    return bool(
        len(below) == 1
        and below[0][1] <= 28.0
        and (not above or below[0][1] + 8.0 < min(item[1] for item in above))
    )


def compact_formula_caption_direction(
    candidates: list[tuple[float, float, BBox]], title: _TitleLike
) -> str | None:
    """Own only a materially nearer plot beside a compact formula caption."""

    above = [item[1] for item in candidates if item[2][3] <= title.bbox_pt[1]]
    below = [item[1] for item in candidates if item[2][1] >= title.bbox_pt[3]]
    above_gap = min(above, default=float("inf"))
    below_gap = min(below, default=float("inf"))
    if below_gap <= 36.0 and below_gap + 8.0 < above_gap:
        return "below"
    if above_gap <= 36.0:
        # Toshiba puts these captions below the chart with spacing that is
        # nearly symmetric to the following row.  Only clear contrary
        # evidence may reverse that source convention.
        return "above"
    if below_gap <= 36.0:
        return "below"
    return None


def breakdown_symbol_caption_direction(
    candidates: list[tuple[float, float, BBox]], title: _TitleLike
) -> str | None:
    """Prefer a directly following V(BR)DSS grid over a remote neighbour axis."""
    compact = re.sub(r"[^a-z0-9]+", "", title.title.lower())
    if "vbrdss" not in compact:
        return None
    return "below" if caption_leads_nearer_grid(
        candidates, title.bbox_pt[1], title.bbox_pt[3]
    ) else None


def caption_leading_plot_bbox(
    page: _PageLike,
    title: _TitleLike,
    kind: str,
    normalize: Callable[[str], str],
) -> BBox | None:
    """Return a source-bounded synthetic crop when a caption leads its plot."""
    if caption_axis_direction(page, title, kind, normalize) != "below":
        return None
    if kind == "body_diode":
        current_axis_bbox = _body_diode_current_axis_bbox(
            page, title, normalize
        )
        if current_axis_bbox is not None:
            return current_axis_bbox
    tx0, _, tx1, ty1 = title.bbox_pt
    tcx = 0.5 * (tx0 + tx1)
    expected = _CAPTION_AXIS_TOKENS[kind]
    axis_bottoms: list[float] = []
    words = sorted(page.words, key=lambda word: (word.y0, word.x0))
    for index, word in enumerate(words):
        token = _combined_axis_token(words, index, normalize)
        matched = token in expected and _axis_unit_evidenced(words, index, kind)
        if (
            matched
            and ty1 < word.y1 <= ty1 + 280.0
            and abs(0.5 * (word.x0 + word.x1) - tcx) <= page.width_pt * 0.22
        ):
            axis_bottoms.append(word.y1)
    if not axis_bottoms:
        return None
    # The first own-axis label closes this chart.  Taking the farthest match
    # silently swallows later same-column panels (for example a second VGS
    # label in the body-diode chart below a transfer chart).
    axis_bottom = min(axis_bottoms)
    half_width = min(max(170.0, (tx1 - tx0) * 0.80), page.width_pt * 0.40)
    x0 = max(0.0, tcx - half_width)
    if tcx > page.width_pt * 0.60:
        x0 = max(x0, page.width_pt * 0.48)
    return x0, ty1 + 2.0, min(page.width_pt, tcx + half_width), min(page.height_pt, axis_bottom + 6.0)


def _body_diode_current_axis_bbox(
    page: _PageLike,
    title: _TitleLike,
    normalize: Callable[[str], str],
) -> BBox | None:
    """Own a caption-leading diode plot whose horizontal axis is current.

    Transposed source-diode charts put VSD on Y and ISD/IF on X.  The current
    glyph alone is also common in operating-condition callouts, so require its
    ampere unit plus a local row of at least three aligned numeric ticks.
    """

    tx0, _ty0, tx1, ty1 = title.bbox_pt
    tcx = 0.5 * (tx0 + tx1)
    words = sorted(page.words, key=lambda word: (word.y0, word.x0))
    candidates: list[tuple[float, BBox]] = []
    numeric = re.compile(r"[+-]?\d+(?:\.\d+)?")
    for index, word in enumerate(words):
        token = _combined_axis_token(words, index, normalize)
        if token not in _BODY_DIODE_CURRENT_AXIS_TOKENS:
            continue
        if not _axis_unit_evidenced(words, index, "body_diode"):
            continue
        axis_cy = 0.5 * (word.y0 + word.y1)
        if not ty1 < axis_cy <= ty1 + 280.0:
            continue
        if abs(0.5 * (word.x0 + word.x1) - tcx) > page.width_pt * 0.22:
            continue
        tick_row = [
            tick
            for tick in words
            if numeric.fullmatch(tick.text.strip())
            and abs(0.5 * (tick.y0 + tick.y1) - axis_cy) <= 6.0
            and word.x0 - 190.0 <= 0.5 * (tick.x0 + tick.x1) <= word.x1 + 40.0
        ]
        tick_centers = sorted({round(0.5 * (tick.x0 + tick.x1), 2) for tick in tick_row})
        if len(tick_centers) < 3 or tick_centers[-1] - tick_centers[0] < 60.0:
            continue
        same_line = [
            item
            for item in words
            if abs(0.5 * (item.y0 + item.y1) - axis_cy) <= 6.0
            and word.x0 - 4.0 <= item.x0 <= word.x1 + 45.0
        ]
        x0 = max(0.0, min(tx0, min(tick.x0 for tick in tick_row)) - 10.0)
        x1 = min(
            page.width_pt,
            max(tx1, max(item.x1 for item in same_line), max(tick.x1 for tick in tick_row))
            + 8.0,
        )
        y1 = min(page.height_pt, max(item.y1 for item in same_line) + 6.0)
        candidates.append((axis_cy - ty1, (x0, ty1 + 2.0, x1, y1)))
    return min(candidates, default=(0.0, None), key=lambda item: item[0])[1]


def page_image_rects(pdf: Path, page_num: int) -> list[BBox]:
    """Return figure-sized embedded-image rectangles on a page, in points."""
    try:
        with pymupdf.open(pdf) as doc:
            infos = doc[page_num - 1].get_image_info()
    except Exception:
        return []
    rects: list[BBox] = []
    for info in infos:
        x0, y0, x1, y1 = (
            float(value) for value in info.get("bbox", (0.0, 0.0, 0.0, 0.0))
        )
        if x1 - x0 >= 90.0 and y1 - y0 >= 90.0:
            rects.append((x0, y0, x1, y1))
    return rects


def page_vector_plot_frames(
    pdf: Path, page_num: int, page: _PageLike, *, include_quadrilaterals: bool = True
) -> list[BBox]:
    """Return positively evidenced stroked vector plot frames on one page."""
    try:
        with pymupdf.open(pdf) as doc:
            drawings = doc[page_num - 1].get_drawings()
    except (OSError, RuntimeError, ValueError, IndexError):
        return []

    min_width = page.width_pt * 0.14
    max_width = page.width_pt * 0.48
    min_height = page.height_pt * 0.08
    max_height = page.height_pt * 0.40
    frames: list[BBox] = []
    for drawing in drawings:
        if drawing.get("type") not in {"s", "fs"}:
            continue
        stroke_width = float(drawing.get("width") or 0.0)
        if not 0.45 <= stroke_width <= 2.5:
            continue
        for item in drawing.get("items", []):
            if item[0] == "re":
                rect = item[1]
            elif item[0] == "qu" and include_quadrilaterals and item[1].is_rectangular:
                rect = item[1].rect
            else:
                continue
            width = float(rect.width)
            height = float(rect.height)
            if not (
                min_width <= width <= max_width
                and min_height <= height <= max_height
            ):
                continue
            aspect = width / max(height, 1e-9)
            if not 0.55 <= aspect <= 2.4:
                continue
            candidate = (
                float(rect.x0),
                float(rect.y0),
                float(rect.x1),
                float(rect.y1),
            )
            if any(bbox_iou(candidate, existing) >= 0.92 for existing in frames):
                continue
            frames.append(candidate)
    return sorted(frames, key=lambda box: (box[1], box[0]))


def caption_vector_frame_bbox(
    page: _PageLike,
    title: _TitleLike,
    frames: list[BBox],
    *,
    numbered_caption: bool,
    tight: bool = False,
) -> BBox | None:
    """Bind an unnumbered caption to a nearby same-column vector frame."""
    if numbered_caption:
        return None

    tx0, ty0, tx1, ty1 = title.bbox_pt
    tcx = 0.5 * (tx0 + tx1)
    best: tuple[float, BBox, str] | None = None
    for frame in frames:
        x0, y0, x1, y1 = frame
        width = x1 - x0
        center_x = 0.5 * (x0 + x1)
        if not x0 - 0.25 * width <= tcx <= x1 + 0.25 * width:
            continue
        if ty1 <= y0:
            gap = y0 - ty1
            direction = "below"
        elif y1 <= ty0:
            gap = ty0 - y1
            direction = "above"
        else:
            continue
        if gap > 65.0:
            continue
        score = gap + 0.20 * abs(tcx - center_x)
        if best is None or score < best[0]:
            best = (score, frame, direction)
    if best is None:
        return None

    _, (x0, y0, x1, y1), direction = best
    width = x1 - x0
    height = y1 - y0
    left_pad = min(52.0, 0.36 * width)
    right_pad = min(20.0, 0.15 * width)
    vertical_pad = min(16.0, 0.10 * height) if tight else min(56.0, 0.40 * height)
    if direction == "below":
        crop_y0 = min(ty0 - 2.0, y0 - 8.0)
        crop_y1 = y1 + vertical_pad
    else:
        crop_y0 = y0 - 8.0
        crop_y1 = max(ty1 + 2.0, y1 + vertical_pad)
    return (
        max(0.0, x0 - left_pad),
        max(0.0, crop_y0),
        min(page.width_pt, x1 + right_pad),
        min(page.height_pt, crop_y1),
    )


def numbered_breakdown_vector_frame_bbox(
    page: _PageLike, title: _TitleLike, frames: list[BBox]
) -> BBox | None:
    """Own a directly adjacent closed frame for a numbered breakdown caption.

    ST layouts can place a caption almost equidistant between two raster grids.
    A same-column stroked frame within 30 pt is stronger ownership evidence than
    that unstable raster tie.  Return the frame itself so the shared axis-gutter
    expansion can add only its local ticks and labels afterward.
    """
    tx0, ty0, tx1, ty1 = title.bbox_pt
    title_width = max(1.0, tx1 - tx0)
    candidates: list[tuple[float, int, float, BBox]] = []
    for frame in frames:
        x0, y0, x1, y1 = frame
        overlap = max(0.0, min(tx1, x1) - max(tx0, x0))
        if overlap < min(title_width, x1 - x0) * 0.30:
            continue
        if ty1 <= y0:
            gap, direction_rank = y0 - ty1, 0
        elif y1 <= ty0:
            gap, direction_rank = ty0 - y1, 1
        else:
            continue
        if gap > 30.0:
            continue
        center_delta = abs(0.5 * (tx0 + tx1) - 0.5 * (x0 + x1))
        candidates.append((gap, direction_rank, center_delta, frame))
    if not candidates:
        return None
    nearest_gap = min(item[0] for item in candidates)
    near_tie = [item for item in candidates if item[0] <= nearest_gap + 4.0]
    return min(near_tie, key=lambda item: (item[1], item[0], item[2]))[3]


def caption_image_panel_bbox(
    image_rects: list[BBox], title: _TitleLike, bbox: BBox | None
) -> BBox | None:
    """Bind a caption to the embedded image directly above it, if any."""
    tx0, ty0, tx1, _ = title.bbox_pt
    tcx = (tx0 + tx1) / 2
    best: tuple[float, BBox] | None = None
    for rect in image_rects:
        x0, y0, x1, y1 = rect
        if not x0 <= tcx <= x1:
            continue
        gap = ty0 - y1
        if not -10.0 <= gap <= 70.0:
            continue
        if bbox is not None:
            ix0, iy0 = max(bbox[0], x0), max(bbox[1], y0)
            ix1, iy1 = min(bbox[2], x1), min(bbox[3], y1)
            inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
            area = max(1e-9, (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))
            if inter / area < 0.6:
                continue
        if best is None or gap < best[0]:
            best = (gap, rect)
    return best[1] if best else None


def expand_caption_bbox_to_axis_labels(
    page: _PageLike,
    bbox: BBox,
    number_pattern: re.Pattern[str],
    kind: str | None = None,
) -> BBox:
    """Grow a grid bbox to include nearby numeric axis-label gutters."""
    x0, y0, x1, y1 = bbox
    cy_lo = y0 - 4.0
    left_cy_hi = y1 - min(18.0, 0.08 * (y1 - y0))

    def numeric(word: _WordLike) -> bool:
        return bool(number_pattern.fullmatch(word.text.strip()))

    has_left_labels = 2 <= sum(
        1
        for word in page.words
        if word.x0 >= x0
        and word.x1 <= x1
        and numeric(word)
        and word.x0 <= x0 + 40.0
        and cy_lo <= (word.y0 + word.y1) / 2 <= left_cy_hi
    )
    has_bottom_labels = 2 <= sum(
        1
        for word in page.words
        if word.y0 >= y0
        and word.y1 <= y1
        and numeric(word)
        and word.y1 >= y1 - 30.0
        and x0 <= (word.x0 + word.x1) / 2 <= x1
    )
    new_x0, new_y1 = x0, y1
    if not has_left_labels:
        left = [
            word
            for word in page.words
            if word.x0 < x0
            and word.x1 <= x0 + 8.0
            and x0 - word.x0 < 46.0
            and cy_lo <= (word.y0 + word.y1) / 2 <= left_cy_hi
            and numeric(word)
        ]
        # A neighboring same-row chart can put one terminal tick just inside
        # the expansion gutter.  Only an aligned run of at least two labels
        # owns an axis; a lone numeral must not pull the crop across columns.
        left_runs: list[list[_WordLike]] = []
        for word in sorted(left, key=lambda item: item.x1, reverse=True):
            for run in left_runs:
                if abs(word.x1 - run[0].x1) <= 7.0:
                    run.append(word)
                    break
            else:
                left_runs.append([word])
        aligned = [run for run in left_runs if len(run) >= 2]
        if aligned:
            run = max(aligned, key=lambda items: max(word.x1 for word in items))
            new_x0 = min(word.x0 for word in run) - 2.0
    if not has_bottom_labels:
        bottom = [
            word
            for word in page.words
            if word.y0 >= y1 - 2.0
            and word.y0 - y1 < 40.0
            and x0 - 4.0 <= (word.x0 + word.x1) / 2 <= x1 + 4.0
            and numeric(word)
        ]
        if run := _nearest_aligned_bottom_run(bottom):
            new_y1 = max(word.y1 for word in run) + 2.0
    if kind == "rds_on":
        normalize = lambda text: re.sub(r"[^a-z0-9]+", "", text.lower())
        left_words = [
            word
            for word in page.words
            if x0 - 48.0 <= word.x0 <= x0 + 8.0
            and y0 <= 0.5 * (word.y0 + word.y1) <= y1
        ]
        left_tokens = {normalize(word.text) for word in left_words}
        if "normalized" in left_tokens and ({"dson", "r"} <= left_tokens):
            new_x0 = min(new_x0, min(word.x0 for word in left_words) - 3.0)
        bottom_words = [
            word
            for word in page.words
            if y1 - 4.0 <= word.y0 <= y1 + 34.0
            and x0 - 4.0 <= 0.5 * (word.x0 + word.x1) <= x1 + 4.0
        ]
        bottom_tokens = {normalize(word.text) for word in bottom_words}
        if {"junction", "temperature"} <= bottom_tokens:
            new_y1 = max(new_y1, max(word.y1 for word in bottom_words) + 2.0)
    if kind == "transfer":
        normalize = lambda text: re.sub(r"[^a-z0-9]+", "", text.lower())
        left_words = [
            word
            for word in page.words
            if x0 - 48.0 <= word.x0 <= x0 + 8.0
            and y0 <= 0.5 * (word.y0 + word.y1) <= y1
        ]
        left_tokens = {normalize(word.text) for word in left_words}
        has_current_axis = (
            {"draintosource", "current", "a"} <= left_tokens
            or {"i", "d", "a"} <= left_tokens
        )
        if has_current_axis:
            new_x0 = min(new_x0, min(word.x0 for word in left_words) - 3.0)
        bottom_words = [
            word
            for word in page.words
            if y1 - 4.0 <= word.y0 <= y1 + 20.0
            and x0 - 4.0 <= 0.5 * (word.x0 + word.x1) <= x1 + 4.0
        ]
        bottom_tokens = {normalize(word.text) for word in bottom_words}
        has_gate_axis = (
            {"gatetosource", "voltage", "v"} <= bottom_tokens
            or {"v", "gs"} <= bottom_tokens
        )
        if has_gate_axis:
            new_y1 = max(new_y1, max(word.y1 for word in bottom_words) + 2.0)
    if kind == "body_diode":
        normalize = lambda text: re.sub(r"[^a-z0-9]+", "", text.lower())
        left_words = [
            word
            for word in page.words
            if x0 - 50.0 <= word.x0 <= x0 + 8.0
            and y0 <= 0.5 * (word.y0 + word.y1) <= y1
        ]
        left_tokens = {normalize(word.text) for word in left_words}
        has_current_axis = (
            {"sourcetodrain", "current", "a"} <= left_tokens
            or {"i", "sd", "a"} <= left_tokens
        )
        if has_current_axis:
            new_x0 = min(new_x0, min(word.x0 for word in left_words) - 3.0)
        bottom_words = [
            word
            for word in page.words
            if y1 - 4.0 <= word.y0 <= y1 + 30.0
            and x0 - 4.0 <= 0.5 * (word.x0 + word.x1) <= x1 + 4.0
        ]
        bottom_tokens = {normalize(word.text) for word in bottom_words}
        has_voltage_axis = (
            {"sourcetodrain", "voltage", "v"} <= bottom_tokens
            or {"v", "sd"} <= bottom_tokens
        )
        if has_voltage_axis:
            new_y1 = max(new_y1, max(word.y1 for word in bottom_words) + 2.0)
    return max(0.0, new_x0), y0, x1, min(page.height_pt, new_y1)


def expand_numbered_dual_y_gate_bbox(
    page: _PageLike, title: _TitleLike, bbox: BBox
) -> BBox:
    """Include both Y scales and the Qg row on narrow Toshiba dual-Y plots."""

    normalized = re.sub(r"\s+", " ", title.title.lower()).strip()
    numbered = bool(re.match(r"^(?:figure|fig\.?)\s*\d", title.line_text.strip(), re.I))
    x0, y0, x1, y1 = bbox
    width = x1 - x0
    if (
        normalized != "dynamic input/output characteristics"
        or not numbered
        or title.number != 810
        or width > page.width_pt * 0.40
        or y1 > title.bbox_pt[1] + 2.0
    ):
        return bbox
    side_pad = min(42.0, 0.25 * width)
    bottom = min(title.bbox_pt[1] - 1.0, y1 + min(32.0, 0.20 * (y1 - y0)))
    return (
        max(0.0, x0 - side_pad),
        y0,
        min(page.width_pt, x1 + side_pad),
        bottom,
    )


def bound_caption_bbox_to_own_column(
    page: _PageLike,
    title: _TitleLike,
    bbox: BBox,
    *,
    preserve_evidenced_left_gutter: bool = False,
) -> BBox | None:
    """Clamp a caption crop at the page midpoint on evidenced two-column axes.

    The clamp is not inferred from title position alone.  It fires only when
    the crop's bottom axis row carries parenthesized units on both sides of
    the page midpoint, which is positive evidence for adjacent plot columns.
    """
    x0, y0, x1, y1 = bbox
    numeric = re.compile(r"[+-]?\d+(?:\.\d+)?")
    left_ticks = [
        word
        for word in page.words
        if word.x1 <= x0
        and x0 - word.x1 <= 12.0
        and y0 - 4.0 <= 0.5 * (word.y0 + word.y1) <= y1 + 4.0
        and numeric.fullmatch(word.text.strip())
    ]
    aligned_runs: list[list[_WordLike]] = []
    for word in sorted(left_ticks, key=lambda item: item.x1, reverse=True):
        for run in aligned_runs:
            if abs(word.x1 - run[0].x1) <= 7.0:
                run.append(word)
                break
        else:
            aligned_runs.append([word])
    aligned = [run for run in aligned_runs if len(run) >= 2]
    owned_axis_x0: float | None = None
    if aligned:
        run = max(aligned, key=lambda items: max(word.x1 for word in items))
        tick_x0 = min(word.x0 for word in run)
        owned_gutter = [
            word
            for word in page.words
            if tick_x0 - 18.0 <= word.x1 <= tick_x0 + 2.0
            and y0 - 4.0 <= 0.5 * (word.y0 + word.y1) <= y1 + 4.0
        ]
        owned_axis_x0 = min(
            [
                x0,
                min(word.x0 for word in run) - 2.0,
                *(word.x0 - 2.0 for word in owned_gutter),
            ]
        )
        x0 = owned_axis_x0

    midpoint = 0.5 * page.width_pt
    title_center = 0.5 * (title.bbox_pt[0] + title.bbox_pt[2])
    if abs(title_center - midpoint) < 0.05 * page.width_pt:
        return x0, y0, x1, y1
    axis_y = bbox[3]
    unit_re = re.compile(r"(?i)^\((?:v|a|nc|pf|°?c)\)$|^℃$")
    units = [
        word
        for word in page.words
        if abs(0.5 * (word.y0 + word.y1) - axis_y) <= 32.0
        and unit_re.fullmatch(word.text.strip())
    ]
    if not any(0.5 * (word.x0 + word.x1) < midpoint for word in units):
        return x0, y0, x1, y1
    if not any(0.5 * (word.x0 + word.x1) > midpoint for word in units):
        return x0, y0, x1, y1
    if title_center < midpoint:
        x1 = min(x1, midpoint)
    elif not preserve_evidenced_left_gutter:
        # Leave a narrow inter-column moat on the right-hand crop.  The crop
        # transform adds an outward review margin later; starting exactly at
        # the midpoint can therefore re-admit the left panel's terminal tick.
        boundary = midpoint + 0.01 * page.width_pt
        # An aligned run of at least two left-axis labels is stronger evidence
        # than the generic midpoint moat.  Preserve that right-panel gutter;
        # a single terminal tick from the neighboring chart cannot opt in.
        evidenced_boundary = (
            owned_axis_x0 if owned_axis_x0 is not None else boundary
        )
        x0 = max(x0, min(boundary, evidenced_boundary))
    if x1 <= x0:
        return None
    return x0, y0, x1, y1


def bound_caption_bbox_to_caption_row(
    page: _PageLike, title: _TitleLike, bbox: BBox, titles: list[_TitleLike], plot_above: bool = False
) -> BBox | None:
    """Keep a panel inside the column evidenced by neighboring captions."""
    x0, y0, x1, y1 = bbox
    center = 0.5 * (title.bbox_pt[0] + title.bbox_pt[2])
    title_y = 0.5 * (title.bbox_pt[1] + title.bbox_pt[3])
    peers = sorted(
        (
            0.5 * (other.bbox_pt[0] + other.bbox_pt[2]),
            other.bbox_pt,
        )
        for other in titles
        if other is not title
        and abs(0.5 * (other.bbox_pt[1] + other.bbox_pt[3]) - title_y) <= 8.0
        and abs(0.5 * (other.bbox_pt[0] + other.bbox_pt[2]) - center) >= 0.20 * page.width_pt
    )
    left = max((item for item in peers if item[0] < center), default=None)
    right = min((item for item in peers if item[0] > center), default=None)
    moat = 0.01 * page.width_pt
    if left is not None:
        boundary = left[1][2]
        if x0 < boundary:
            x0 = max(x0, boundary + moat)
    if right is not None:
        boundary = right[1][0]
        if x1 > boundary:
            x1 = min(x1, boundary - moat)
    if plot_above:
        prior_bottoms = [
            other.bbox_pt[3]
            for other in titles
            if other is not title
            and abs(
                0.5 * (other.bbox_pt[0] + other.bbox_pt[2]) - center
            ) < 0.20 * page.width_pt
            and bbox_overlap_fraction_of_smaller(
                (title.bbox_pt[0], 0.0, title.bbox_pt[2], 1.0),
                (other.bbox_pt[0], 0.0, other.bbox_pt[2], 1.0),
            ) >= 0.30
            and y0 <= other.bbox_pt[3] < title.bbox_pt[1]
        ]
        if prior_bottoms:
            y0 = max(y0, max(prior_bottoms) + 2.0)
    else:
        next_tops = [
            other.bbox_pt[1]
            for other in titles
            if other is not title
            and 0.5 * (other.bbox_pt[0] + other.bbox_pt[2])
            - 0.20 * page.width_pt < center
            < 0.5 * (other.bbox_pt[0] + other.bbox_pt[2])
            + 0.20 * page.width_pt
            and title.bbox_pt[3] < other.bbox_pt[1] <= y1
        ]
        if next_tops:
            y1 = min(y1, min(next_tops) - 2.0)
    return None if x1 <= x0 or y1 <= y0 else (x0, y0, x1, y1)
