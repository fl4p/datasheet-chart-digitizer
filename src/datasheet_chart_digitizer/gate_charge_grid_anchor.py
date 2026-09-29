"""Seat gate-charge VGS ticks on the gridlines their labels name.

The Vpl scalar is read through a straight line fitted to the VGS tick-LABEL
centres (``gate_charge_estimation._v_from_local_ticks`` on word-box centres,
and ``gate_axis_ocr.recalibrate_gate_result`` on OCR boxes). A label is set
beside its rule, not on it, so that fit is biased by the typography. Here the
labels only identify values: ``gridline_anchor.anchor_axis_on_grid`` binds each
to its observed rule (or frame tick mark) and the ticks are re-expressed at the
rule, then ``check_served_on_grid`` checks the mapping that will be SERVED.

Pixels here are continuous crop pixels (``(pt - crop.y0) * scale``, the
convention of the gate-charge ticks, plot box and ``vpl_y_px``);
gridline_anchor works in pixel-index coordinates (index = continuous - 0.5).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .gridline_anchor import (
    GridCheck,
    anchor_axis_on_grid_attempts,
    check_served_on_grid_attempts,
)
from .numeric_axis import AxisTick, NumericAxis, fit_axis_ticks

_INDEX_TO_CONTINUOUS = 0.5


@dataclass(frozen=True)
class _Frame:
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass(frozen=True)
class GateYGrid:
    """Served VGS ticks (value, continuous px) plus the grid evidence."""

    ticks_px: tuple[tuple[float, float], ...]
    anchoring: dict | None
    anchor_attempt: str | None
    anchor_error: str | None
    check: GridCheck

    def payload(self) -> dict[str, object]:
        return {
            "anchoring": self.anchoring,
            "anchor_attempt": self.anchor_attempt,
            "anchor_error": self.anchor_error,
            "grid_check": self.check.payload(),
        }


def _index_ticks(ticks_px) -> list[AxisTick]:
    return [
        AxisTick(f"{value:g}", float(value), float(px) - _INDEX_TO_CONTINUOUS)
        for value, px in ticks_px
    ]


def _served_index_axis(ticks_px) -> NumericAxis:
    """The straight line the caller serves (value vs continuous px), in index px."""
    values = np.asarray([v for v, _ in ticks_px], dtype=float)
    pixels = np.asarray([p for _, p in ticks_px], dtype=float)
    m, b = np.polyfit(pixels, values, 1)
    return NumericAxis("linear", float(m), float(b + m * _INDEX_TO_CONTINUOUS), (), 0.0, ())


def seat_gate_y_ticks(
    gray: np.ndarray,
    label_ticks_px: list[tuple[float, float]] | tuple[tuple[float, float], ...],
    plot_box: tuple[int, int, int, int],
) -> GateYGrid:
    """Anchor labelled VGS ticks on their rules; check the served mapping.

    *label_ticks_px* are (value, continuous crop px of the label centre). If
    no rule binding is evidenced the label ticks are returned unchanged and
    the check says why (``unverified``) -- the caller must not report ok.
    """
    x0, y0, x1, y1 = (float(v) for v in plot_box)
    frame = _Frame(x0, y0, x1, y1)
    cross = (x0 - _INDEX_TO_CONTINUOUS, x1 - _INDEX_TO_CONTINUOUS)
    label_ticks = _index_ticks(label_ticks_px)
    served_ticks = tuple((float(v), float(p)) for v, p in label_ticks_px)
    anchoring = attempt_name = anchor_error = None
    try:
        label_axis = fit_axis_ticks(label_ticks, "VGS", model="linear")
    except RuntimeError as exc:
        label_axis = None
        anchor_error = str(exc)
    if label_axis is not None:
        try:
            anchored, attempt_name = anchor_axis_on_grid_attempts(
                gray, label_axis, frame=frame, orientation="y", cross_span=cross, name="VGS"
            )
        except RuntimeError as exc:
            anchor_error = str(exc)
        else:
            served_ticks = tuple(
                (a.value, a.line_px + _INDEX_TO_CONTINUOUS) for a in anchored.anchors
            )
            anchoring = anchored.payload()
            anchoring["pixel_convention"] = "index (add 0.5 for continuous crop px)"
    check = check_gate_y_on_grid(gray, served_ticks, label_ticks_px, plot_box)
    return GateYGrid(served_ticks, anchoring, attempt_name, anchor_error, check)


def check_gate_y_on_grid(
    gray: np.ndarray,
    served_ticks_px,
    label_ticks_px,
    plot_box: tuple[int, int, int, int],
) -> GridCheck:
    """The guard: the served VGS line against the rule each label names."""
    if len(served_ticks_px) < 2 or len(label_ticks_px) < 2:
        return GridCheck("unverified", "VGS: fewer than 2 ticks to check", None, ())
    x0, y0, x1, y1 = (float(v) for v in plot_box)
    frame = _Frame(x0, y0, x1, y1)
    cross = (x0 - _INDEX_TO_CONTINUOUS, x1 - _INDEX_TO_CONTINUOUS)
    served = _served_index_axis(served_ticks_px)
    labels = _index_ticks(label_ticks_px)
    return check_served_on_grid_attempts(
        gray, served, labels, frame=frame, orientation="y", cross_span=cross, name="VGS"
    )


def served_value(ticks_px, y_px: float) -> float:
    """VGS at continuous crop pixel *y_px* through the served ticks."""
    values = np.asarray([v for v, _ in ticks_px], dtype=float)
    pixels = np.asarray([p for _, p in ticks_px], dtype=float)
    m, b = np.polyfit(pixels, values, 1)
    return float(m * y_px + b)
