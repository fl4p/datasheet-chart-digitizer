"""Generic position-based calibration for numeric linear or log chart axes."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from .capacitance_types import PlotBox

_NUMBER_RE = re.compile(r"^[+-]?\d+(?:\.\d+)?$")
_EXPLICIT_POWER_RE = re.compile(r"^10\^([+-]?\d+)$")
_ENGINEERING_RE = re.compile(
    r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+))(?:(?:[eE]([+-]?\d+))|([u\u00b5\u03bcmk]))?$"
)
_TIME_SCALES = {"u": 1e-6, "m": 1e-3, "k": 1e3}
_OUTER_FRAME_STEP_TOLERANCE = 0.18
_OUTER_PARTIAL_STEP_TOLERANCE = 0.35


@dataclass(frozen=True)
class AxisTick:
    text: str
    value: float
    pixel: float
    normalized_text: str | None = None


@dataclass(frozen=True)
class NumericAxis:
    """An axis whose linearized coordinate is ``m * pixel + b``."""

    model: str  # "linear" or "log10"
    m: float
    b: float
    ticks: tuple[AxisTick, ...]
    residual_px: float
    candidate_residuals_px: tuple[tuple[str, float], ...]

    def value(self, pixel: float) -> float:
        coordinate = self.m * pixel + self.b
        return 10.0**coordinate if self.model == "log10" else coordinate


def fit_numeric_axis(
    labels: list[tuple[str, float]],
    name: str = "axis",
    *,
    quantity: str | None = None,
    text_source: str = "native",
) -> NumericAxis:
    """Fit a linear/log axis from label text and pixel positions.

    At least two labels are required.  Two positive labels cannot distinguish
    linear from logarithmic spacing and therefore refuse unless one label is
    zero (which rules out log).  Superscript-aware PDF extraction supplies
    powers as ``10^n``. Raw strings such as ``100,101,...`` remain ordinary
    numbers because they could be a genuine narrow linear axis.
    """
    parsed, corrected_pixels = _parse_labels(labels, name, quantity, text_source)
    try:
        axis = fit_axis_ticks(parsed, name)
    except RuntimeError as exc:
        if corrected_pixels:
            raise RuntimeError(
                f"{name}: OCR-corrected time token fails log-position sequence ({exc})"
            ) from exc
        raise
    if corrected_pixels:
        _validate_ocr_corrections(axis, corrected_pixels, name)
    return axis


def fit_axis_ticks(ticks: Sequence[AxisTick], name: str = "axis") -> NumericAxis:
    """Fit a linear/log axis from already-parsed numeric ticks.

    The supported PUBLIC entry point for consumers that have their own tick
    labels (text/context parsing lives in ``fit_numeric_axis``, which calls this).
    Owns the shared calibration contract: min-count / underdetermined refusal,
    strictly-monotone requirement, linear-vs-log selection, candidate residuals,
    and strict residual rejection — no consumer should re-implement it."""
    parsed = list(ticks)
    if len(parsed) < 2:
        raise RuntimeError(f"{name}: need >=2 numeric tick labels, found {len(parsed)}")
    pixels = np.asarray([tick.pixel for tick in parsed], dtype=float)
    values = np.asarray([tick.value for tick in parsed], dtype=float)
    order = np.argsort(pixels)
    pixels, values = pixels[order], values[order]
    parsed = [parsed[int(index)] for index in order]
    if np.any(np.diff(pixels) <= 0) or not (
        np.all(np.diff(values) > 0) or np.all(np.diff(values) < 0)
    ):
        raise RuntimeError(f"{name}: tick positions/values are not strictly monotone")

    candidates: list[tuple[float, str, float, float]] = []
    for model in ("linear", "log10"):
        if model == "log10" and np.any(values <= 0):
            continue
        coordinates = values if model == "linear" else np.log10(values)
        m, b = np.polyfit(pixels, coordinates, 1)
        if abs(m) < 1e-12:
            continue
        predicted_pixels = (coordinates - b) / m
        residual = float(np.sqrt(np.mean((predicted_pixels - pixels) ** 2)))
        candidates.append((residual, model, float(m), float(b)))
    if not candidates:
        raise RuntimeError(f"{name}: no valid linear/log calibration")
    candidates.sort()
    span_px = float(pixels[-1] - pixels[0])
    if candidates[0][0] > max(1.5, 0.03 * span_px):
        raise RuntimeError(f"{name}: best calibration residual {candidates[0][0]:.2f}px is untrusted")
    if len(candidates) > 1 and candidates[1][0] - candidates[0][0] <= max(0.75, 0.005 * span_px):
        raise RuntimeError(
            f"{name}: linear/log calibration is ambiguous "
            f"({candidates[0][0]:.2f}px vs {candidates[1][0]:.2f}px)"
        )
    residual, model, m, b = candidates[0]
    diagnostics = tuple(
        (candidate_model, candidate_residual)
        for candidate_residual, candidate_model, _, _ in candidates
    )
    return NumericAxis(model, m, b, tuple(parsed), residual, diagnostics)


def tick_aligned_plot(x_axis: NumericAxis, y_axis: NumericAxis, hint: PlotBox) -> PlotBox:
    """Derive plot edges from ticks, accepting only nearby detector hints."""
    xs = sorted(tick.pixel for tick in x_axis.ticks)
    ys = sorted(tick.pixel for tick in y_axis.ticks)
    if len(xs) < 2 or len(ys) < 2:
        raise RuntimeError("plot: both axes need >=2 ticks")
    return PlotBox(
        x0=int(round(_nearby_edge(xs[0], hint.x0, xs, "min", x_axis.model == "log10"))),
        y0=int(round(_nearby_edge(ys[0], hint.y0, ys, "min", y_axis.model == "log10"))),
        x1=int(round(_nearby_edge(xs[-1], hint.x1, xs, "max", x_axis.model == "log10"))),
        y1=int(round(_nearby_edge(ys[-1], hint.y1, ys, "max", y_axis.model == "log10"))),
    )


def _nearby_edge(
    tick_edge: float,
    hint_edge: float,
    positions: list[float],
    side: str,
    allow_partial_interval: bool = False,
) -> float:
    if (side == "min" and hint_edge > tick_edge) or (side == "max" and hint_edge < tick_edge):
        return tick_edge
    steps = np.diff(np.asarray(positions, dtype=float))
    fraction = (
        _OUTER_PARTIAL_STEP_TOLERANCE
        if allow_partial_interval
        else _OUTER_FRAME_STEP_TOLERANCE
    )
    tolerance = max(4.0, fraction * float(np.median(steps)))
    return float(hint_edge) if abs(hint_edge - tick_edge) <= tolerance else tick_edge


def _parse_labels(
    labels: list[tuple[str, float]],
    name: str,
    quantity: str | None,
    text_source: str,
) -> tuple[list[AxisTick], set[float]]:
    if quantity not in (None, "time_s"):
        raise ValueError(f"unsupported axis quantity: {quantity}")
    ticks: list[AxisTick] = []
    corrected_pixels: set[float] = set()
    for raw_text, raw_pixel in labels:
        text = raw_text.strip().replace("−", "-")
        pixel = float(raw_pixel)
        parsed = _parse_numeric_token(text, quantity, text_source)
        if parsed is None:
            continue
        value, normalized_text, corrected = parsed
        ticks.append(AxisTick(text, value, pixel, normalized_text))
        if corrected:
            corrected_pixels.add(pixel)
    if len(ticks) != len(labels):
        raise RuntimeError(f"{name}: non-numeric tick label in selected run")
    return ticks, corrected_pixels


def _parse_numeric_token(
    text: str, quantity: str | None, text_source: str
) -> tuple[float, str | None, bool] | None:
    explicit = _EXPLICIT_POWER_RE.fullmatch(text)
    if explicit is not None:
        return 10.0 ** int(explicit.group(1)), None, False
    if _NUMBER_RE.fullmatch(text):
        return float(text), None, False
    if quantity != "time_s":
        return None

    normalized = text.replace("\u00b5", "u").replace("\u03bc", "u")
    corrected = False
    if text_source == "tesseract" and re.fullmatch(r"[Il][umk]", normalized):
        normalized = "1" + normalized[1:]
        corrected = True
    match = _ENGINEERING_RE.fullmatch(normalized)
    if match is None:
        return None
    mantissa = float(match.group(1))
    exponent = match.group(2)
    suffix = match.group(3)
    if exponent is not None:
        value = mantissa * 10.0 ** int(exponent)
        canonical = f"{match.group(1)}e{int(exponent)}"
    elif suffix is not None:
        suffix = suffix.replace("\u00b5", "u").replace("\u03bc", "u")
        value = mantissa * _TIME_SCALES[suffix]
        canonical = f"{match.group(1)}{suffix}"
    else:
        value = mantissa
        canonical = match.group(1)
    return value, canonical, corrected


def _validate_ocr_corrections(axis: NumericAxis, corrected_pixels: set[float], name: str) -> None:
    """Require a corrected OCR token to agree with its log-axis neighbours."""
    ticks = sorted(axis.ticks, key=lambda tick: tick.pixel)
    if axis.model != "log10" or len(ticks) < 3:
        raise RuntimeError(f"{name}: OCR-corrected time token lacks a log-position sequence")
    for index, tick in enumerate(ticks):
        if tick.pixel not in corrected_pixels:
            continue
        if index == 0 or index == len(ticks) - 1:
            raise RuntimeError(f"{name}: OCR-corrected time token is not sequence-bracketed")
        left, right = ticks[index - 1], ticks[index + 1]
        coordinates = np.log10([left.value, tick.value, right.value])
        predicted = left.pixel + (right.pixel - left.pixel) * (
            (coordinates[1] - coordinates[0]) / (coordinates[2] - coordinates[0])
        )
        if abs(predicted - tick.pixel) > 1.5:
            raise RuntimeError(f"{name}: OCR-corrected time token fails log-position sequence")


def axis_to_json(axis: NumericAxis) -> dict[str, object]:
    return {
        "model": axis.model,
        "m": axis.m,
        "b": axis.b,
        "residual_px": axis.residual_px,
        "candidate_residuals_px": dict(axis.candidate_residuals_px),
        "ticks": [_tick_to_json(tick) for tick in axis.ticks],
    }


def _tick_to_json(tick: AxisTick) -> dict[str, object]:
    payload: dict[str, object] = {"text": tick.text, "value": tick.value, "pixel": tick.pixel}
    if tick.normalized_text is not None:
        payload["normalized_text"] = tick.normalized_text
    return payload
