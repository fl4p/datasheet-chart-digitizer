"""Bounded OCR recovery for raster gate-charge chart axes."""

from __future__ import annotations

import math
import re
import subprocess
from pathlib import Path

import numpy as np
import pymupdf

from .charge_units import gate_charge_unit
from .find_charts import ChartPanel, PageText, Word
from .gate_charge_estimation import (
    _best_x_axis_for_panel,
    _drop_glyph_offset_tick,
    _local_y_ticks_for_plot,
    _parse_numeric_label,
)
from .region_ocr import (
    ocr_words_in_poppler_page_rect,
    ocr_words_in_rect,
    ocr_words_on_page_poppler,
)


BOUNDED_AXIS_OCR_DPI = 900.0
UNIT_AXIS_OCR_DPI = 1200.0
DUAL_Y_AXIS_OCR_DPI = 800.0
CONTEXT_AXIS_OCR_DPI = 180.0
MAX_PLOT_BOX_ASPECT = 1.75


class _OcrWordPage:
    """Expose OCR words through the small page API used by axis parsers."""

    text_source = "tesseract_bounded_gate_axes"

    def __init__(self, words: list[Word]):
        self._words = [
            (word.x0, word.y0, word.x1, word.y1, word.text)
            for word in words
        ]

    def get_text(self, option: str, *args, **kwargs):
        if option == "words":
            return self._words
        return ""


def poppler_low_text_page_text(pdf: Path) -> list[PageText]:
    """OCR image-only pages without paying for another full-document pass."""

    pages: list[PageText] = []
    with pymupdf.open(pdf) as doc:
        for page_num, page in enumerate(doc, start=1):
            if len(page.get_text("words")) >= 30:
                continue
            try:
                raw = ocr_words_on_page_poppler(
                    pdf,
                    page_num,
                    dpi=180.0,
                    psm=6,
                    min_confidence=10.0,
                )
            except (OSError, RuntimeError, subprocess.SubprocessError):
                continue
            words = _words(raw)
            pages.append(
                PageText(
                    page_num,
                    float(page.rect.width),
                    float(page.rect.height),
                    words,
                    "tesseract_fallback",
                )
            )
    return pages


def plot_box_aspect_implausible(
    plot_box: tuple[int, int, int, int],
) -> bool:
    """Return whether one alleged plot spans implausibly stacked panels."""

    x0, y0, x1, y1 = plot_box
    width = x1 - x0
    return width > 0 and (y1 - y0) / width > MAX_PLOT_BOX_ASPECT


def vpl_extrapolated_beyond_ticks(
    vpl_y_px: float | None,
    local_y_ticks: list[tuple[float, float]],
    crop_rect,
    scale: float,
) -> bool:
    """Return whether the plateau lies beyond the measured Y-tick span."""

    if vpl_y_px is None or len(local_y_ticks) < 2:
        return False
    tick_px = [
        float((y - crop_rect.y0) * scale)
        for _value, y in local_y_ticks
    ]
    lo, hi = min(tick_px), max(tick_px)
    span = hi - lo
    if span <= 0:
        return False
    slack = 0.10 * span
    return not lo - slack <= float(vpl_y_px) <= hi + slack


def depletion_vpl_is_source_plausible(
    vpl: float | None,
    vpl_y_px: float | None,
    curve: list[tuple[int, int]],
    plot_box: tuple[int, int, int, int],
    y_ticks: list[tuple[float, float]],
) -> bool:
    """Recognize a low depletion-mode plateau without widening normal limits."""

    if vpl is None or vpl_y_px is None or len(curve) < 20 or len(y_ticks) < 3:
        return False
    values = np.asarray([value for value, _pixel in y_ticks], dtype=float)
    pixels = np.asarray([pixel for _value, pixel in y_ticks], dtype=float)
    if not (np.min(values) < 0.0 < np.max(values)):
        return False
    slope, offset = np.polyfit(pixels, values, 1)
    first_y = min(curve, key=lambda point: point[0])[1]
    if float(slope * first_y + offset) >= -0.25:
        return False
    if not np.min(values) <= float(vpl) <= np.max(values):
        return False

    nearby_x = sorted(
        x for x, y in curve
        if abs(float(y) - float(vpl_y_px)) <= 2.0
    )
    longest_span = 0
    if nearby_x:
        start = previous = nearby_x[0]
        for x in nearby_x[1:]:
            if x - previous > 3:
                longest_span = max(longest_span, previous - start)
                start = x
            previous = x
        longest_span = max(longest_span, previous - start)
    plot_width = max(1, plot_box[2] - plot_box[0])
    return longest_span >= 0.05 * plot_width


