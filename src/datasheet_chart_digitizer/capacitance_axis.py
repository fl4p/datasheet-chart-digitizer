"""Axis calibration helpers for MOSFET capacitance charts."""

from __future__ import annotations

import math
import re
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np

from .axis_calibration import (
    _NUMBER_TOKEN_RE,
    _number_tokens,
    _x_ticks_look_log,
    calibrate_axes,
)

from .capacitance_traces import _interp_y
from .capacitance_types import AxisCalibration, GridlineFit, PlotBox, Trace
from .capacitance_vector import _load_fitz
from .crop_transform import CropTransform
from .numeric_axis import AxisTick, fit_axis_ticks
from .region_ocr import ocr_rotated_text_in_rect, ocr_words_in_rect

def infer_text_order_axis_calibration(chart: dict[str, object]) -> AxisCalibration:
    text = str(chart.get("text") or "")
    x_ticks, x_start_index = _parse_x_ticks_from_chart_text(text)
    y_decades = _parse_y_decades_from_chart_text(text, x_start_index)
    if len(x_ticks) < 2:
        raise RuntimeError("could not infer x-axis ticks from chart text")
    if len(y_decades) < 2:
        raise RuntimeError("could not infer y-axis decades from chart text")
    return AxisCalibration(
        x_min_v=float(x_ticks[0]),
        x_max_v=float(x_ticks[-1]),
        y_min_decade=float(min(y_decades)),
        y_max_decade=float(max(y_decades)),
        source="chart_text",
        x_ticks_v=tuple(float(v) for v in x_ticks),
        y_decades=tuple(float(v) for v in sorted(set(y_decades))),
        # chart_text is never trusted, but the normalized v_of_x branch honors
        # x_log -- keep the untrusted debug output on the right scale too.
        x_log=bool(_x_ticks_look_log is not None and _x_ticks_look_log([float(v) for v in x_ticks])),
        x_source="text_order_normalized_plot_extent",
        y_source="text_order_normalized_plot_extent",
    )


def _plot_rect_pt(chart: dict[str, object], image: np.ndarray, plot: PlotBox):
    """Detected plot frame in page-pt coordinates (as a fitz.Rect)."""
    fitz = _load_fitz()
    if fitz is None:
        raise RuntimeError("PyMuPDF is not available")
    transform = CropTransform.for_chart(chart, image.shape)
    plot_x0, plot_y0 = transform.to_pt(plot.x0, plot.y0)
    plot_x1, plot_y1 = transform.to_pt(plot.x1, plot.y1)
    return transform, fitz.Rect(plot_x0, plot_y0, plot_x1, plot_y1)


def _fit_position_calibration(page_like, transform: CropTransform, plot_rect, source: str) -> AxisCalibration:
    """Position-fit tick labels from any words source (PDF text or OCR).

    `page_like` only needs `get_text("words")` returning (x0, y0, x1, y1, text)
    tuples in page-pt coordinates -- a real PyMuPDF page or an OCR adapter.
    """
    if calibrate_axes is None:
        raise RuntimeError("axis_calibration.calibrate_axes is not available")
    pos_cal = calibrate_axes(
        page_like,
        x_row_band=(plot_rect.y1 + 2.0, plot_rect.y1 + 24.0),
        y_label_x_band=(plot_rect.x0 - 42.0, plot_rect.x0 - 1.0),
        plot_y_band=(plot_rect.y0 - 8.0, plot_rect.y1 + 8.0),
        # Two-charts-per-row pages (TI) put both tick rows in the same y band;
        # keep only labels under THIS plot. The margin must still admit our own
        # origin '0' label (a few pt left of the frame) while excluding the
        # neighbor chart's rightmost tick (tens of pt away).
        x_col_band=(plot_rect.x0 - 24.0, plot_rect.x1 + 12.0),
    )

    # Convert page-coordinate fits to crop-pixel-coordinate fits, because trace
    # points are stored in crop pixels.
    x_scale = float(pos_cal.mx) / transform.scale_x
    x_offset = float(pos_cal.mx) * transform.x0_pt + float(pos_cal.bx)
    y_scale = float(pos_cal.my) / transform.scale_y
    y_offset = float(pos_cal.my) * transform.y0_pt + float(pos_cal.by)
    x_ticks = tuple(float(v) for v, _ in pos_cal.x_ticks)
    x_tick_label_px = tuple(
        float(transform.to_px(pixel, plot_rect.y1)[0])
        for _value, pixel in pos_cal.x_ticks
    )
    y_log = bool(getattr(pos_cal, "y_log", True))
    y_coordinates = tuple(float(e) for e, _ in pos_cal.y_decades)
    if y_log:
        y_ticks_pf: tuple[float, ...] = ()
        y_decades = y_coordinates
        y_min_decade = min(y_decades)
        y_max_decade = max(y_decades)
        y_resid_dec: float | None = float(pos_cal.y_resid)
        y_resid_pf: float | None = None
    else:
        y_ticks_pf = tuple(sorted(set(y_coordinates)))
        y_tick_label_px = tuple(
            float(transform.to_px(plot_rect.x0, pixel)[1])
            for _value, pixel in pos_cal.y_decades
        )
        positive_ticks = [value for value in y_ticks_pf if value > 0.0]
        if len(positive_ticks) < 3:
            raise RuntimeError("linear Y calibration needs >=3 positive capacitance ticks")
        top_pf = float(pos_cal.my * plot_rect.y0 + pos_cal.by)
        bottom_pf = float(pos_cal.my * plot_rect.y1 + pos_cal.by)
        frame_positive = [value for value in (top_pf, bottom_pf) if value > 0.0]
        y_min_decade = math.log10(min(positive_ticks))
        y_max_decade = math.log10(max(positive_ticks + frame_positive))
        y_decades = tuple(math.log10(value) for value in positive_ticks)
        y_resid_dec = None
        y_resid_pf = float(pos_cal.y_resid)
    if y_log:
        y_tick_label_px = ()
    return AxisCalibration(
        x_min_v=min(x_ticks),
        x_max_v=max(x_ticks),
        y_min_decade=y_min_decade,
        y_max_decade=y_max_decade,
        source=source,
        x_ticks_v=x_ticks,
        y_decades=tuple(sorted(set(y_decades))),
        x_log=bool(getattr(pos_cal, "x_log", False)),
        y_log=y_log,
        y_ticks_pf=y_ticks_pf,
        x_resid_v=float(pos_cal.x_resid),
        y_resid_dec=y_resid_dec,
        y_resid_pf=y_resid_pf,
        y_tick_label_px=y_tick_label_px,
        x_scale=x_scale,
        x_offset=x_offset,
        y_scale=y_scale,
        y_offset=y_offset,
        x_source=source,
        y_source=source,
        x_source_ticks_v=tuple(
            float(value) for value in getattr(pos_cal, "x_source_ticks", ())
        ),
        x_value_transform=getattr(pos_cal, "x_value_transform", None),
        x_tick_label_px=x_tick_label_px,
    )


def infer_position_axis_calibration(
    chart: dict[str, object], image: np.ndarray, plot: PlotBox
) -> AxisCalibration:
    fitz = _load_fitz()
    if fitz is None:
        raise RuntimeError("PyMuPDF is not available")
    transform, plot_rect = _plot_rect_pt(chart, image, plot)
    doc = fitz.open(Path(str(chart["pdf"])))
    page = doc[int(chart["page"]) - 1]
    calibration = _fit_position_calibration(page, transform, plot_rect, "position_text")
    calibration = _seat_linear_y_ticks_on_grid(
        calibration, image, plot, page=page, transform=transform
    )
    return _seat_regular_log_x_ticks_on_grid(calibration, image, plot)


