"""Axis calibration and unit reading for RDS(on)-versus-VGS panels.

Tick labels come from the PDF text layer first; only when that cannot
calibrate an axis is the crop OCRed (sparse OCR of the whole crop, then
blob-by-blob voted OCR of the tick bands). Every OCR path ends in the same
gates as the text layer -- a monotone linear/log fit within a residual limit,
bound to the frame's grid rules -- and an OCR-read axis needs at least three
consistent ticks. The RDS unit is read from the panel's own y-axis title,
never defaulted: mOhm against Ohm is a thousandfold error nothing else catches.
"""

from __future__ import annotations

import math
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import pymupdf

from .capacitance_types import PlotBox
from .crop_transform import CropTransform
from .diode_forward_voltage import (
    TextLabel,
    _anchor_linear_axis_to_plot_frame,
    _full_span_grid_lines,
    _NUMERIC_RE,
    _normalize_numeric_text,
    _plot_hint,
    _select_axis,
    _snap_axis_to_grid,
)
from .find_charts import PageText
from .numeric_axis import NumericAxis, fit_numeric_axis
from .rdson_gate_voltage_locate import LocatedPanel
from .rdson_temperature import _vector_full_span_grid_lines
from .region_ocr import _tesseract_words

CROP_DPI = 300
MAX_AXIS_RESIDUAL_PT = 0.6

_MOHM_UNIT_RE = re.compile(
    r"m\s*[ΩΩ:]|m\s*ohm|milli\s*ohm|[(\[]\s*m\s*[WQO0ΩΩ:]\s*[)\]]",
    re.IGNORECASE,
)
_OHM_UNIT_RE = re.compile(
    r"(?<![mMµu\w])[ΩΩ]|[(\[]\s*[ΩΩ:WQ]\s*[)\]]|\bohms?\b",
)


class PanelRefused(RuntimeError):
    """Nothing trustworthy can be served from this panel."""


@dataclass(frozen=True)
class Calibration:
    plot: PlotBox
    x_axis: NumericAxis
    y_axis: NumericAxis
    tick_source: str
    grid_binding: str
    residual_limit_px: float
    tick_scatter_px: float = 0.0


def _text_labels(words: PageText, transform: CropTransform, shape) -> list[TextLabel]:
    height, width = shape[:2]
    labels = []
    for word in words.words:
        x0, y0 = transform.to_px(word.x0, word.y0)
        x1, y1 = transform.to_px(word.x1, word.y1)
        if -5 <= 0.5 * (x0 + x1) <= width + 5 and -5 <= 0.5 * (y0 + y1) <= height + 5:
            text = _normalize_numeric_text(word.text.strip().replace(",", "."))
            labels.append(TextLabel(text, 0.5 * (x0 + x1), 0.5 * (y0 + y1), x0, x1))
    return labels


def _ocr_crop_labels(out_dir: Path, panel: LocatedPanel, stem: str, shape) -> list[TextLabel]:
    """OCR the crop itself (sparse text) into crop-pixel labels with boxes."""
    crop = out_dir / "crops" / panel.part / f"{stem}.png"
    if shutil.which("tesseract") is None:
        return []
    height, width = shape[:2]
    words = _tesseract_words(
        crop, clip=pymupdf.Rect(0, 0, width, height), scale_x=1.0, scale_y=1.0,
        psm=11, timeout=90.0, whitelist=None, min_confidence=30.0,
    )
    labels: list[TextLabel] = []
    for x0, y0, x1, y1, text in words:
        if re.fullmatch(r"[\dO.,]+", text):
            text = text.replace(",", ".").replace("O", "0")
        labels.append(_BoxedLabel(_normalize_numeric_text(text), 0.5 * (x0 + x1), 0.5 * (y0 + y1), x0, x1, y0, y1))
    return labels