def needs_dual_y_axis_ocr(panel: ChartPanel, result) -> bool:
    title = re.sub(r"\s+", " ", panel.title.lower()).strip()
    diagnostics = getattr(result, "diagnostics", ())
    axis_problem = result.status in {"axis_assumed", "axis_grid_inferred"} or any(
        item in diagnostics
        for item in (
            "axis_assumed_0_10",
            "axis_inferred_from_regular_grid",
            "gate_charge_unit_unresolved",
        )
    )
    return (
        panel.diagram < 900
        and title == "dynamic input/output characteristics"
        and axis_problem
    )


def needs_bounded_axis_ocr(result) -> bool:
    """Retry only when the first pass names a calibration defect."""

    if "axis_ocr_bounded_dual_y" in getattr(result, "diagnostics", ()):
        return False
    calibration_diagnostics = {
        "axis_assumed_0_10",
        "axis_inferred_from_regular_grid",
        "gate_charge_unit_unresolved",
        "vpl_extrapolated_beyond_ticks",
        "vpl_outside_expected_range",
        "plot_box_aspect_implausible",
        "low_trace_confidence",
        "curve_missing_initial_ramp",
        "curve_missing_axis_origin",
    }
    return bool(
        calibration_diagnostics.intersection(
            getattr(result, "diagnostics", ())
        )
    )


def bounded_axis_result_is_trusted(result) -> bool:
    """Require a complete physical result before replacing the first pass."""

    return (
        result is not None
        and result.status == "ok"
        and result.y_tick_count >= 2
        and result.x_tick_unit is not None
        and result.vpl is not None
        and 1.0 <= abs(float(result.vpl)) <= 12.0
    )


def dual_y_axis_ocr_page(
    pdf: Path,
    doc: pymupdf.Document,
    panel: ChartPanel,
    result,
) -> PageText | None:
    """OCR only the owned right-VGS and bottom-Qg label bands."""

    page = doc[panel.page - 1]
    crop = pymupdf.Rect(result.crop_box_pt)
    scale = result.dpi / 72.0
    px0, py0, px1, py1 = result.plot_box_px
    plot = pymupdf.Rect(
        crop.x0 + px0 / scale,
        crop.y0 + py0 / scale,
        crop.x0 + px1 / scale,
        crop.y0 + py1 / scale,
    )
    owner = pymupdf.Rect(panel.bbox_pt)
    # The first pass can bind only part of the raster Qg span (TPH5R60APL:
    # box ends at the 40 nC tick of a 0..60 axis), leaving the true right
    # frame and its VGS labels ~55 pt past plot.x1. Reach as far right as the
    # bounded retry does; the linear-run selectors drop what is not an axis.
    right_band = pymupdf.Rect(
        plot.x1 - 30.0,
        max(page.rect.y0, owner.y0 - 8.0),
        min(page.rect.x1, plot.x1 + 75.0),
        min(page.rect.y1, owner.y1 + 6.0),
    )
    bottom_band = pymupdf.Rect(
        max(page.rect.x0, plot.x0 - 18.0),
        max(page.rect.y0, plot.y1 - 16.0),
        min(page.rect.x1, plot.x1 + 75.0),
        min(page.rect.y1, owner.y1 + 20.0),
    )
    try:
        right_sparse = ocr_words_in_rect(
            pdf, panel.page, right_band, dpi=DUAL_Y_AXIS_OCR_DPI, psm=11
        )
        right_block = ocr_words_in_rect(
            pdf, panel.page, right_band, dpi=DUAL_Y_AXIS_OCR_DPI, psm=6
        )
        right_poppler = ocr_words_in_poppler_page_rect(
            pdf, panel.page, right_band, psm=6
        )
        right_source = max(
            (right_sparse, right_block, right_poppler),
            key=lambda raw: len(
                _raw_linear_y_ticks(_words(raw), owner)
            ),
        )
        raw_words = [
            *right_source,
            *ocr_words_in_rect(
                pdf, panel.page, bottom_band, dpi=DUAL_Y_AXIS_OCR_DPI, psm=6
            ),
            *ocr_words_in_poppler_page_rect(
                pdf, panel.page, bottom_band, psm=6
            ),
        ]
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return None
    words = _words(raw_words)
    if gate_charge_unit(" ".join(word.text for word in words)) is None:
        bottom_band.y1 = min(page.rect.y1, owner.y1 + 30.0)
        try:
            raw_words = [
                *right_source,
                *ocr_words_in_rect(
                    pdf, panel.page, bottom_band, dpi=DUAL_Y_AXIS_OCR_DPI, psm=6
                ),
                *ocr_words_in_poppler_page_rect(
                    pdf, panel.page, bottom_band, psm=6
                ),
            ]
        except (OSError, RuntimeError, subprocess.SubprocessError):
            return None
        words = _words(raw_words)
        if gate_charge_unit(" ".join(word.text for word in words)) is None:
            return None
    return PageText(
        panel.page,
        float(page.rect.width),
        float(page.rect.height),
        words,
        "tesseract_fallback",
    )