class _OcrWordsPage:
    """Duck-typed stand-in for a PyMuPDF page backed by OCR word boxes."""

    def __init__(self, words: list[tuple[float, float, float, float, str]]):
        self._words = words

    def get_text(self, kind: str):
        return list(self._words)


def _ocr_words_in_rect(
    chart: dict[str, object], clip_rect, dpi: float = 400.0
) -> list[tuple[float, float, float, float, str]]:
    return ocr_words_in_rect(
        str(chart["pdf"]), int(chart["page"]), clip_rect, dpi=dpi, psm=11
    )


_MIN_OCR_DECADE_LADDER = 4
_MIN_OCR_DECADE_ANCHORS = 2
_MAX_OCR_DECADE_SPACING_CV = 0.15
_MAX_OCR_DECADE_COLUMN_OFFSET_PT = 4.0
_OCR_DECADE_TRAILING_JUNK = "°º*'\"`’~^!"
_OCR_DECADE_CLEAN_RE = re.compile(r"10(\d)")
_OCR_DECADE_MANGLED_RE = re.compile(r"[147][0oO68e](\d?)")
_EXPONENT_SUPERSCRIPTS = "⁰¹²³⁴⁵⁶⁷⁸⁹"
_EXPLICIT_OCR_POWER_RE = re.compile(r"10[⁰¹²³⁴⁵⁶⁷⁸⁹]")


def _repair_ocr_decade_ladder(
    words: list[tuple[float, float, float, float, str]],
    plot_rect,
    *,
    minimum_anchors: int = _MIN_OCR_DECADE_ANCHORS,
) -> list[tuple[float, float, float, float, str]]:
    """Rewrite an OCR-mangled ``10^N`` decade column into explicit powers.

    Tesseract reads superscript decade labels (``10⁵``..``10¹``) as tokens
    like ``10°``, ``104``, ``40°``, ``462``. No single token's digits are
    trustworthy, but a COLUMN of them is: >=4 uniformly spaced rows in the
    left label gutter whose exposed trailing digits INDEPENDENTLY solve the
    same top exponent for a one-decade step per row. Anything short of that
    leaves the words untouched, so the existing refusal paths keep failing
    closed; a rewritten ladder still has to pass the shared position-fit
    residual and endpoint-coverage gates downstream, and the served tick
    labels render on the verify overlay where a human can falsify them.
    """
    band_x0, band_x1 = plot_rect.x0 - 42.0, plot_rect.x0 - 1.0
    band_y0, band_y1 = plot_rect.y0 - 8.0, plot_rect.y1 + 8.0
    members: list[tuple[int, float, float, int | None]] = []
    numeric_blockers: list[float] = []
    for index, (x0, y0, x1, y1, text) in enumerate(words):
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        if not (band_x0 < cx < band_x1 and band_y0 < cy < band_y1):
            continue
        token = text.strip().rstrip(_OCR_DECADE_TRAILING_JUNK)
        if not token or not any(ch.isdigit() for ch in token):
            continue
        clean = _OCR_DECADE_CLEAN_RE.fullmatch(token)
        mangled = _OCR_DECADE_MANGLED_RE.fullmatch(token)
        if clean is not None:
            members.append((index, cx, cy, int(clean.group(1))))
        elif mangled is not None:
            claimed = mangled.group(1)
            members.append((index, cx, cy, int(claimed) if claimed else None))
        else:
            numeric_blockers.append(cx)
    if len(members) < _MIN_OCR_DECADE_LADDER:
        return words
    column_x = float(np.median([cx for _index, cx, _cy, _exponent in members]))
    if any(
        abs(cx - column_x) > _MAX_OCR_DECADE_COLUMN_OFFSET_PT
        for _index, cx, _cy, _exponent in members
    ):
        return words
    if any(
        abs(cx - column_x) <= _MAX_OCR_DECADE_COLUMN_OFFSET_PT
        for cx in numeric_blockers
    ):
        # An in-column numeric token outside the 10^N shape (an arithmetic
        # ladder such as 4000/2000, or a plain-decade 100000) proves this is
        # not a mangled decade column. Numeric fragments from the farther-left
        # rotated axis title are outside the evidenced label column and do not.
        return words
    members.sort(key=lambda item: item[2])
    spacing = np.diff([cy for _index, _cx, cy, _exponent in members])
    if np.any(spacing <= 0.0):
        return words
    if float(np.std(spacing)) > _MAX_OCR_DECADE_SPACING_CV * float(np.median(spacing)):
        return words
    anchors = [
        claimed + row
        for row, (_index, _cx, _cy, claimed) in enumerate(members)
        if claimed is not None
    ]
    claimed_digits = [
        claimed
        for _index, _cx, _cy, claimed in members
        if claimed is not None
    ]
    if len(set(claimed_digits)) != len(claimed_digits):
        # The same exposed exponent at two different rows is direct source
        # contradiction, even if one implied top exponent is out of range.
        return words
    if len(anchors) < minimum_anchors:
        return words
    # A visibly mangled superscript can expose a contradictory digit (for
    # example ``10⁴`` OCRed as ``107``).  Discard only candidates that would
    # force the evidenced ladder beyond the guarded 10^0..10^7 range; at least
    # two independently exposed digits are still required, and every remaining
    # physically possible candidate must agree.
    plausible_anchors = [
        anchor
        for anchor in anchors
        if len(members) - 1 <= anchor <= 7
    ]
    if not plausible_anchors or len(set(plausible_anchors)) != 1:
        return words
    top_exponent = plausible_anchors[0]
    bottom_exponent = top_exponent - (len(members) - 1)
    if bottom_exponent < 0 or top_exponent > 7:
        return words
    repaired = list(words)
    for row, (index, _cx, _cy, _claimed) in enumerate(members):
        exponent = top_exponent - row
        x0, y0, x1, y1, _text = words[index]
        repaired[index] = (x0, y0, x1, y1, "10" + _EXPONENT_SUPERSCRIPTS[exponent])
    return repaired


def _replace_words_in_band(
    words: list[tuple[float, float, float, float, str]],
    replacements: list[tuple[float, float, float, float, str]],
    *,
    x_band: tuple[float, float],
    y_band: tuple[float, float],
) -> list[tuple[float, float, float, float, str]]:
    """Replace one bounded OCR stratum without duplicating its coarse words."""

    kept = [
        word
        for word in words
        if not (
            x_band[0] < (word[0] + word[2]) / 2.0 < x_band[1]
            and y_band[0] < (word[1] + word[3]) / 2.0 < y_band[1]
        )
    ]
    return kept + replacements


def _bounded_ocr_x_ticks(
    chart: dict[str, object], plot_rect
) -> list[tuple[float, float, float, float, str]]:
    """OCR only the horizontal numeric tick row.

    The combined axis/title clip makes Tesseract read Infineon ticks such as
    30/60/90 as ``0``/``So``/``30``. A tight single-line numeric crop reads
    every label correctly. Shared position-fit residual and monotonicity gates
    remain authoritative after this source-owned OCR replacement.
    """

    fitz = _load_fitz()
    if fitz is None:
        return []
    clip = fitz.Rect(
        plot_rect.x0 - 12.0,
        plot_rect.y1 + 1.0,
        plot_rect.x1 + 12.0,
        plot_rect.y1 + 14.0,
    )
    try:
        words = ocr_words_in_rect(
            str(chart["pdf"]),
            int(chart["page"]),
            clip,
            dpi=500.0,
            psm=11,
            whitelist="0123456789.-",
            min_confidence=0.0,
        )
    except RuntimeError:
        return []
    normalized: list[tuple[float, float, float, float, str]] = []
    numeric: list[tuple[float, float]] = []
    for x0, y0, x1, y1, text in words:
        token = text.strip().strip("‘’'`")
        if re.fullmatch(r"\d+\.", token):
            token = token[:-1]
        if re.fullmatch(r"\d+(?:\.\d+)?", token):
            numeric.append((float(token), (x0 + x1) / 2.0))
            normalized.append((x0, y0, x1, y1, token))
    numeric.sort(key=lambda item: item[1])
    values = np.asarray([value for value, _pixel in numeric], dtype=float)
    if len(values) < 5 or values[0] != 0.0 or np.any(np.diff(values) <= 0.0):
        return []
    steps = np.diff(values)
    if float(np.std(steps)) > 0.05 * float(np.median(steps)):
        return []
    return normalized


