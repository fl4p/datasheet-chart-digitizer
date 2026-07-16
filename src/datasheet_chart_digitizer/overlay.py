"""Shared QA-overlay primitives for the datasheet-digitizer plugins.

Every chart plugin renders a QA overlay: the plot frame, the digitized curves,
and a crosshair + value label at each calibration tick so a human can spot a
mis-picked or mis-scaled tick at a glance. That frame + tick-drawing loop was
copy-pasted across breakdown_voltage, transfer_characteristics, rdson_temperature
and diode_forward_voltage, differing only in color, font size, unit suffix and
label offset. This is the ONE implementation.

Callers pass already-resolved ``(pixel, value)`` tick pairs, so this stays
agnostic to how each plugin calibrates its axes: ``numeric_axis.AxisTick`` carries
``.pixel``/``.value`` directly, while the capacitance pipeline resolves a pixel
from a calibration function. ``plot`` is any object exposing integer-ish
``x0/y0/x1/y1`` (PlotBox and fitz-style rects both qualify).

Note: ``capacitance_overlay.draw_axis_debug_overlay`` intentionally draws a RICHER
variant (full gridlines across the plot + log-decade labels), so it is not folded
in here; this module covers the plain crosshair-tick overlay the other plugins share.
"""

from __future__ import annotations

from typing import Iterable

import cv2
import numpy as np

_FONT = cv2.FONT_HERSHEY_SIMPLEX


def draw_plot_frame(
    image: np.ndarray,
    plot,
    color: tuple[int, int, int] = (0, 190, 0),
    thickness: int = 2,
) -> np.ndarray:
    """Outline the plot rectangle."""
    cv2.rectangle(
        image,
        (int(plot.x0), int(plot.y0)),
        (int(plot.x1), int(plot.y1)),
        color,
        thickness,
    )
    return image


def draw_axis_ticks(
    image: np.ndarray,
    plot,
    x_ticks: Iterable[tuple[float, float]] = (),
    y_ticks: Iterable[tuple[float, float]] = (),
    *,
    color: tuple[int, int, int] = (255, 0, 0),
    font_scale: float = 0.23,
    thickness: int = 1,
    marker_size: int = 5,
    unit_x: str = "",
    unit_y: str = "",
) -> np.ndarray:
    """Crosshair marker + ``{value:g}{unit}`` label at each calibration tick.

    ``x_ticks`` are drawn along the bottom axis (at ``plot.y1``), ``y_ticks`` along
    the left axis (at ``plot.x0``); each is an iterable of ``(pixel, value)``.
    Labels are clamped to stay inside the plot span. The X ticks are rendered
    before the Y ticks (callers that assert marker order rely on this)."""
    x0, y0, y1 = int(plot.x0), int(plot.y0), int(plot.y1)
    for pixel, value in x_ticks:
        x = int(round(pixel))
        cv2.drawMarker(image, (x, y1), color, cv2.MARKER_CROSS, marker_size, thickness)
        cv2.putText(
            image,
            f"{value:g}{unit_x}",
            (max(x0 + 1, x - 13), y1 - 3),
            _FONT,
            font_scale,
            color,
            thickness,
            cv2.LINE_AA,
        )
    for pixel, value in y_ticks:
        y = int(round(pixel))
        cv2.drawMarker(image, (x0, y), color, cv2.MARKER_CROSS, marker_size, thickness)
        cv2.putText(
            image,
            f"{value:g}{unit_y}",
            (x0 + 3, min(y1 - 2, max(y0 + 9, y + 3))),
            _FONT,
            font_scale,
            color,
            thickness,
            cv2.LINE_AA,
        )
    return image