def _words(raw: list[tuple]) -> list[Word]:
    return [
        Word(
            str(item[4]),
            float(item[0]),
            float(item[1]),
            float(item[2]),
            float(item[3]),
        )
        for item in raw
        if len(item) >= 5
    ]


def _native_words_in_rect(
    page: pymupdf.Page,
    rect: pymupdf.Rect,
) -> list[tuple[float, float, float, float, str]]:
    """Return native words whose centers lie in one bounded source band."""

    out = []
    for word in page.get_text("words"):
        x0, y0, x1, y1 = (float(value) for value in word[:4])
        if rect.contains((0.5 * (x0 + x1), 0.5 * (y0 + y1))):
            out.append((x0, y0, x1, y1, str(word[4])))
    return out


def _linear_axis_is_evidenced(ticks: list[tuple[float, float]]) -> bool:
    if len(ticks) < 2:
        return False
    values = np.asarray([value for value, _y in ticks], dtype=float)
    rows = np.asarray([y for _value, y in ticks], dtype=float)
    if np.ptp(values) < 1.0 or np.ptp(rows) < 15.0:
        return False
    slope, offset = np.polyfit(rows, values, 1)
    residual = float(np.max(np.abs(values - (slope * rows + offset))))
    return (
        slope < 0.0
        and residual <= max(0.25, 0.06 * float(np.ptp(values)))
        and -20.0 <= float(np.min(values))
        and float(np.max(values)) <= 30.0
    )