def _ocr_axis_band_labels(gray, plot: PlotBox, out_dir: Path, panel, stem: str) -> list[TextLabel]:
    """OCR tick labels blob by blob in the bands left of and below the frame.

    Small raster tick labels (a 150 dpi embedded chart) defeat sparse OCR of
    the whole crop, and tesseract drops isolated single digits in sparse mode.
    So the bands are binarised, split into label blobs (connected components
    merged along a text line), and each blob is upscaled and read as ONE line
    with a digit whitelist. A misread cannot pass silently: the ladder still
    has to fit a monotone linear/log axis within the residual limit and bind
    to the grid.
    """
    if shutil.which("tesseract") is None:
        return []
    height, width = gray.shape[:2]
    plot_h = plot.y1 - plot.y0
    bands = {
        "y": (0, max(0, plot.y0 - 25), max(1, plot.x0 - 4), min(height, plot.y1 + 25)),
        "x": (max(0, plot.x0 - 40), min(height - 1, plot.y1 + 4), min(width, plot.x1 + 40),
              min(height, plot.y1 + int(0.12 * plot_h) + 30)),
    }
    labels: list[TextLabel] = []
    folder = out_dir / "work" / "axis_ocr" / panel.part / stem
    folder.mkdir(parents=True, exist_ok=True)
    for name, (x0, y0, x1, y1) in bands.items():
        sub = gray[y0:y1, x0:x1]
        if sub.size == 0:
            continue
        ink = (sub < 150).astype(np.uint8)
        for index, (bx0, by0, bx1, by1) in enumerate(_text_blobs(ink)):
            if not 6 <= by1 - by0 <= 60 or bx1 - bx0 > 8 * (by1 - by0):
                continue
            text = _vote_blob_digits(sub, (bx0, by0, bx1, by1), folder / f"{name}_{index:02d}")
            if text is None:
                continue
            ax0, ay0, ax1, ay1 = x0 + bx0, y0 + by0, x0 + bx1, y0 + by1
            labels.append(_BoxedLabel(_normalize_numeric_text(text), 0.5 * (ax0 + ax1), 0.5 * (ay0 + ay1), ax0, ax1, ay0, ay1))
    return labels


def _vote_blob_digits(sub, box, stem_path: Path) -> str | None:
    """Read one tick-label blob six ways; keep a reading only if at least half agree."""
    bx0, by0, bx1, by1 = box
    pad = 4
    piece = sub[max(0, by0 - pad) : by1 + pad, max(0, bx0 - pad) : bx1 + pad]
    variants = []
    for factor, blur in ((4.0, False), (6.0, True)):
        up = cv2.resize(piece, None, fx=factor, fy=factor, interpolation=cv2.INTER_CUBIC)
        if blur:
            up = cv2.GaussianBlur(up, (3, 3), 0)
        variants.append(up)
        _thr, binary = cv2.threshold(up, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        variants.append(binary)
    readings: list[str] = []
    for v_index, variant in enumerate(variants):
        framed = cv2.copyMakeBorder(variant, 24, 24, 24, 24, cv2.BORDER_CONSTANT, value=255)
        target = stem_path.with_name(f"{stem_path.name}_v{v_index}.png")
        cv2.imwrite(str(target), framed)
        for psm in ("7", "8"):
            proc = subprocess.run(
                ["tesseract", str(target), "stdout", "--psm", psm, "-c", "tessedit_char_whitelist=0123456789.-"],
                capture_output=True, text=True, timeout=30,
            )
            text = proc.stdout.strip()
            if proc.returncode == 0 and re.fullmatch(r"-?\d+(?:\.\d+)?", text):
                readings.append(text)
    if not readings:
        return None
    best = max(set(readings), key=readings.count)
    return best if readings.count(best) >= 4 else None


def _text_blobs(ink: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Bounding boxes of text-line blobs: components merged along a baseline."""
    count, _labels, stats, _ = cv2.connectedComponentsWithStats(ink, 8)
    boxes = [
        [int(x), int(y), int(x + w), int(y + h)]
        for x, y, w, h, area in stats[1:]
        if area >= 4 and h >= 4
    ]
    boxes.sort(key=lambda b: b[0])
    merged: list[list[int]] = []
    for box in boxes:
        for other in merged:
            height = max(box[3] - box[1], other[3] - other[1])
            overlap = min(box[3], other[3]) - max(box[1], other[1])
            gap = box[0] - other[2]
            if overlap >= 0.5 * min(box[3] - box[1], other[3] - other[1]) and gap <= 0.6 * height:
                other[0], other[1] = min(other[0], box[0]), min(other[1], box[1])
                other[2], other[3] = max(other[2], box[2]), max(other[3], box[3])
                break
        else:
            merged.append(list(box))
    return [tuple(b) for b in merged]


def ocr_plot_labels(gray, plot: PlotBox, out_dir: Path, panel, stem: str) -> list[TextLabel]:
    """OCR the plot interior upscaled 2x and binarised, for in-plot curve labels.

    Raster charts print "TJ=125C" or "ID=5A" small and anti-aliased; sparse OCR
    of the 300 dpi crop misses most of them. Labels only ever feed the
    conservative binder, which leaves a curve's parameter unknown rather than
    guess, so a missed or misread label costs a binding, never a value.
    """
    if shutil.which("tesseract") is None:
        return []
    sub = gray[plot.y0 : plot.y1, plot.x0 : plot.x1]
    if sub.size == 0:
        return []
    scale = 2.0
    up = cv2.resize(sub, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    _thr, binary = cv2.threshold(up, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    target = out_dir / "work" / "plot_ocr" / panel.part / f"{stem}.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(target), binary)
    words = _tesseract_words(
        target, clip=pymupdf.Rect(0, 0, binary.shape[1], binary.shape[0]), scale_x=1.0, scale_y=1.0,
        psm=11, timeout=90.0, whitelist=None, min_confidence=40.0,
    )
    labels: list[TextLabel] = []
    for x0, y0, x1, y1, text in words:
        ax0, ay0 = plot.x0 + x0 / scale, plot.y0 + y0 / scale
        ax1, ay1 = plot.x0 + x1 / scale, plot.y0 + y1 / scale
        labels.append(_BoxedLabel(text, 0.5 * (ax0 + ax1), 0.5 * (ay0 + ay1), ax0, ax1, ay0, ay1))
    return labels


@dataclass(frozen=True)
class _BoxedLabel(TextLabel):
    y0: float = 0.0
    y1: float = 0.0


def _plot_frame_px(panel: LocatedPanel, transform: CropTransform, gray) -> PlotBox:
    """The owned frame in crop pixels, refined to the crop's own frame rails."""
    if panel.frame_source == "embedded_image":
        try:
            box, _source, _x, _y = _plot_hint(gray)
            return box
        except RuntimeError as error:
            raise PanelRefused(f"plot_frame_not_found_in_embedded_image: {error}") from error
    x0, y0 = transform.to_px(panel.frame_pt[0], panel.frame_pt[1])
    x1, y1 = transform.to_px(panel.frame_pt[2], panel.frame_pt[3])
    hint = PlotBox(int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1)))
    if panel.frame_source == "vector":
        return hint
    xs, ys = _full_span_grid_lines(gray, hint)

    def nearest(lines, edge):
        near = [line for line in lines if abs(line - edge) <= 8]
        return int(round(min(near, key=lambda line: abs(line - edge)))) if near else edge

    return PlotBox(nearest(xs, hint.x0), nearest(ys, hint.y0), nearest(xs, hint.x1), nearest(ys, hint.y1))


