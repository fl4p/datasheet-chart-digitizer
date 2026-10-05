"""Seat gate-charge VGS and Qg ticks on the gridlines their labels name.

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
    Orientation,
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
class GateAxisGrid:
    """Served ticks (value, continuous px) of one axis plus the grid evidence."""

    ticks_px: tuple[tuple[float, float], ...]
    anchoring: dict | None
    anchor_attempt: str | None
    anchor_error: str | None
    check: GridCheck

    @property
    def anchored(self) -> bool:
        """The served ticks are the rules the labels name (not label glyphs)."""
        return self.anchoring is not None

    def payload(self) -> dict[str, object]:
        return {
            "anchoring": self.anchoring,
            "anchor_attempt": self.anchor_attempt,
            "anchor_error": self.anchor_error,
            "grid_check": self.check.payload(),
        }


# Kept for callers that name the VGS axis explicitly.
GateYGrid = GateAxisGrid


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


def _frame_and_cross(plot_box, orientation: Orientation):
    x0, y0, x1, y1 = (float(v) for v in plot_box)
    frame = _Frame(x0, y0, x1, y1)
    lo, hi = (y0, y1) if orientation == "x" else (x0, x1)
    return frame, (lo - _INDEX_TO_CONTINUOUS, hi - _INDEX_TO_CONTINUOUS)


def seat_gate_ticks(
    gray: np.ndarray,
    label_ticks_px: list[tuple[float, float]] | tuple[tuple[float, float], ...],
    plot_box: tuple[int, int, int, int],
    *,
    orientation: Orientation,
    name: str,

) -> GateAxisGrid:
    """Anchor one axis's labelled ticks on their rules; check the served mapping.

    *label_ticks_px* are (value, continuous crop px of the label centre);
    *orientation* ``"y"`` is the VGS axis (horizontal rules), ``"x"`` the Qg
    axis (vertical rules). If no rule binding is evidenced the label ticks are
    returned unchanged and the check says why (``unverified``) -- the caller
    must not serve anything read through that axis as verified.
    """
    frame, cross = _frame_and_cross(plot_box, orientation)
    label_ticks = _index_ticks(label_ticks_px)
    served_ticks = tuple((float(v), float(p)) for v, p in label_ticks_px)
    anchoring = attempt_name = anchor_error = None
    try:
        label_axis = fit_axis_ticks(label_ticks, name, model="linear")
    except RuntimeError as exc:
        label_axis = None
        anchor_error = str(exc)
    if label_axis is not None:
        try:
            anchored, attempt_name = anchor_axis_on_grid_attempts(
                gray, label_axis, frame=frame, orientation=orientation, cross_span=cross, name=name
            )
        except RuntimeError as exc:
            anchor_error = str(exc)
        else:
            served_ticks = tuple(
                (a.value, a.line_px + _INDEX_TO_CONTINUOUS) for a in anchored.anchors
            )
            anchoring = anchored.payload()
            anchoring["pixel_convention"] = "index (add 0.5 for continuous crop px)"
    check = check_gate_on_grid(
        gray, served_ticks, label_ticks_px, plot_box, orientation=orientation, name=name
    )
    return GateAxisGrid(served_ticks, anchoring, attempt_name, anchor_error, check)


def seat_gate_y_ticks(gray, label_ticks_px, plot_box) -> GateAxisGrid:
    """VGS: ``seat_gate_ticks`` on the horizontal rules."""
    return seat_gate_ticks(gray, label_ticks_px, plot_box, orientation="y", name="VGS")


def seat_gate_x_ticks(gray, label_ticks_px, plot_box) -> GateAxisGrid:
    """Qg: ``seat_gate_ticks`` on the vertical rules (or bottom-frame tick marks)."""
    return seat_gate_ticks(gray, label_ticks_px, plot_box, orientation="x", name="Qg")


def check_gate_on_grid(
    gray: np.ndarray,
    served_ticks_px,
    label_ticks_px,
    plot_box: tuple[int, int, int, int],
    *,
    orientation: Orientation,
    name: str,
) -> GridCheck:
    """The guard: the SERVED line of one axis against the rule each label names.

    *served_ticks_px* is what the caller serves values through (after every
    transform); *label_ticks_px* only identify which rule each value names.
    """
    if len(served_ticks_px) < 2 or len(label_ticks_px) < 2:
        return GridCheck("unverified", f"{name}: fewer than 2 ticks to check", None, ())
    served_px = [float(p) for _v, p in served_ticks_px]
    if len({round(p, 6) for p in served_px}) < 2:
        return GridCheck("unverified", f"{name}: served ticks collapse to one pixel", None, ())
    frame, cross = _frame_and_cross(plot_box, orientation)
    served = _served_index_axis(served_ticks_px)
    labels = _index_ticks(label_ticks_px)
    return check_served_on_grid_attempts(
        gray, served, labels, frame=frame, orientation=orientation, cross_span=cross, name=name
    )


def check_gate_y_on_grid(gray, served_ticks_px, label_ticks_px, plot_box) -> GridCheck:
    """The guard: the served VGS line against the rule each label names."""
    return check_gate_on_grid(
        gray, served_ticks_px, label_ticks_px, plot_box, orientation="y", name="VGS"
    )


def check_gate_x_on_grid(gray, served_ticks_px, label_ticks_px, plot_box) -> GridCheck:
    """The guard: the served Qg line against the rule each label names."""
    return check_gate_on_grid(
        gray, served_ticks_px, label_ticks_px, plot_box, orientation="x", name="Qg"
    )


def served_value(ticks_px, y_px: float) -> float:
    """VGS at continuous crop pixel *y_px* through the served ticks."""
    values = np.asarray([v for v, _ in ticks_px], dtype=float)
    pixels = np.asarray([p for _, p in ticks_px], dtype=float)
    m, b = np.polyfit(pixels, values, 1)
    return float(m * y_px + b)