def _dedupe_axis_ticks(
    ticks: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Keep one source row per value, preferring the common linear fit."""

    if len(ticks) < 2:
        return ticks
    values = np.asarray([value for value, _y in ticks], dtype=float)
    rows = np.asarray([y for _value, y in ticks], dtype=float)
    slope, offset = np.polyfit(rows, values, 1)
    grouped: dict[float, list[tuple[float, float]]] = {}
    for value, y in ticks:
        grouped.setdefault(value, []).append((value, y))
    return sorted(
        (
            min(
                group,
                key=lambda item: abs(item[0] - (slope * item[1] + offset)),
            )
            for group in grouped.values()
        ),
        key=lambda item: item[1],
    )


def _axis_quality(ticks: list[tuple[float, float]]) -> tuple[int, float, float, float]:
    """Rank a calibration by fit quality before OCR word count."""

    unique = _dedupe_axis_ticks(ticks)
    if len(unique) < 2:
        return (0, -1e9, 0.0, 0.0)
    values = np.asarray([value for value, _y in unique], dtype=float)
    rows = np.asarray([y for _value, y in unique], dtype=float)
    slope, offset = np.polyfit(rows, values, 1)
    value_span = float(np.ptp(values))
    residual = float(np.max(np.abs(values - (slope * rows + offset))))
    return (
        len(unique),
        -residual / max(1.0, value_span),
        float(np.ptp(rows)),
        value_span,
    )


def _raw_linear_y_ticks(
    words: list[Word],
    plot: pymupdf.Rect,
) -> list[tuple[float, float]]:
    """Choose the largest linear OCR subset, leaving decimal-loss outliers out."""

    points = []
    for word in words:
        token = word.text.strip()
        if re.fullmatch(r"[0-9oOuU]+", token):
            token = token.replace("O", "0").replace("o", "0")
            token = token.replace("U", "0").replace("u", "0")
        value = _parse_numeric_label(token)
        y = 0.5 * (word.y0 + word.y1)
        if (
            value is not None
            and -20.0 <= value <= 30.0
            and plot.y0 - 10.0 <= y <= plot.y1 + 10.0
        ):
            points.append((float(value), float(y)))
    points = list(dict.fromkeys((round(value, 6), round(y, 3)) for value, y in points))
    best: tuple[
        int, float, float, float, list[tuple[float, float]]
    ] | None = None
    for first_index, (first_value, first_y) in enumerate(points):
        for second_value, second_y in points[first_index + 1 :]:
            if abs(second_y - first_y) < 15.0:
                continue
            slope = (second_value - first_value) / (second_y - first_y)
            if slope >= -1e-6:
                continue
            offset = first_value - slope * first_y
            inliers = [
                (value, y)
                for value, y in points
                if abs(value - (slope * y + offset)) <= 0.35
            ]
            if len(inliers) < 2:
                continue
            values = np.asarray([value for value, _y in inliers], dtype=float)
            rows = np.asarray([y for _value, y in inliers], dtype=float)
            fit_slope, fit_offset = np.polyfit(rows, values, 1)
            if fit_slope >= -1e-6:
                continue
            residual = float(
                np.max(np.abs(values - (fit_slope * rows + fit_offset)))
            )
            ordered = sorted(inliers, key=lambda item: item[1])
            score = (
                len(ordered),
                float(np.ptp(rows)),
                float(np.ptp(values)),
                -residual,
            )
            if best is None or score > best[:4]:
                best = (*score, ordered)
    return [] if best is None else best[4]


def _axis_ticks_from_band(
    words: list[Word],
    crop: pymupdf.Rect,
    scale: float,
    plot_box: tuple[int, int, int, int],
    plot: pymupdf.Rect,
) -> list[tuple[float, float]]:
    local = _local_y_ticks_for_plot(
        _OcrWordPage(words), crop, scale, plot_box
    )
    raw = _raw_linear_y_ticks(words, plot)
    candidates = [ticks for ticks in (local, raw) if _linear_axis_is_evidenced(ticks)]
    selected = max(candidates, key=_axis_quality, default=[])
    return _dedupe_axis_ticks(selected)


def _x_axis_quality(ticks: list[tuple[float, float]]) -> tuple[int, float]:
    if len(ticks) < 2:
        return (0, -1e9)
    ordered = sorted(ticks)
    values = np.asarray([value for value, _x in ordered], dtype=float)
    xs = np.asarray([x for _value, x in ordered], dtype=float)
    if np.any(np.diff(values) <= 0.0) or np.any(np.diff(xs) <= 0.0):
        return (0, -1e9)
    typical_step = float(np.median(np.diff(values)))
    slope, offset = np.polyfit(values, xs, 1)
    residual = float(np.max(np.abs(xs - (slope * values + offset))))
    return (
        int(typical_step > 0.0 and values[0] <= 1.5 * typical_step),
        -residual,
    )


def _best_bottom_words(
    raw_candidates: list[list[tuple[float, float, float, float, str]]],
    panel_rect: pymupdf.Rect,
) -> tuple[list[Word], list[tuple[float, float]], float | None]:
    best: tuple[
        int,
        int,
        int,
        float,
        int,
        list[Word],
        list[tuple[float, float]],
        float | None,
    ] | None = None
    for raw in raw_candidates:
        words = [
            word
            for word in _words(raw)
            if panel_rect.contains(
                (0.5 * (word.x0 + word.x1), 0.5 * (word.y0 + word.y1))
            )
        ]
        axis = _best_x_axis_for_panel(_OcrWordPage(words), panel_rect)
        ticks = [] if axis is None else axis[0]
        unit_evidence = int(
            gate_charge_unit(" ".join(word.text for word in words)) is not None
        )
        row_y = None if axis is None else float(axis[1])
        candidate = (
            int(len(ticks) >= 3),
            unit_evidence,
            *_x_axis_quality(ticks),
            len(ticks),
            words,
            ticks,
            row_y,
        )
        if best is None or candidate[:5] > best[:5]:
            best = candidate
    assert best is not None
    return best[5], best[6], best[7]


def _charge_unit_tick_band(
    raw_candidates: list[list[tuple[float, float, float, float, str]]],
    search_rect: pymupdf.Rect,
    page_rect: pymupdf.Rect,
) -> pymupdf.Rect | None:
    """Return a narrow numeric-row crop anchored by a local charge unit."""

    def is_charge_unit(word: Word) -> bool:
        return gate_charge_unit(word.text) is not None

    units = [
        word
        for raw in raw_candidates
        for word in _words(raw)
        if search_rect.contains(
            (0.5 * (word.x0 + word.x1), 0.5 * (word.y0 + word.y1))
        )
        and is_charge_unit(word)
    ]
    if not units:
        return None
    unit = max(units, key=lambda word: (word.y1, word.x1))
    row_y = 0.5 * (unit.y0 + unit.y1)
    return pymupdf.Rect(
        max(page_rect.x0, search_rect.x0),
        max(page_rect.y0, row_y - 11.0),
        min(page_rect.x1, max(search_rect.x1, unit.x1 + 5.0)),
        min(page_rect.y1, row_y + 11.0),
    )


def _gate_x_tick_edges(
    ticks: list[tuple[float, float]],
    plot: pymupdf.Rect,
    horizontal_extra: float,
) -> tuple[float, float] | None:
    """Return evidenced Qg plot edges before looking for either Y axis."""

    if len(ticks) < 3:
        return None
    ordered = sorted(ticks)
    values = np.asarray([value for value, _x in ordered], dtype=float)
    xs = np.asarray([x for _value, x in ordered], dtype=float)
    if np.any(np.diff(values) <= 0.0) or np.any(np.diff(xs) <= 0.0):
        return None
    steps = np.diff(values)
    typical_step = float(np.median(steps))
    if typical_step <= 0.0 or values[0] > 1.5 * typical_step:
        return None
    slope, zero_x = np.polyfit(values, xs, 1)
    residual = float(np.max(np.abs(xs - (slope * values + zero_x))))
    if slope <= 0.0 or residual > 2.5:
        return None
    left = float(zero_x)
    right = float(xs[-1])
    ownership = pymupdf.Rect(
        plot.x0 - 25.0 - horizontal_extra,
        plot.y0,
        plot.x1 + 75.0 + horizontal_extra,
        plot.y1,
    )
    if (
        right - left < max(55.0, min(100.0, 0.30 * plot.width))
        or not ownership.x0 <= left < right <= ownership.x1
    ):
        return None
    return left, right


def bounded_gate_axis_ocr(
    pdf: Path,
    page: pymupdf.Page,
    panel: ChartPanel,
    result,
) -> PageText | None:
    """Read only the owned Y labels and bottom Qg label/tick bands."""

    scale = result.dpi / 72.0
    crop = pymupdf.Rect(result.crop_box_pt)
    px0, py0, px1, py1 = result.plot_box_px
    plot = pymupdf.Rect(
        crop.x0 + px0 / scale,
        crop.y0 + py0 / scale,
        crop.x0 + px1 / scale,
        crop.y0 + py1 / scale,
    )
    geometry_retry = bool(
        {"low_trace_confidence", "curve_missing_axis_origin"}.intersection(
            result.diagnostics
        )
    )
    horizontal_extra = min(60.0, 0.35 * plot.width) if geometry_retry else 0.0
    vertical_extra = 0.75 * plot.height if geometry_retry else 0.0
    bottom = pymupdf.Rect(
        max(page.rect.x0, plot.x0 - 15.0 - horizontal_extra),
        max(page.rect.y0, plot.y1 - 6.0),
        min(page.rect.x1, plot.x1 + 45.0),
        min(page.rect.y1, plot.y1 + 65.0),
    )
    bottom_panel = pymupdf.Rect(
        plot.x0 - 20.0 - horizontal_extra,
        plot.y0 - 20.0,
        min(page.rect.x1, plot.x1 + 75.0),
        min(page.rect.y1, plot.y1 + 95.0),
    )
    contextual_words: list[Word] = []
    provisional_axis = bool(
        {
            "axis_assumed_0_10",
            "axis_inferred_from_regular_grid",
            "curve_missing_initial_ramp",
            "curve_missing_axis_origin",
            "gate_charge_unit_unresolved",
            "low_trace_confidence",
        }.intersection(result.diagnostics)
    )
    try:
        raw_bottom = [
            _native_words_in_rect(page, bottom),
            ocr_words_in_rect(
                pdf, panel.page, bottom, dpi=BOUNDED_AXIS_OCR_DPI, psm=6
            ),
            ocr_words_in_rect(
                pdf, panel.page, bottom, dpi=BOUNDED_AXIS_OCR_DPI, psm=11
            ),
        ]
        if provisional_axis:
            contextual_words = _words(
                ocr_words_on_page_poppler(
                    pdf,
                    panel.page,
                    dpi=CONTEXT_AXIS_OCR_DPI,
                    psm=6,
                    min_confidence=0.0,
                )
            )
            raw_bottom.append(
                [
                    (word.x0, word.y0, word.x1, word.y1, word.text)
                    for word in contextual_words
                ]
            )
            tick_band = _charge_unit_tick_band(
                raw_bottom, bottom_panel, page.rect
            )
            if tick_band is not None:
                raw_bottom.extend(
                    [
                        ocr_words_in_poppler_page_rect(
                            pdf,
                            panel.page,
                            tick_band,
                            render_dpi=240.0,
                            psm=6,
                        ),
                        ocr_words_in_poppler_page_rect(
                            pdf,
                            panel.page,
                            tick_band,
                            render_dpi=240.0,
                            psm=11,
                        ),
                    ]
                )
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return None

    bottom_words, x_ticks, x_row_y = _best_bottom_words(
        raw_bottom, bottom_panel
    )
    x_edges = _gate_x_tick_edges(x_ticks, plot, horizontal_extra)
    left_edge, right_edge = (
        x_edges if x_edges is not None else (plot.x0, plot.x1)
    )
    axis_bottom = (
        min(page.rect.y1, x_row_y + 5.0)
        if x_edges is not None and x_row_y is not None
        else plot.y1
    )
    axis_search_plot = pymupdf.Rect(
        left_edge - horizontal_extra,
        plot.y0 - vertical_extra,
        right_edge + horizontal_extra,
        axis_bottom,
    )
    left = pymupdf.Rect(
        max(page.rect.x0, left_edge - 24.0 - horizontal_extra),
        max(page.rect.y0, plot.y0 - 10.0 - vertical_extra),
        min(page.rect.x1, left_edge + 5.0),
        min(page.rect.y1, axis_bottom + 10.0),
    )
    # The first pass can bind the Toshiba dual-axis plot to the VDS sub-frame.
    # Keep enough room to reach the source VGS labels and the final Qg tick.
    right = pymupdf.Rect(
        max(page.rect.x0, right_edge - 5.0),
        max(page.rect.y0, plot.y0 - 10.0 - vertical_extra),
        min(page.rect.x1, right_edge + 75.0 + horizontal_extra),
        min(page.rect.y1, axis_bottom + 10.0),
    )
    try:
        native_left = _words(_native_words_in_rect(page, left))
        native_right = _words(_native_words_in_rect(page, right))
        left_words = _words(
            ocr_words_in_rect(
                pdf, panel.page, left, dpi=BOUNDED_AXIS_OCR_DPI, psm=6
            )
        )
        right_words = _words(
            ocr_words_in_rect(
                pdf, panel.page, right, dpi=BOUNDED_AXIS_OCR_DPI, psm=6
            )
        )
        poppler_left = (
            _words(
                ocr_words_in_poppler_page_rect(
                    pdf, panel.page, left, psm=6
                )
            )
            if contextual_words
            else []
        )
        poppler_right = (
            _words(
                ocr_words_in_poppler_page_rect(
                    pdf, panel.page, right, psm=6
                )
            )
            if contextual_words
            else []
        )
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return None

    axis_candidates = [
        (
            side,
            _axis_ticks_from_band(
                words, crop, scale, result.plot_box_px, axis_search_plot
            ),
        )
        for side, words in (
            ("left", native_left),
            ("right", native_right),
            ("left", left_words),
            ("right", right_words),
            ("left", poppler_left),
            ("right", poppler_right),
            (
                "left",
                [
                    word
                    for word in contextual_words
                    if left.contains(
                        (
                            0.5 * (word.x0 + word.x1),
                            0.5 * (word.y0 + word.y1),
                        )
                    )
                ],
            ),
            (
                "right",
                [
                    word
                    for word in contextual_words
                    if right.contains(
                        (
                            0.5 * (word.x0 + word.x1),
                            0.5 * (word.y0 + word.y1),
                        )
                    )
                ],
            ),
        )
    ]
    side, ticks = max(
        axis_candidates,
        key=lambda item: _axis_quality(item[1]),
    )
    if not _linear_axis_is_evidenced(ticks):
        return None

    if gate_charge_unit(" ".join(word.text for word in bottom_words)) is None:
        unit_band = pymupdf.Rect(
            max(page.rect.x0, left_edge - 10.0),
            max(page.rect.y0, (x_row_y or plot.y1) + 4.0),
            min(page.rect.x1, right_edge + 35.0),
            min(page.rect.y1, (x_row_y or plot.y1) + 65.0),
        )
        try:
            bottom_words.extend(
                _words(
                    ocr_words_in_rect(
                        pdf,
                        panel.page,
                        unit_band,
                        dpi=UNIT_AXIS_OCR_DPI,
                        psm=6,
                    )
                )
            )
        except (OSError, RuntimeError, subprocess.SubprocessError):
            pass
    right_axis = side == "right"
    column_x = right_edge + 7.0 if right_axis else left_edge - 7.0
    synthetic_y = [
        Word(
            f"{value:g}",
            column_x - 3.0,
            y - 1.5,
            column_x + 3.0,
            y + 1.5,
        )
        for value, y in ticks
    ]
    numeric_bottom_ids = {
        id(word)
        for word in bottom_words
        if _parse_numeric_label(word.text) is not None
    }
    bottom_text = [
        word for word in bottom_words if id(word) not in numeric_bottom_ids
    ]
    synthetic_x_row_y = (
        float(np.median([word.y0 for word in bottom_words]))
        if not x_ticks
        else x_row_y
        if x_row_y is not None
        else min(
            (
                0.5 * (word.y0 + word.y1)
                for word in bottom_words
                if _parse_numeric_label(word.text) is not None
            ),
            key=lambda y: abs(y - plot.y1),
        )
    )
    synthetic_x = [
        Word(
            f"{value:g}",
            x - 2.0,
            synthetic_x_row_y - 1.5,
            x + 2.0,
            synthetic_x_row_y + 1.5,
        )
        for value, x in x_ticks
    ]
    return PageText(
        panel.page,
        float(page.rect.width),
        float(page.rect.height),
        [*synthetic_y, *synthetic_x, *bottom_text],
        "tesseract_bounded_gate_axes",
    )


def recalibrate_gate_result(
    result,
    page_text: PageText,
) -> tuple[float, tuple[tuple[float, float], ...], str] | None:
    """Apply bounded source ticks to an already high-confidence pixel trace."""

    if (
        result.vpl_y_px is None
        or len(result.curve_px) < 20
        or any(
            item in result.diagnostics
            for item in (
                "low_trace_confidence",
                "curve_missing_initial_ramp",
                "curve_missing_axis_origin",
                "plot_box_aspect_implausible",
            )
        )
    ):
        return None
    scale = result.dpi / 72.0
    crop = pymupdf.Rect(result.crop_box_pt)
    px0, py0, px1, py1 = result.plot_box_px
    plot = pymupdf.Rect(
        crop.x0 + px0 / scale,
        crop.y0 + py0 / scale,
        crop.x0 + px1 / scale,
        crop.y0 + py1 / scale,
    )
    # The first-pass plot box can sit tens of points inboard of the true axis
    # (a grid-bound frame on SP010N07AGTQ starts 27 pt right of the labels), so
    # ownership is a band beside either edge, not a +-5 pt line. Each side is
    # fit separately: a bottom x-tick row entering the window would otherwise
    # poison one merged fit with samples from the other axis.
    left_words: list[Word] = []
    right_words: list[Word] = []
    for word in page_text.words:
        value = _parse_numeric_label(word.text)
        if value is None:
            continue
        x = 0.5 * (word.x0 + word.x1)
        y = 0.5 * (word.y0 + word.y1)
        if not plot.y0 - 12.0 <= y <= plot.y1 + 12.0:
            continue
        if plot.x0 - 45.0 <= x <= plot.x0 + 10.0:
            left_words.append(word)
        elif plot.x1 - 10.0 <= x <= plot.x1 + 45.0:
            right_words.append(word)
    candidates = [
        _dedupe_axis_ticks(_raw_linear_y_ticks(words, plot))
        for words in (left_words, right_words)
    ]
    ticks = _drop_glyph_offset_tick(max(candidates, key=_axis_quality))
    if not _linear_axis_is_evidenced(ticks):
        return None
    values = np.asarray([value for value, _y in ticks], dtype=float)
    rows = np.asarray([y for _value, y in ticks], dtype=float)
    vpl_y = crop.y0 + float(result.vpl_y_px) / scale
    row_span = float(np.ptp(rows))
    slack = 0.05 * row_span
    if len(rows) >= 4:
        # A plateau a fraction of one interval past the last printed label is a
        # different animal from SUP90140E's two-tick long-range extrapolation:
        # NCEP25N10AK prints 10..4 and leaves 3.2 V one half-interval below.
        spacing = float(np.median(np.diff(np.sort(rows))))
        slack = max(slack, min(0.75 * spacing, 0.15 * row_span))
    if not float(np.min(rows)) - slack <= vpl_y <= float(np.max(rows)) + slack:
        return None
    slope, offset = np.polyfit(rows, values, 1)
    vpl = float(slope * vpl_y + offset)
    if not 1.0 <= abs(vpl) <= 12.0:
        return None
    unit = gate_charge_unit(" ".join(word.text for word in page_text.words))
    if unit is None:
        unit = result.x_tick_unit
    if unit is None:
        return None
    ticks_px = tuple(
        (value, float((y - crop.y0) * scale))
        for value, y in ticks
    )
    return vpl, ticks_px, unit


def refine_oversized_ocr_gate_panel(
    panel_rect: pymupdf.Rect,
    panel: ChartPanel,
    page_text: PageText | None,
    text_page,
) -> pymupdf.Rect:
    """Localize a bare ``Gate Charge`` caption that owns a whole chart column."""

    if (
        re.sub(r"[^a-z]", "", panel.title.lower())
        not in {"gatecharge", "gatechargecharacteristics"}
        or page_text is None
    ):
        return panel_rect

    source_words = page_text.words
    page_width = page_text.width_pt
    page_height = page_text.height_pt
    pairs: list[tuple[float, float]] = []
    ordered = sorted(source_words, key=lambda word: (word.y0, word.x0))
    for gate in ordered:
        if gate.text.lower().strip(" :") != "gate":
            continue
        gate_y = 0.5 * (gate.y0 + gate.y1)
        for charge in ordered:
            if charge.text.lower().strip(" :") != "charge":
                continue
            charge_y = 0.5 * (charge.y0 + charge.y1)
            if (
                0.0 <= charge.x0 - gate.x1 <= 16.0
                and abs(charge_y - gate_y) <= 4.0
                and panel_rect.x0 <= gate.x0 <= charge.x1 <= panel_rect.x1
                and panel_rect.y0
                <= gate_y
                <= panel_rect.y1 + 0.35 * panel_rect.width
            ):
                pairs.append((gate_y, 0.5 * (gate.x0 + charge.x1)))
    if not pairs:
        return panel_rect
    caption_y, _caption_x = max(pairs)
    provisional = pymupdf.Rect(
        panel_rect.x0,
        max(panel_rect.y0, caption_y - 0.88 * panel_rect.width),
        panel_rect.x1,
        caption_y - 4.0,
    )
    x_axis = _best_x_axis_for_panel(text_page, provisional)
    if x_axis is None:
        return provisional if caption_y > panel_rect.y1 else panel_rect
    ticks, row_y = x_axis
    if len(ticks) < 4 or not 5.0 <= caption_y - row_y <= 60.0:
        return panel_rect
    values = np.asarray([value for value, _x in ticks], dtype=float)
    xs = np.asarray([x for _value, x in ticks], dtype=float)
    if np.any(np.diff(values) <= 0.0) or np.any(np.diff(xs) <= 0.0):
        return panel_rect
    slope, zero_x = np.polyfit(values, xs, 1)
    residual = float(np.max(np.abs(xs - (slope * values + zero_x))))
    value_step = float(np.median(np.diff(values)))
    if (
        not math.isfinite(zero_x)
        or residual > 2.0
        or values[0] > 1.5 * value_step
        or not panel_rect.x0 - 20.0 <= zero_x <= xs[0] + 2.0
    ):
        return panel_rect
    width = float(xs[-1] - zero_x)
    if width < 100.0:
        return panel_rect
    bottom = row_y - max(5.0, 0.04 * width)
    refined = pymupdf.Rect(
        zero_x - 2.0,
        bottom - 0.82 * width,
        xs[-1] + 0.03 * width,
        bottom + 2.0,
    )
    return refined & pymupdf.Rect(0.0, 0.0, page_width, page_height)