def _bounded_ocr_y_ladder(
    chart: dict[str, object], plot_rect
) -> list[tuple[float, float, float, float, str]]:
    """Recover a decade column missed by the combined OCR clip.

    A narrow numeric pass exposes all six IAUCN rows. One row is still read as
    ``102`` instead of ``103``; a second page-segmentation mode independently
    reads that row cleanly. The supplemental token may replace only a
    position-matched row, and the resulting ladder needs three agreeing read
    exponents (one fitted top exponent, hence overdetermined) before any
    inferred rows are emitted.
    """

    fitz = _load_fitz()
    if fitz is None:
        return []
    clip = fitz.Rect(
        plot_rect.x0 - 19.0,
        plot_rect.y0 - 8.0,
        plot_rect.x0 - 1.0,
        plot_rect.y1 + 8.0,
    )

    def run(psm: int, *, dpi: float = 500.0):
        return ocr_words_in_rect(
            str(chart["pdf"]),
            int(chart["page"]),
            clip,
            dpi=dpi,
            psm=psm,
            whitelist="0123456789!°º*^",
            min_confidence=0.0,
        )

    try:
        primary = run(11)
        # PSM 6 at 400 dpi reads the six-row Infineon gutter as one uniform
        # block.  On current tesseract PSM 3 returns no words for the same
        # narrow clip, while this pass independently exposes clean 10^4,
        # 10^3, and 10^2 anchors needed to repair the remaining mangled rows.
        supplemental = run(6, dpi=400.0)
    except RuntimeError:
        return []
    merged = list(primary)
    for replacement in supplemental:
        token = replacement[4].strip().rstrip(_OCR_DECADE_TRAILING_JUNK)
        if _OCR_DECADE_CLEAN_RE.fullmatch(token) is None:
            continue
        rcx = (replacement[0] + replacement[2]) / 2.0
        rcy = (replacement[1] + replacement[3]) / 2.0
        candidates = [
            (abs((word[1] + word[3]) / 2.0 - rcy), index)
            for index, word in enumerate(merged)
            if abs((word[0] + word[2]) / 2.0 - rcx) <= 4.0
            and abs((word[1] + word[3]) / 2.0 - rcy) <= 4.0
        ]
        if candidates:
            _distance, index = min(candidates)
            merged[index] = replacement
    repaired = _repair_ocr_decade_ladder(
        merged, plot_rect, minimum_anchors=3
    )
    if sum(
        _EXPLICIT_OCR_POWER_RE.fullmatch(word[4].strip()) is not None
        for word in repaired
    ) < _MIN_OCR_DECADE_LADDER:
        return []
    return repaired


_ROTATED_UNIT_RE = re.compile(r"\(\s*(pF|nF)\s*\)|\b(pF|nF)\b")


def _ocr_rotated_axis_unit(chart: dict[str, object], plot_rect) -> str | None:
    """OCR the rotated Y-axis title strip for an unambiguous pF/nF unit.

    Raster charts print the capacitance unit only inside the rotated axis
    title ("C - Capacitance (pF)"), which horizontal OCR cannot read, so an
    otherwise clean arithmetic label ladder stays unitless and refuses. The
    strip is rotated upright and OCRed case-sensitively; only a literal
    ``pF``/``nF`` counts. A degraded read such as ``(oF)`` is EXACTLY the
    pF-vs-nF ambiguity this evidence exists to resolve, so it stays None.
    """
    fitz = _load_fitz()
    if fitz is None:
        return None
    strip = fitz.Rect(
        plot_rect.x0 - 70.0, plot_rect.y0 - 6.0,
        plot_rect.x0 - 12.0, plot_rect.y1 + 6.0,
    )
    text = ocr_rotated_text_in_rect(
        str(chart["pdf"]), int(chart["page"]), strip, dpi=500.0
    )
    return _unit_from_rotated_title(text)


def _unit_from_rotated_title(text: str | None) -> str | None:
    """One unambiguous case-sensitive pF/nF from rotated-title OCR text."""
    if text is None:
        return None
    matches = {
        (match.group(1) or match.group(2))
        for match in _ROTATED_UNIT_RE.finditer(text)
    }
    if len(matches) != 1:
        return None
    return next(iter(matches)).casefold()


def _drop_ocr_x_tick_outlier(
    words: list[tuple[float, float, float, float, str]], plot_rect
) -> list[tuple[float, float, float, float, str]]:
    """Blank one OCR X-tick token that contradicts an otherwise exact ladder.

    Tesseract occasionally prepends a stray digit to one tick ("50" ->
    "350"), and a single such token pushes the position-fit residual past the
    trust gate, refusing a chart whose other labels are exact. Dropping is
    allowed only when >=5 remaining ticks fit a line value<->position within
    0.5 V while the dropped token deviates by more than 5 V -- a real
    curved/log axis fails the tight remainder fit and refuses as before.
    """
    band_y0, band_y1 = plot_rect.y1 + 2.0, plot_rect.y1 + 24.0
    band_x0, band_x1 = plot_rect.x0 - 24.0, plot_rect.x1 + 12.0
    ticks: list[tuple[int, float, float]] = []
    for index, (x0, y0, x1, y1, text) in enumerate(words):
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        if not (band_x0 < cx < band_x1 and band_y0 < cy < band_y1):
            continue
        token = text.strip()
        if re.fullmatch(r"[−-]?\d+(?:\.\d+)?", token):
            ticks.append((index, float(token.replace("−", "-")), cx))
    if len(ticks) < 6:
        return words
    values = np.asarray([value for _i, value, _cx in ticks], dtype=float)
    pixels = np.asarray([cx for _i, _value, cx in ticks], dtype=float)
    fit = np.polyfit(pixels, values, 1)
    residuals = np.abs(np.polyval(fit, pixels) - values)
    worst = int(np.argmax(residuals))
    if residuals[worst] <= 5.0:
        return words
    keep = [item for position, item in enumerate(ticks) if position != worst]
    keep_values = np.asarray([value for _i, value, _cx in keep], dtype=float)
    keep_pixels = np.asarray([cx for _i, _value, cx in keep], dtype=float)
    order = np.argsort(keep_pixels)
    if np.any(np.diff(keep_values[order]) <= 0.0):
        return words
    keep_fit = np.polyfit(keep_pixels, keep_values, 1)
    keep_residuals = np.abs(np.polyval(keep_fit, keep_pixels) - keep_values)
    if float(np.max(keep_residuals)) > 0.5:
        return words
    repaired = list(words)
    index = ticks[worst][0]
    x0, y0, x1, y1, _text = words[index]
    repaired[index] = (x0, y0, x1, y1, "")
    return repaired