def _calibrate(
    gray, plot: PlotBox, labels: list[TextLabel], source: str, transform: CropTransform, page=None
) -> Calibration:
    numeric = [
        label for label in labels
        if _NUMERIC_RE.fullmatch(label.text) or re.fullmatch(r"10\^[+-]?\d+", label.text)
    ]
    inside = [
        label for label in numeric
        if not (plot.x0 + 4 < label.cx < plot.x1 - 4 and plot.y0 + 4 < label.cy < plot.y1 - 4)
    ]
    dropped: list[str] = []
    raw_x = _axis_or_robust(inside, plot, "x", dropped)
    raw_y = _axis_or_robust(inside, plot, "y", dropped)
    if dropped:
        source = f"{source} (robust ladder; dropped misread ticks {sorted(set(dropped))})"
    if "ocr" in source and min(len(raw_x.ticks), len(raw_y.ticks)) < 3:
        raise RuntimeError("an OCR-read axis needs >= 3 consistent tick labels")
    for axis, name, lo, hi in ((raw_x, "x", plot.x0, plot.x1), (raw_y, "y", plot.y0, plot.y1)):
        outside = [t for t in axis.ticks if not lo - 6 <= t.pixel <= hi + 6]
        if outside:
            raise RuntimeError(f"{name} axis: consumed tick {outside[0].text!r} lies outside the plot frame")
    search = PlotBox(max(0, plot.x0 - 6), max(0, plot.y0 - 6), min(gray.shape[1] - 1, plot.x1 + 6), min(gray.shape[0] - 1, plot.y1 + 6))
    vertical, horizontal = _full_span_grid_lines(gray, search, plot)
    if page is not None:
        vector_x, vector_y = _vector_full_span_grid_lines(page, transform, plot)
        vertical, horizontal = _merge_lines(vector_x, vertical), _merge_lines(vector_y, horizontal)
    binding = "snapped_to_full_span_grid"
    try:
        x_axis = _snap_axis_to_grid(raw_x, vertical, "X axis", authoritative=True)
        y_axis = _snap_axis_to_grid(raw_y, horizontal, "Y axis", authoritative=True)
        if x_axis is raw_x or y_axis is raw_y:
            binding = "label_centroids_only (grid lines did not bind every tick)"
    except RuntimeError as error:
        x_axis, y_axis = raw_x, raw_y
        binding = f"label_centroids_only ({error})"
    scatter = max(x_axis.residual_px, y_axis.residual_px)
    plot = _seat_on_outer_tick_rules(plot, vertical, horizontal, x_axis, y_axis)
    x_axis = _anchor_linear_axis_to_plot_frame(x_axis, plot, "x")
    y_axis = _anchor_linear_axis_to_plot_frame(y_axis, plot, "y")
    return Calibration(plot, x_axis, y_axis, source, binding, MAX_AXIS_RESIDUAL_PT * transform.scale_x, scatter)


