"""Generic position-based calibration for numeric linear or log chart axes."""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

from .capacitance_types import PlotBox

_NUMBER_RE = re.compile(r"^[+-]?\d+(?:\.\d+)?$")
_EXPLICIT_POWER_RE = re.compile(r"^10\^([+-]?\d+)$")


@dataclass(frozen=True)
class AxisTick:
    text: str
    value: float
    pixel: float


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


def fit_numeric_axis(labels: list[tuple[str, float]], name: str = "axis") -> NumericAxis:
    """Fit a linear/log axis from label text and pixel positions.

    At least two labels are required.  Two positive labels cannot distinguish
    linear from logarithmic spacing and therefore refuse unless one label is
    zero (which rules out log).  Superscript-aware PDF extraction supplies
    powers as ``10^n``. Raw strings such as ``100,101,...`` remain ordinary
    numbers because they could be a genuine narrow linear axis.
    """
    parsed = _parse_labels(labels, name)
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
        x0=int(round(_nearby_edge(xs[0], hint.x0, xs))),
        y0=int(round(_nearby_edge(ys[0], hint.y0, ys))),
        x1=int(round(_nearby_edge(xs[-1], hint.x1, xs))),
        y1=int(round(_nearby_edge(ys[-1], hint.y1, ys))),
    )


def _nearby_edge(tick_edge: float, hint_edge: float, positions: list[float]) -> float:
    steps = np.diff(np.asarray(positions, dtype=float))
    tolerance = max(4.0, 0.18 * float(np.median(steps)))
    return float(hint_edge) if abs(hint_edge - tick_edge) <= tolerance else tick_edge


def _parse_labels(labels: list[tuple[str, float]], name: str) -> list[AxisTick]:
    cleaned = [(text.strip().replace("−", "-"), float(pixel)) for text, pixel in labels]
    numeric = [
        (text, pixel)
        for text, pixel in cleaned
        if _NUMBER_RE.fullmatch(text) or _EXPLICIT_POWER_RE.fullmatch(text)
    ]
    ticks = []
    for text, pixel in numeric:
        explicit = _EXPLICIT_POWER_RE.fullmatch(text)
        if explicit is not None:
            value = 10.0 ** int(explicit.group(1))
        else:
            value = float(text)
        ticks.append(AxisTick(text, value, pixel))
    if len(ticks) != len(labels):
        raise RuntimeError(f"{name}: non-numeric tick label in selected run")
    return ticks


def axis_to_json(axis: NumericAxis) -> dict[str, object]:
    return {
        "model": axis.model,
        "m": axis.m,
        "b": axis.b,
        "residual_px": axis.residual_px,
        "candidate_residuals_px": dict(axis.candidate_residuals_px),
        "ticks": [
            {"text": tick.text, "value": tick.value, "pixel": tick.pixel}
            for tick in axis.ticks
        ],
    }