def infer_ocr_position_axis_calibration(
    chart: dict[str, object], image: np.ndarray, plot: PlotBox
) -> AxisCalibration:
    """Position calibration for raster-image charts with no PDF text.

    Some vendors (Toshiba) embed the whole figure -- gridlines, traces AND
    tick labels -- as one raster image; `page.get_text("words")` is empty over
    the chart, so `infer_position_axis_calibration` cannot fit. Here the label
    bands are OCRed (tesseract) into page-pt word boxes and fed through the
    same position fit; the shared residual gates then decide trust.
    """
    fitz = _load_fitz()
    if fitz is None:
        raise RuntimeError("PyMuPDF is not available")
    transform, plot_rect = _plot_rect_pt(chart, image, plot)
    # Cover the label bands used by _fit_position_calibration (left decade
    # column and the tick row under the frame), with margin.
    clip = fitz.Rect(
        plot_rect.x0 - 60.0,
        plot_rect.y0 - 12.0,
        plot_rect.x1 + 16.0,
        plot_rect.y1 + 30.0,
    )
    words = _ocr_words_in_rect(chart, clip)
    if not words:
        raise RuntimeError("OCR found no words in the axis label bands")
    base_words = _repair_ocr_decade_ladder(words, plot_rect)
    unit = _ocr_rotated_axis_unit(chart, plot_rect)
    doc = fitz.open(Path(str(chart["pdf"])))
    page = doc[int(chart["page"]) - 1]

    def fit_attempt(
        attempt_words: list[tuple[float, float, float, float, str]],
    ) -> tuple[AxisCalibration | None, str | None]:
        attempt_words = _drop_ocr_x_tick_outlier(attempt_words, plot_rect)
        band_has_unit = any(
            plot_rect.x0 - 42.0 < (w[0] + w[2]) / 2.0 < plot_rect.x0 - 1.0
            and _ROTATED_UNIT_RE.fullmatch(w[4].strip())
            for w in attempt_words
        )
        if unit is not None and not band_has_unit:
            # The unit read from THIS chart's rotated axis title, injected as
            # an in-band token so the shared fit sees the same evidence a
            # horizontal label would provide.
            attempt_words = attempt_words + [(
                plot_rect.x0 - 30.0, plot_rect.y0 + 4.0,
                plot_rect.x0 - 20.0, plot_rect.y0 + 10.0,
                f"({'pF' if unit == 'pf' else 'nF'})",
            )]
        try:
            calibration = _fit_position_calibration(
                _OcrWordsPage(attempt_words),
                transform,
                plot_rect,
                "position_ocr",
            )
        except RuntimeError as exc:
            return None, str(exc)
        calibration = _seat_linear_y_ticks_on_grid(
            calibration, image, plot, page=page, transform=transform
        )
        calibration = _seat_signed_log_x_ticks_on_grid(
            calibration, image, plot
        )
        calibration = _seat_regular_log_x_ticks_on_grid(
            calibration, image, plot
        )
        return calibration, reject_bad_position_calibration(calibration, plot)

    calibration, base_error = fit_attempt(base_words)
    if calibration is not None and base_error is None:
        return calibration

    # The bounded multi-pass retry is a source-family stratum, not a generic
    # second guess. Infineon's raster-only panels carry this exact caption and
    # no chart-band text; broader use changed otherwise unrelated OCR charts
    # in the negative corpus. Other families keep their established refusal.
    bounded_retry_allowed = (
        str(chart.get("title") or "").strip().casefold() == "typ. capacitances"
        and not str(chart.get("text") or "").strip()
    )
    if not bounded_retry_allowed:
        raise RuntimeError(base_error or "OCR position calibration was rejected")

    retry_words = list(base_words)
    if not any(
        _EXPLICIT_OCR_POWER_RE.fullmatch(word[4].strip()) is not None
        for word in retry_words
    ):
        y_words = _bounded_ocr_y_ladder(chart, plot_rect)
        if y_words:
            retry_words = _replace_words_in_band(
                retry_words,
                y_words,
                x_band=(plot_rect.x0 - 42.0, plot_rect.x0 - 1.0),
                y_band=(plot_rect.y0 - 8.0, plot_rect.y1 + 8.0),
            )
    x_words = _bounded_ocr_x_ticks(chart, plot_rect)
    if len(x_words) >= 2:
        retry_words = _replace_words_in_band(
            retry_words,
            x_words,
            x_band=(plot_rect.x0 - 24.0, plot_rect.x1 + 12.0),
            y_band=(plot_rect.y1 + 2.0, plot_rect.y1 + 24.0),
        )
    if retry_words != base_words:
        calibration, retry_error = fit_attempt(retry_words)
        if calibration is not None and retry_error is None:
            return calibration
        if retry_error is not None:
            raise RuntimeError(retry_error)
    raise RuntimeError(base_error or "OCR position calibration was rejected")


def _endpoint_tick_coverage_error(
    tick_pixels: list[float], start: float, end: float, axis_name: str
) -> str | None:
    """Reject fits that serve multiple unseen intervals beyond labeled ticks."""

    pixels = sorted(set(float(pixel) for pixel in tick_pixels))
    if len(pixels) < 2:
        return f"{axis_name} endpoint coverage needs >=2 distinct tick centers"
    left_step = pixels[1] - pixels[0]
    right_step = pixels[-1] - pixels[-2]
    if left_step <= 0 or right_step <= 0:
        return f"{axis_name} tick centers are not strictly increasing"
    endpoint_intervals = (
        max(0.0, (pixels[0] - start) / left_step),
        max(0.0, (end - pixels[-1]) / right_step),
    )
    side, unseen = max(
        (("left", endpoint_intervals[0]), ("right", endpoint_intervals[1])),
        key=lambda item: item[1],
    )
    if unseen > 1.25:
        return (
            f"{axis_name} {side} endpoint leaves {unseen:.2f} unlabeled "
            "tick intervals; maximum is one"
        )
    return None


def reject_bad_position_calibration(
    calibration: AxisCalibration, plot: PlotBox | None = None
) -> str | None:
    if calibration.x_log:
        # Log-X fits carry their residual in decades, like the Y axis.
        if calibration.x_resid_v is not None and calibration.x_resid_v > 0.05:
            return f"position x residual {calibration.x_resid_v:.4g} decades exceeds 0.05"
        residual_error = None
    else:
        x_span = abs(calibration.x_max_v - calibration.x_min_v)
        max_x_resid = max(0.5, 0.02 * x_span)
        residual_error = None
        if calibration.x_resid_v is not None and calibration.x_resid_v > max_x_resid:
            residual_error = (
                f"position x residual {calibration.x_resid_v:.4g} V "
                f"exceeds {max_x_resid:.4g} V"
            )
    if residual_error is None:
        if calibration.y_log:
            if calibration.y_resid_dec is not None and calibration.y_resid_dec > 0.05:
                residual_error = (
                    f"position y residual {calibration.y_resid_dec:.4g} decades "
                    "exceeds 0.05"
                )
        else:
            y_span_pf = max(calibration.y_ticks_pf, default=0.0) - min(
                calibration.y_ticks_pf, default=0.0
            )
            max_y_resid_pf = max(1e-6, 0.02 * y_span_pf)
            if (
                calibration.y_resid_pf is None
                or calibration.y_resid_pf > max_y_resid_pf
            ):
                value = calibration.y_resid_pf
                rendered = "missing" if value is None else f"{value:.4g} pF"
                residual_error = (
                    f"position linear-y residual {rendered} exceeds "
                    f"{max_y_resid_pf:.4g} pF"
                )
    if residual_error is not None or plot is None:
        return residual_error

    if (
        calibration.x_scale is None
        or calibration.x_offset is None
        or calibration.y_scale is None
        or calibration.y_offset is None
        or calibration.x_scale == 0
        or calibration.y_scale == 0
    ):
        return "position calibration lacks invertible axis coefficients"
    x_values = [
        math.log10(value) if calibration.x_log else value
        for value in calibration.x_ticks_v
        if not calibration.x_log or value > 0
    ]
    x_pixels = [
        (value - calibration.x_offset) / calibration.x_scale
        for value in x_values
    ]
    x_coverage_error = _endpoint_tick_coverage_error(
        x_pixels, plot.x0, plot.x1, "X axis"
    )
    if x_coverage_error is not None:
        return x_coverage_error
    if calibration.y_log:
        y_pixels = [
            (value - calibration.y_offset) / calibration.y_scale
            for value in calibration.y_decades
        ]
    else:
        y_pixels = [
            (value - calibration.y_offset) / calibration.y_scale
            for value in calibration.y_ticks_pf
        ]
    return _endpoint_tick_coverage_error(y_pixels, plot.y0, plot.y1, "Y axis")