def _seat_on_outer_tick_rules(plot: PlotBox, vertical, horizontal, x_axis, y_axis) -> PlotBox:
    """Move a frame edge that sits on no rule onto the outer tick's rule just inside it.

    The 150 dpi page-frame detector put FDP8870's left edge at 242 px, 12 px
    left of the printed frame (a filled rule at 253-255 px that is also the
    2 V tick; review R2-6). An edge is moved only when it has no full-span rule
    within 2 px AND the outermost consumed tick lies on a rule no more than
    15 px inside it. Calibration is unaffected: it comes from the ticks.
    """
    def seat(edge: int, lines, tick: float, inward: int) -> int:
        if any(abs(line - edge) <= 2 for line in lines):
            return edge
        if 0 < (tick - edge) * inward <= 15 and any(abs(line - tick) <= 1.5 for line in lines):
            return int(round(tick))
        return edge

    xs = sorted(t.pixel for t in x_axis.ticks)
    ys = sorted(t.pixel for t in y_axis.ticks)
    return PlotBox(
        seat(plot.x0, vertical, xs[0], +1),
        seat(plot.y0, horizontal, ys[0], +1),
        seat(plot.x1, vertical, xs[-1], -1),
        seat(plot.y1, horizontal, ys[-1], -1),
    )


def _axis_or_robust(labels: list[TextLabel], plot: PlotBox, orientation: str, dropped: list[str]) -> NumericAxis:
    """The shared tick selector, or -- if one misread label breaks it -- the
    largest subset of the aligned ladder that fits one linear axis.

    OCR drops decimal points ("0.5" -> "05") and confuses digits; the shared
    selector fits every aligned label and fails on the first misread. Here
    every pair of aligned labels proposes a linear value(pixel) map, scored by
    how many labels it EXPLAINS: read exactly (within max(4 px, 0.8 % of the
    axis) of the predicted position) or read with the decimal point lost
    ("05" where 0.5 is predicted). Scoring the decimal-dropped readings is
    what defeats the self-consistent x10 ladder those same readings form on
    their own (0, 5, 15, 25 ... is collinear too). Only exact readings are
    fitted; >= 3 of them, a score >= 60 % of the ladder, and a margin of 2
    over the best different hypothesis are required; the rest is reported.
    """
    try:
        return _select_axis(labels, plot, orientation)
    except RuntimeError as error:
        first = error
    numeric = [l for l in labels if _NUMERIC_RE.fullmatch(l.text)]
    if orientation == "x":
        band = [l for l in numeric if plot.x0 - 40 <= l.cx <= plot.x1 + 40 and 0 < l.cy - plot.y1 <= 0.25 * (plot.y1 - plot.y0) + 20]
        key, pos = (lambda l: l.cy), (lambda l: l.cx)
    else:
        band = [l for l in numeric if l.x1 <= plot.x0 + 4 and plot.y0 - 30 <= l.cy <= plot.y1 + 30]
        key, pos = (lambda l: l.x1), (lambda l: l.cy)
    span = (plot.x1 - plot.x0) if orientation == "x" else (plot.y1 - plot.y0)
    tolerance = max(4.0, 0.008 * span)
    best: list[TextLabel] = []
    best_score, runner_up = 0, 0
    for group in _aligned_groups(band, key, 9.0):
        group = _dedupe_same_reading(sorted(group, key=pos), pos)
        hypotheses: dict[tuple[float, float], tuple[int, list[TextLabel]]] = {}
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                va, vb = float(a.text), float(b.text)
                if pos(b) - pos(a) < 5 or va == vb:
                    continue
                slope = (vb - va) / (pos(b) - pos(a))
                exact, score = [], 0
                for l in group:
                    if abs((float(l.text) - va) / slope + pos(a) - pos(l)) <= tolerance:
                        exact.append(l)
                        score += 1
                    elif _decimal_dropped(l.text, lambda v: (v - va) / slope + pos(a), pos(l), tolerance):
                        score += 1
                ladder = (round(slope, 9), round(va - slope * pos(a), 6))
                hypotheses[ladder] = (score, exact)
        ranked = sorted(hypotheses.values(), key=lambda item: (-item[0], -len(item[1])))
        if not ranked:
            continue
        score, exact = ranked[0]
        second = next((s2 for s2, e2 in ranked[1:] if set(map(id, e2)) != set(map(id, exact))), 0)
        if score > best_score and len(exact) >= 3 and score >= 0.6 * len(group):
            best, best_score, runner_up = exact, score, second
    if len(best) < 3 or best_score - runner_up < 2:
        raise first
    ticks = [(l.text, pos(l)) for l in best]
    axis = fit_numeric_axis(ticks, f"{orientation.upper()} axis")
    for l in band:
        if abs(key(l) - key(best[0])) > 9.0:
            continue
        coordinate = float(l.text)
        if axis.model == "log10":
            if coordinate <= 0:
                dropped.append(l.text)
                continue
            coordinate = math.log10(coordinate)
        if abs((coordinate - axis.b) / axis.m - pos(l)) > tolerance:
            dropped.append(l.text)
    if max(p for _t, p in ticks) - min(p for _t, p in ticks) < 0.35 * span:
        raise first
    return axis


