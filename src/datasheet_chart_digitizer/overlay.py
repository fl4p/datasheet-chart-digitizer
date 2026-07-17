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
    font_scale: float = 0.35,
    thickness: int = 1,
    marker_size: int = 8,
    unit_x: str = "",
    unit_y: str = "",
    line_aa: bool = False,
    halo: bool = False,
) -> np.ndarray:
    """Crosshair marker + ``{value:g}{unit}`` label at each calibration tick.

    ``x_ticks`` are drawn along the bottom axis (at ``plot.y1``) with the label
    centered under the tick just inside the axis; ``y_ticks`` along the left axis
    (at ``plot.x0``); each is an iterable of ``(pixel, value)``. Labels are clamped
    to stay inside the plot span. The X ticks are rendered before the Y ticks
    (callers that assert marker order rely on this).

    ``line_aa`` anti-aliases the crosshair markers; ``halo`` draws a white outline
    behind each label so it stays legible over dark traces (diode overlays). Color,
    marker size, font scale and unit suffixes cover the per-plugin variation."""
    x0, y0, x1, y1 = int(plot.x0), int(plot.y0), int(plot.x1), int(plot.y1)
    marker_args = (marker_size, thickness) + ((cv2.LINE_AA,) if line_aa else ())

    def _label(text: str, org: tuple[int, int]) -> None:
        if halo:
            cv2.putText(image, text, org, _FONT, font_scale, (255, 255, 255),
                        thickness + 2, cv2.LINE_AA)
        cv2.putText(image, text, org, _FONT, font_scale, color, thickness, cv2.LINE_AA)

    for pixel, value in x_ticks:
        x = int(round(pixel))
        cv2.drawMarker(image, (x, y1), color, cv2.MARKER_CROSS, *marker_args)
        text = f"{value:g}{unit_x}"
        width = cv2.getTextSize(text, _FONT, font_scale, thickness)[0][0]
        _label(text, (min(max(x0 + 2, x - width // 2), x1 - width), y1 - 5))
    for pixel, value in y_ticks:
        y = int(round(pixel))
        cv2.drawMarker(image, (x0, y), color, cv2.MARKER_CROSS, *marker_args)
        _label(f"{value:g}{unit_y}", (x0 + 5, min(y1 - 3, max(y0 + 12, y + 4))))
    return image