def infer_gridline_axis_calibration(chart: dict[str, object], image: np.ndarray, plot: PlotBox) -> AxisCalibration:
    text_calibration = infer_text_order_axis_calibration(chart)
    # This tier maps px->V LINEARLY between the extreme tick values and is
    # reported as trusted. On a log X axis that mapping is silently, severely
    # wrong (mid-plot reads ~16x high on a 0.1-100 V axis) -- refuse instead.
    if _x_ticks_look_log is not None and _x_ticks_look_log(list(text_calibration.x_ticks_v)):
        raise RuntimeError("log-spaced X ticks: grid-tier linear X mapping would mis-scale; refusing")
    y_fit = _major_horizontal_gridline_fit(image, plot, len(text_calibration.y_decades))
    y_positions = y_fit.centers
    if len(y_positions) != len(text_calibration.y_decades):
        raise RuntimeError("could not match Y decade labels to horizontal gridlines")

    y_values = np.array(sorted(text_calibration.y_decades, reverse=True), dtype=float)
    y_pixels = np.array(sorted(y_positions), dtype=float)
    y_scale, y_offset = np.polyfit(y_pixels, y_values, 1)
    y_resid = float(np.sqrt(np.mean((y_scale * y_pixels + y_offset - y_values) ** 2)))
    if y_resid > 0.05:
        raise RuntimeError(f"Y gridline fit residual {y_resid:.4g} decades exceeds 0.05")

    x_scale = (text_calibration.x_max_v - text_calibration.x_min_v) / max(1, plot.x1 - plot.x0)
    x_offset = text_calibration.x_min_v - x_scale * plot.x0
    return AxisCalibration(
        x_min_v=text_calibration.x_min_v,
        x_max_v=text_calibration.x_max_v,
        y_min_decade=text_calibration.y_min_decade,
        y_max_decade=text_calibration.y_max_decade,
        source="grid_text",
        x_ticks_v=text_calibration.x_ticks_v,
        y_decades=text_calibration.y_decades,
        x_resid_v=None,
        y_resid_dec=y_resid,
        x_scale=float(x_scale),
        x_offset=float(x_offset),
        y_scale=float(y_scale),
        y_offset=float(y_offset),
        x_source="plot_box_endpoints_from_text_ticks",
        y_source="gridline_fit_from_text_decades",
        y_gridline_px=tuple(float(y) for y in y_positions),
        y_grid_candidate_count=y_fit.candidate_count,
        y_grid_span_fraction=y_fit.span_fraction,
        y_grid_residual_px=y_fit.residual_px,
    )


def _major_horizontal_gridline_centers(image: np.ndarray, plot: PlotBox, count: int) -> list[float]:
    return _major_horizontal_gridline_fit(image, plot, count).centers