def _decimal_dropped(text: str, position_of, position: float, tolerance: float) -> bool:
    """A reading that fits the ladder once its lost decimal point is restored ("05" -> 0.5)."""
    if "." in text:
        return False
    value = float(text)
    return any(
        abs(position_of(value / 10**shift) - position) <= tolerance
        for shift in range(1, len(text.lstrip("-")) + 1)
    )


def _dedupe_same_reading(labels, pos):
    out = []
    for label in labels:
        if out and abs(pos(label) - pos(out[-1])) < 3.0 and label.text == out[-1].text:
            continue
        out.append(label)
    return out


def _aligned_groups(labels, key, tolerance):
    groups: list[list] = []
    for label in sorted(labels, key=key):
        if groups and abs(key(label) - key(groups[-1][-1])) <= tolerance:
            groups[-1].append(label)
        else:
            groups.append([label])
    return groups


def _merge_lines(preferred: tuple[float, ...], other: tuple[float, ...]) -> tuple[float, ...]:
    """Union of two rule lists; a vector rule wins over a raster one within 1.5 px."""
    merged = list(preferred)
    merged.extend(line for line in other if all(abs(line - kept) > 1.5 for kept in preferred))
    return tuple(sorted(merged))


def _y_unit(page, words: PageText, transform, plot: PlotBox, image, out_dir, panel, stem):
    """Read the RDS unit from the panel's own y-axis title (never defaulted)."""
    gx0, gy0 = transform.to_pt(0, plot.y0 - 40)
    gx1, gy1 = transform.to_pt(plot.x0 - 2, plot.y1 + 10)
    gutter = pymupdf.Rect(gx0, gy0, gx1, gy1)
    text = " ".join(
        w.text for w in words.words
        if gutter.x0 <= 0.5 * (w.x0 + w.x1) <= gutter.x1 and gutter.y0 <= 0.5 * (w.y0 + w.y1) <= gutter.y1
    )
    unit = _unit_from_text(text)
    if unit is None:
        ocr = _ocr_rotated_gutter(image, plot, out_dir, panel, stem)
        text = f"{text} || OCR: {ocr}" if ocr else text
        unit = _unit_from_text(ocr or "")
    if unit is None:
        return text, None, None
    return text, unit[0], unit[1]


def _unit_from_text(text: str) -> tuple[str, float] | None:
    mohm = bool(_MOHM_UNIT_RE.search(text))
    ohm = bool(_OHM_UNIT_RE.search(_MOHM_UNIT_RE.sub(" ", text)))
    if mohm and not ohm:
        return "mOhm", 1.0
    if ohm and not mohm:
        return "Ohm", 1000.0
    return None


def _ocr_rotated_gutter(image, plot: PlotBox, out_dir: Path, panel, stem: str) -> str:
    if shutil.which("tesseract") is None:
        return ""
    strip = image[max(0, plot.y0 - 20) : plot.y1 + 20, 0 : max(1, plot.x0 - 2)]
    if strip.size == 0:
        return ""
    target = out_dir / "work" / "gutter_ocr" / panel.part / f"{stem}.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(target), np.rot90(strip, k=-1))
    proc = subprocess.run(
        ["tesseract", str(target), "stdout", "--psm", "6"],
        capture_output=True, text=True, timeout=60,
    )
    return " ".join(proc.stdout.split()) if proc.returncode == 0 else ""