def _horizontal_gridline_candidates(image: np.ndarray, plot: PlotBox) -> list[float]:
    """Return source horizontal-line centers crossing most of the plot width."""

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    _, bw = cv2.threshold(gray, 245, 255, cv2.THRESH_BINARY_INV)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(80, gray.shape[1] // 5), 1))
    hlines = cv2.morphologyEx(bw, cv2.MORPH_OPEN, kernel)
    contours, _ = cv2.findContours(hlines, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    by_y: list[tuple[float, list[tuple[int, int]]]] = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w < plot.width * 0.20 or h > 8:
            continue
        center_y = y + h / 2.0
        if not (plot.y0 - 4 <= center_y <= plot.y1 + 4):
            continue
        start = max(plot.x0, x)
        end = min(plot.x1, x + w - 1)
        if end <= start:
            continue
        for idx, (existing_y, intervals) in enumerate(by_y):
            if abs(center_y - existing_y) <= 3.0:
                intervals.append((start, end))
                ys = [existing_y] * (len(intervals) - 1) + [center_y]
                by_y[idx] = (float(np.median(ys)), intervals)
                break
        else:
            by_y.append((center_y, [(start, end)]))

    candidates: list[float] = []
    for center_y, intervals in by_y:
        if _interval_coverage_fraction(intervals, plot.x0, plot.x1) >= 0.65:
            candidates.append(center_y)
    return sorted(candidates)


def _vertical_gridline_candidates(image: np.ndarray, plot: PlotBox) -> list[float]:
    """Return source vertical-line centers by reusing the horizontal detector."""

    transposed = np.transpose(image, (1, 0, 2)) if image.ndim == 3 else image.T
    transposed_plot = PlotBox(plot.y0, plot.x0, plot.y1, plot.x1)
    return _horizontal_gridline_candidates(transposed, transposed_plot)


_SIGNED_LOG_X_LABEL_GRID_MAX_PX = 6.0
_SIGNED_LOG_X_FIT_GRID_MAX_PX = 1.0


def _seat_regular_log_x_ticks_on_grid(
    calibration: AxisCalibration, image: np.ndarray, plot: PlotBox
) -> AxisCalibration:
    """Use a clear full-width log ladder's source rails, not glyph centers."""

    values = calibration.x_ticks_v
    labels = calibration.x_tick_label_px
    if (
        not calibration.x_log
        or calibration.x_value_transform is not None
        or len(values) < 3
        or len(labels) != len(values)
    ):
        return calibration
    numeric_values = np.asarray(values, dtype=float)
    if np.any(numeric_values <= 0.0):
        return calibration
    steps = np.diff(np.log10(numeric_values))
    if np.any(steps <= 0.0) or np.max(np.abs(steps - np.median(steps))) > 0.05:
        return calibration
    transposed = np.transpose(image, (1, 0, 2)) if image.ndim == 3 else image.T
    transposed_plot = PlotBox(plot.y0, plot.x0, plot.y1, plot.x1)
    try:
        grid_fit = _major_horizontal_gridline_fit(
            transposed, transposed_plot, len(values)
        )
    except RuntimeError:
        return calibration
    grid_pixels = np.asarray(grid_fit.centers, dtype=float)
    label_gap = max(
        abs(label - grid) for label, grid in zip(labels, grid_pixels)
    )
    if label_gap > max(16.0, 0.04 * plot.width):
        return calibration
    axis = fit_axis_ticks(
        [
            AxisTick(f"{value:g}", value, grid)
            for value, grid in zip(values, grid_pixels)
        ],
        "capacitance regular log X grid",
        model="log10",
    )
    errors = [
        abs((math.log10(value) - axis.b) / axis.m - grid)
        for value, grid in zip(values, grid_pixels)
    ]
    if max(errors) > 1.0:
        return calibration
    residual = float(np.sqrt(np.mean([
        (axis.m * grid + axis.b - math.log10(value)) ** 2
        for value, grid in zip(values, grid_pixels)
    ])))
    return replace(
        calibration,
        x_resid_v=residual,
        x_scale=float(axis.m),
        x_offset=float(axis.b),
        x_source=f"{calibration.x_source}_grid_seated",
        x_gridline_px=tuple(float(pixel) for pixel in grid_pixels),
        x_grid_candidate_count=grid_fit.candidate_count,
        x_grid_span_fraction=grid_fit.span_fraction,
        x_grid_residual_px=max(errors),
        x_label_to_grid_max_px=float(label_gap),
    )


def _seat_signed_log_x_ticks_on_grid(
    calibration: AxisCalibration, image: np.ndarray, plot: PlotBox
) -> AxisCalibration:
    """Refit signed raster VDS magnitudes at observed source grid centers.

    Toshiba P-channel figures print negative VDS ticks whose OCR glyph centers
    sit several pixels right of the vertical source rails.  OCR establishes
    tick identity; serving uses the unique nearby rail, with the signed source
    values and absolute-value transform retained explicitly for review.
    """

    if calibration.x_value_transform != "abs_source_negative_vds":
        return calibration
    values = calibration.x_ticks_v
    source_values = calibration.x_source_ticks_v
    label_pixels = calibration.x_tick_label_px
    if not calibration.x_log:
        raise RuntimeError("signed VDS magnitude grid seating requires a log X axis")
    if len(values) < 3 or len(label_pixels) != len(values):
        raise RuntimeError("signed log X grid seating lacks one label center per tick")
    if len(source_values) != len(values) or not all(value < 0.0 for value in source_values):
        raise RuntimeError("signed log X grid seating lacks all-negative source ticks")
    log_values = np.log10(np.asarray(values, dtype=float))
    log_steps = np.diff(log_values)
    if np.any(log_steps <= 0.0) or np.max(np.abs(log_steps - np.median(log_steps))) > 0.05:
        raise RuntimeError("signed log X ticks do not form a regular logarithmic ladder")

    candidates = _vertical_gridline_candidates(image, plot)
    assignments: list[tuple[float, float, float]] = []
    used: set[float] = set()
    for value, label_pixel in zip(values, label_pixels):
        owned = [
            pixel
            for pixel in candidates
            if pixel not in used
            and abs(pixel - label_pixel) <= _SIGNED_LOG_X_LABEL_GRID_MAX_PX
        ]
        if len(owned) != 1:
            raise RuntimeError(
                "signed log X tick does not own exactly one source gridline within "
                f"{_SIGNED_LOG_X_LABEL_GRID_MAX_PX:g} px"
            )
        grid_pixel = owned[0]
        used.add(grid_pixel)
        assignments.append((value, label_pixel, grid_pixel))

    grid_pixels = np.asarray([item[2] for item in assignments], dtype=float)
    if np.any(np.diff(grid_pixels) <= 0.0):
        raise RuntimeError("signed log X source grid centers do not follow tick-value order")
    axis = fit_axis_ticks(
        [
            AxisTick(f"{value:g}", value, grid_pixel)
            for value, _label_pixel, grid_pixel in assignments
        ],
        "capacitance signed log X grid",
        model="log10",
    )
    inverse_errors = [
        abs((math.log10(value) - axis.b) / axis.m - grid_pixel)
        for value, _label_pixel, grid_pixel in assignments
    ]
    max_inverse_error = max(inverse_errors, default=float("inf"))
    if max_inverse_error > _SIGNED_LOG_X_FIT_GRID_MAX_PX:
        raise RuntimeError(
            "signed log X fit misses a source grid center by "
            f"{max_inverse_error:.3f} px; maximum is "
            f"{_SIGNED_LOG_X_FIT_GRID_MAX_PX:g} px"
        )
    value_residual_dec = float(
        np.sqrt(
            np.mean(
                [
                    (axis.m * grid_pixel + axis.b - math.log10(value)) ** 2
                    for value, _label_pixel, grid_pixel in assignments
                ]
            )
        )
    )
    return replace(
        calibration,
        x_resid_v=value_residual_dec,
        x_scale=float(axis.m),
        x_offset=float(axis.b),
        x_source=f"{calibration.x_source}_grid_seated",
        x_gridline_px=tuple(float(item[2]) for item in assignments),
        x_grid_candidate_count=len(candidates),
        x_grid_span_fraction=float(
            (max(grid_pixels) - min(grid_pixels)) / max(1, plot.width - 1)
        ),
        x_grid_residual_px=float(max_inverse_error),
        x_label_to_grid_max_px=max(
            abs(label_pixel - grid_pixel)
            for _value, label_pixel, grid_pixel in assignments
        ),
    )


_LINEAR_Y_LABEL_GRID_MAX_PX = 3.0
_LINEAR_Y_VECTOR_LABEL_GRID_MAX_PX = 7.0
_LINEAR_Y_FIT_GRID_MAX_PX = 1.0


def _vector_horizontal_gridline_candidates(
    page, transform: CropTransform, plot: PlotBox
) -> list[float]:
    """Return full-width horizontal source strokes in crop-pixel coordinates."""

    positions: list[float] = []
    for drawing in page.get_drawings():
        if drawing.get("type") not in {"s", "fs"}:
            continue
        for item in drawing.get("items", []):
            if item[0] == "l":
                x0, y0 = transform.to_px(float(item[1].x), float(item[1].y))
                x1, y1 = transform.to_px(float(item[2].x), float(item[2].y))
                if abs(y1 - y0) > 1.0:
                    continue
                if min(x0, x1) > plot.x0 + 3.0 or max(x0, x1) < plot.x1 - 3.0:
                    continue
                center = (y0 + y1) / 2.0
                if plot.y0 - 3.0 <= center <= plot.y1 + 3.0:
                    positions.append(center)
                continue
            rect = None
            if item[0] == "re":
                rect = item[1]
            elif item[0] == "qu" and item[1].is_rectangular:
                rect = item[1].rect
            if rect is None:
                continue
            x0, y0 = transform.to_px(float(rect.x0), float(rect.y0))
            x1, y1 = transform.to_px(float(rect.x1), float(rect.y1))
            if (
                min(x0, x1) <= plot.x0 + 3.0
                and max(x0, x1) >= plot.x1 - 3.0
                and abs(y1 - y0) >= 0.50 * plot.height
            ):
                positions.extend(
                    center
                    for center in (y0, y1)
                    if plot.y0 - 3.0 <= center <= plot.y1 + 3.0
                )


    merged: list[float] = []
    for position in sorted(positions):
        if merged and position - merged[-1] <= 1.0:
            merged[-1] = (merged[-1] + position) / 2.0
        else:
            merged.append(position)
    return merged


def _seat_linear_y_ticks_on_grid(
    calibration: AxisCalibration,
    image: np.ndarray,
    plot: PlotBox,
    *,
    page=None,
    transform: CropTransform | None = None,
) -> AxisCalibration:
    """Refit an arithmetic capacitance ladder at observed source grid centers."""

    if calibration.y_log:
        return calibration
    values = calibration.y_ticks_pf
    label_pixels = calibration.y_tick_label_px
    if len(values) < 4 or len(label_pixels) != len(values):
        raise RuntimeError("linear Y grid seating lacks one label center per tick")

    vector_candidates = (
        _vector_horizontal_gridline_candidates(page, transform, plot)
        if page is not None and transform is not None
        else []
    )
    # Source vector gridlines are exact geometry, so a LABEL may sit a few px
    # off its rule (text centering drift) without the seating being wrong.
    # Raster-detected candidates get no such latitude. Either way the fit
    # residual gate below (<=1 px to grid centers) stays load-bearing, so this
    # widens ASSIGNMENT only, never acceptance.
    vector_backed = len(vector_candidates) >= len(values)
    candidates = vector_candidates
    if not vector_backed:
        candidates = _horizontal_gridline_candidates(image, plot)
    label_grid_tolerance = (
        _LINEAR_Y_VECTOR_LABEL_GRID_MAX_PX
        if vector_backed
        else _LINEAR_Y_LABEL_GRID_MAX_PX
    )

    assignments: list[tuple[float, float, float]] = []
    used: set[float] = set()
    for value, label_pixel in zip(values, label_pixels):
        owned = [
            pixel
            for pixel in candidates
            if pixel not in used
            and abs(pixel - label_pixel) <= label_grid_tolerance
        ]
        if len(owned) != 1:
            raise RuntimeError(
                "linear Y tick does not own exactly one source gridline within "
                f"{label_grid_tolerance:g} px"
            )
        grid_pixel = owned[0]
        used.add(grid_pixel)
        assignments.append((value, label_pixel, grid_pixel))

    grid_pixels = np.asarray([item[2] for item in assignments], dtype=float)
    if np.any(np.diff(grid_pixels) >= 0.0):
        raise RuntimeError("linear Y source grid centers do not follow tick-value order")
    axis = fit_axis_ticks(
        [
            AxisTick(f"{value:g}", value, grid_pixel)
            for value, _label_pixel, grid_pixel in assignments
        ],
        "capacitance linear Y grid",
        model="linear",
    )
    inverse_errors = [
        abs((value - axis.b) / axis.m - grid_pixel)
        for value, _label_pixel, grid_pixel in assignments
    ]
    max_inverse_error = max(inverse_errors, default=float("inf"))
    if max_inverse_error > _LINEAR_Y_FIT_GRID_MAX_PX:
        raise RuntimeError(
            "linear Y fit misses a source grid center by "
            f"{max_inverse_error:.3f} px; maximum is {_LINEAR_Y_FIT_GRID_MAX_PX:g} px"
        )
    value_residual_pf = float(
        np.sqrt(
            np.mean(
                [
                    (axis.m * grid_pixel + axis.b - value) ** 2
                    for value, _label_pixel, grid_pixel in assignments
                ]
            )
        )
    )
    frame_values = [axis.value(float(plot.y0)), axis.value(float(plot.y1))]
    positive_frame_values = [value for value in frame_values if value > 0.0]
    return replace(
        calibration,
        y_min_decade=math.log10(min(value for value in values if value > 0.0)),
        y_max_decade=math.log10(max(list(values) + positive_frame_values)),
        y_resid_pf=value_residual_pf,
        y_scale=float(axis.m),
        y_offset=float(axis.b),
        y_source=f"{calibration.y_source}_grid_seated",
        y_gridline_px=tuple(float(item[2]) for item in assignments),
        y_grid_candidate_count=len(candidates),
        y_grid_span_fraction=float(
            (max(grid_pixels) - min(grid_pixels)) / max(1, plot.height - 1)
        ),
        y_grid_residual_px=float(max_inverse_error),
        y_label_to_grid_max_px=max(
            abs(label_pixel - grid_pixel)
            for _value, label_pixel, grid_pixel in assignments
        ),
    )


def _major_horizontal_gridline_fit(image: np.ndarray, plot: PlotBox, count: int) -> GridlineFit:
    candidates = _horizontal_gridline_candidates(image, plot)
    if len(candidates) < count:
        raise RuntimeError(f"found only {len(candidates)} horizontal gridline candidates")

    best: tuple[float, float, float, list[float]] | None = None
    best_rejected: tuple[float, float, float, list[float]] | None = None
    for first_idx in range(len(candidates)):
        for last_idx in range(first_idx + count - 1, len(candidates)):
            first = candidates[first_idx]
            last = candidates[last_idx]
            span = last - first
            if span < plot.height * 0.94:
                continue
            expected = np.linspace(first, last, count)
            chosen = []
            used: set[int] = set()
            for target in expected:
                idx = min(
                    (i for i in range(len(candidates)) if i not in used),
                    key=lambda i: abs(candidates[i] - target),
                )
                chosen.append(candidates[idx])
                used.add(idx)
            residual = float(np.sqrt(np.mean((np.array(chosen) - expected) ** 2)))
            # A log-axis decade set should cover the whole plotted axis. Dense
            # minor log gridlines can form many very uniform but shifted
            # sequences; choosing by residual alone can pick an internal
            # sequence and mis-scale every capacitance. Prefer full-height
            # sequences first, then use residual as the tie-breaker.
            score = -span + residual * 0.05
            candidate = (score, residual, span, chosen)
            if residual <= 3.0:
                if best is None or score < best[0]:
                    best = candidate
            elif best_rejected is None or score < best_rejected[0]:
                best_rejected = candidate

    if best is None:
        residual = best_rejected[1] if best_rejected is not None else float("nan")
        raise RuntimeError(f"could not find a uniform major-grid sequence; residual {residual:.4g}")
    return GridlineFit(
        centers=sorted(best[3]),
        candidate_count=len(candidates),
        span_fraction=float(best[2] / max(1, plot.height - 1)),
        residual_px=float(best[1]),
    )


def _interval_coverage_fraction(intervals: list[tuple[int, int]], start: int, end: int) -> float:
    if not intervals:
        return 0.0
    clipped = sorted((max(start, lo), min(end, hi)) for lo, hi in intervals if hi >= start and lo <= end)
    if not clipped:
        return 0.0
    total = 0
    cur_lo, cur_hi = clipped[0]
    for lo, hi in clipped[1:]:
        if lo <= cur_hi + 1:
            cur_hi = max(cur_hi, hi)
        else:
            total += cur_hi - cur_lo + 1
            cur_lo, cur_hi = lo, hi
    total += cur_hi - cur_lo + 1
    return total / max(1, end - start + 1)


def _parse_x_ticks_from_chart_text(text: str) -> tuple[list[float], int]:
    prefix = re.split(r"\bV\s*\[\s*V\s*\]", text, maxsplit=1)[0]
    tokens = _number_tokens(prefix)
    best: tuple[list[float], int] = ([], -1)
    for idx, value in enumerate(tokens):
        if abs(value) > 1e-9:
            continue
        run = [value]
        last = value
        for candidate in tokens[idx + 1 :]:
            if candidate <= last:
                break
            run.append(candidate)
            last = candidate
        if len(run) >= 3 and _is_uniform_tick_run(run):
            if len(run) > len(best[0]) or (len(run) == len(best[0]) and idx > best[1]):
                best = (run, idx)
    return best


def _is_uniform_tick_run(values: list[float]) -> bool:
    if len(values) < 3:
        return False
    diffs = np.diff(np.asarray(values, dtype=float))
    if np.any(diffs <= 0):
        return False
    return float(np.std(diffs)) <= max(0.05, float(np.median(diffs)) * 0.15)


def _parse_y_decades_from_chart_text(text: str, x_start_index: int) -> list[float]:
    prefix = re.split(r"\bV\s*\[\s*V\s*\]", text, maxsplit=1)[0]
    tokens = _number_tokens(prefix)
    if x_start_index > 0:
        tokens = tokens[:x_start_index]
    adjacent = _numbers_adjacent_in_text(prefix)

    decades: list[float] = []
    for index, (a, b) in enumerate(zip(tokens, tokens[1:])):
        # A split "10 5" label prints the mantissa and exponent side by side.
        # Numbers separated by PROSE are not a label: Infineon's caption number
        # pairs with the condition line ("10 Typ. capacitances ... V GS = 0 V")
        # and fabricated a decade 0, stretching every tick by a full decade.
        if index < len(adjacent) and not adjacent[index]:
            continue
        if _is_power_ten_exponent(a) and abs(b - 10.0) < 1e-9:
            decades.append(a)
        elif abs(a - 10.0) < 1e-9 and _is_power_ten_exponent(b):
            decades.append(b)

    # Preserve the useful values and discard duplicate pair hits from adjacent
    # labels such as "10 4 10 3".
    out: list[float] = []
    for value in decades:
        if not out or abs(value - out[-1]) > 1e-9:
            out.append(value)
    return out


def _numbers_adjacent_in_text(text: str) -> list[bool]:
    """For each consecutive number pair, whether only blanks separate them.

    Indices align with ``zip(tokens, tokens[1:])`` because both walk the same
    tokenizer's matches in order.
    """

    spans = [match.span() for match in _NUMBER_TOKEN_RE.finditer(text)]
    return [
        text[first[1] : second[0]].strip() == ""
        for first, second in zip(spans, spans[1:])
    ]


def _is_power_ten_exponent(value: float) -> bool:
    return 0.0 <= value <= 6.0 and abs(value - round(value)) < 1e-9


def trace_data_points(
    trace: Trace, plot: PlotBox, calibration: AxisCalibration
) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for x, y in trace.points:
        vds = calibration_v_of_x(calibration, plot, x)
        log_c = calibration_log_c_of_y(calibration, plot, y)
        out.append((float(vds), float(10.0**log_c)))
    return out


def calibration_v_of_x(calibration: AxisCalibration, plot: PlotBox, x: float) -> float:
    if calibration.x_scale is not None and calibration.x_offset is not None:
        fitted = float(calibration.x_scale * x + calibration.x_offset)
        return float(10.0 ** fitted) if calibration.x_log else fitted
    x_norm = _clip01((x - plot.x0) / max(1, plot.width - 1))
    if calibration.x_log:
        lo, hi = math.log10(calibration.x_min_v), math.log10(calibration.x_max_v)
        return float(10.0 ** (lo + x_norm * (hi - lo)))
    return float(calibration.x_min_v + x_norm * (calibration.x_max_v - calibration.x_min_v))


def calibration_x_of_v(calibration: AxisCalibration, plot: PlotBox, vds: float) -> float:
    if calibration.x_scale is not None and calibration.x_offset is not None and abs(calibration.x_scale) > 1e-12:
        value = math.log10(vds) if calibration.x_log and vds > 0.0 else vds
        return float((value - calibration.x_offset) / calibration.x_scale)
    if calibration.x_log:
        lo, hi = math.log10(calibration.x_min_v), math.log10(calibration.x_max_v)
        x_norm = (math.log10(max(vds, 1e-12)) - lo) / max(1e-12, hi - lo)
    else:
        x_norm = (vds - calibration.x_min_v) / max(1e-12, calibration.x_max_v - calibration.x_min_v)
    return float(plot.x0 + _clip01(x_norm) * max(1, plot.width - 1))


def calibration_log_c_of_y(calibration: AxisCalibration, plot: PlotBox, y: float) -> float:
    if calibration.y_scale is not None and calibration.y_offset is not None:
        value = float(calibration.y_scale * y + calibration.y_offset)
        return value if calibration.y_log else float(math.log10(max(value, 1e-12)))
    y_norm = _clip01((plot.y1 - y) / max(1, plot.height - 1))
    return float(calibration.y_min_decade + y_norm * (calibration.y_max_decade - calibration.y_min_decade))


def calibration_y_of_log_c(calibration: AxisCalibration, plot: PlotBox, log_c: float) -> float:
    if calibration.y_scale is not None and calibration.y_offset is not None and abs(calibration.y_scale) > 1e-12:
        value = log_c if calibration.y_log else 10.0 ** log_c
        return float((value - calibration.y_offset) / calibration.y_scale)
    y_norm = (log_c - calibration.y_min_decade) / max(1e-12, calibration.y_max_decade - calibration.y_min_decade)
    return float(plot.y1 - _clip01(y_norm) * max(1, plot.height - 1))


def _clip01(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


def arrays_for_trace_data(data_points: list[tuple[float, float]]) -> tuple[np.ndarray, np.ndarray]:
    by_v: dict[float, list[float]] = {}
    for vds, cap in data_points:
        by_v.setdefault(float(vds), []).append(float(cap))
    vds = np.array(sorted(by_v), dtype=float)
    cap = np.array([float(np.median(by_v[v])) for v in vds], dtype=float)
    return vds, cap


def axis_calibration_to_json(calibration: AxisCalibration) -> dict[str, object]:
    payload = {
        "source": calibration.source,
        "x_source": calibration.x_source,
        "y_source": calibration.y_source,
        "x_min_v": calibration.x_min_v,
        "x_max_v": calibration.x_max_v,
        "y_min_decade": calibration.y_min_decade,
        "y_max_decade": calibration.y_max_decade,
        "x_ticks_v": list(calibration.x_ticks_v),
        "y_decades": list(calibration.y_decades),
        "y_log": calibration.y_log,
        "y_ticks_pf": list(calibration.y_ticks_pf),
        "x_log": calibration.x_log,
        "x_resid_v": calibration.x_resid_v,
        "y_resid_dec": calibration.y_resid_dec,
        "y_resid_pf": calibration.y_resid_pf,
        "y_tick_label_px": list(calibration.y_tick_label_px),
        "y_label_to_grid_max_px": calibration.y_label_to_grid_max_px,
        "x_scale": calibration.x_scale,
        "x_offset": calibration.x_offset,
        "y_scale": calibration.y_scale,
        "y_offset": calibration.y_offset,
        "y_gridline_px": list(calibration.y_gridline_px),
        "y_grid_candidate_count": calibration.y_grid_candidate_count,
        "y_grid_span_fraction": calibration.y_grid_span_fraction,
        "y_grid_residual_px": calibration.y_grid_residual_px,
    }
    if calibration.x_source_ticks_v:
        payload["x_source_ticks_v"] = list(calibration.x_source_ticks_v)
    if calibration.x_value_transform is not None:
        payload["x_value_transform"] = calibration.x_value_transform
    if calibration.x_gridline_px:
        payload.update(
            {
                "x_tick_label_px": list(calibration.x_tick_label_px),
                "x_label_to_grid_max_px": calibration.x_label_to_grid_max_px,
                "x_gridline_px": list(calibration.x_gridline_px),
                "x_grid_candidate_count": calibration.x_grid_candidate_count,
                "x_grid_span_fraction": calibration.x_grid_span_fraction,
                "x_grid_residual_px": calibration.x_grid_residual_px,
            }
        )
    return payload


def calibration_delta_to_json(
    primary: AxisCalibration | None, baseline: AxisCalibration | None, plot: PlotBox
) -> dict[str, float] | None:
    if primary is None or baseline is None:
        return None
    return {
        "left_v_delta": calibration_v_of_x(primary, plot, plot.x0) - calibration_v_of_x(baseline, plot, plot.x0),
        "right_v_delta": calibration_v_of_x(primary, plot, plot.x1) - calibration_v_of_x(baseline, plot, plot.x1),
        "top_dec_delta": calibration_log_c_of_y(primary, plot, plot.y0)
        - calibration_log_c_of_y(baseline, plot, plot.y0),
        "bottom_dec_delta": calibration_log_c_of_y(primary, plot, plot.y1)
        - calibration_log_c_of_y(baseline, plot, plot.y1),
    }


def axis_calibration_is_trusted(calibration: AxisCalibration | None) -> bool:
    if calibration is None:
        return False
    if calibration.source == "chart_text":
        return False
    return calibration.x_scale is not None and calibration.y_scale is not None
