"""Overlay rendering for MOSFET capacitance chart extraction."""

from __future__ import annotations

import cv2
import numpy as np

from .capacitance_axis import calibration_x_of_v, calibration_y_of_log_c
from .capacitance_types import AxisCalibration, PlotBox, TRACE_COLORS_BGR, Trace

def draw_trace_overlay(image: np.ndarray, plot: PlotBox, traces: list[Trace]) -> np.ndarray:
    overlay = image.copy()
    cv2.rectangle(overlay, (plot.x0, plot.y0), (plot.x1, plot.y1), (0, 180, 255), 2)

    for trace in traces:
        color = TRACE_COLORS_BGR[trace.name]
        pts = trace.points
        for a, b in zip(pts, pts[1:]):
            dx = abs(b[0] - a[0])
            dy = abs(b[1] - a[1])
            if dx <= max(8, int(plot.width * 0.06)) and dy <= max(60, int(plot.height * 0.18)):
                cv2.line(overlay, a, b, color, 3, lineType=cv2.LINE_AA)
        for point in pts[:: max(1, len(pts) // 80)]:
            cv2.circle(overlay, point, 2, color, -1, lineType=cv2.LINE_AA)

        label_at = pts[min(len(pts) - 1, max(0, int(len(pts) * 0.78)))]
        cv2.putText(
            overlay,
            trace.name,
            (label_at[0] + 5, max(18, label_at[1] - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            color,
            2,
            lineType=cv2.LINE_AA,
        )

    return overlay


def draw_axis_debug_overlay(
    image: np.ndarray,
    plot: PlotBox,
    calibration: AxisCalibration,
    title: str,
) -> np.ndarray:
    overlay = image.copy()
    cv2.rectangle(overlay, (plot.x0, plot.y0), (plot.x1, plot.y1), (0, 180, 255), 3)

    font = cv2.FONT_HERSHEY_SIMPLEX
    cv2.putText(
        overlay,
        title[:120],
        (max(5, plot.x0 - 90), max(26, plot.y0 - 42)),
        font,
        0.82,
        (0, 0, 0),
        3,
        lineType=cv2.LINE_AA,
    )
    subtitle1 = (
        f"axis={calibration.source} x={calibration.x_source or 'n/a'} "
        f"y={calibration.y_source or 'n/a'} "
        f"x_resid={_fmt_optional(calibration.x_resid_v)} "
        f"y_resid={_fmt_optional(calibration.y_resid_dec)}"
    )
    subtitle2 = (
        f"grid_n={calibration.y_grid_candidate_count if calibration.y_grid_candidate_count is not None else 'n/a'} "
        f"grid_span={_fmt_optional(calibration.y_grid_span_fraction)} "
        f"grid_resid_px={_fmt_optional(calibration.y_grid_residual_px)}"
    )
    cv2.putText(
        overlay,
        subtitle1[:140],
        (max(5, plot.x0 - 90), max(52, plot.y0 - 16)),
        font,
        0.62,
        (0, 0, 0),
        2,
        lineType=cv2.LINE_AA,
    )
    cv2.putText(
        overlay,
        subtitle2[:140],
        (max(5, plot.x0 - 90), max(78, plot.y0 + 10)),
        font,
        0.62,
        (0, 0, 0),
        2,
        lineType=cv2.LINE_AA,
    )

    for tick in calibration.x_ticks_v:
        x = int(round(calibration_x_of_v(calibration, plot, float(tick))))
        if plot.x0 - 3 <= x <= plot.x1 + 3:
            cv2.line(overlay, (x, plot.y0), (x, plot.y1), (255, 230, 0), 3, lineType=cv2.LINE_AA)
            cv2.circle(overlay, (x, plot.y1), 8, (255, 230, 0), -1, lineType=cv2.LINE_AA)
            cv2.putText(
                overlay,
                f"{tick:g}",
                (x - 16, min(image.shape[0] - 6, plot.y1 + 32)),
                font,
                0.70,
                (180, 125, 0),
                2,
                lineType=cv2.LINE_AA,
            )
    for exponent in calibration.y_decades:
        y = int(round(calibration_y_of_log_c(calibration, plot, float(exponent))))
        if plot.y0 - 3 <= y <= plot.y1 + 3:
            cv2.line(overlay, (plot.x0, y), (plot.x1, y), (255, 0, 255), 3, lineType=cv2.LINE_AA)
            cv2.circle(overlay, (plot.x0, y), 8, (255, 0, 255), -1, lineType=cv2.LINE_AA)
            cv2.putText(
                overlay,
                f"10^{int(round(exponent))}",
                (max(2, plot.x0 - 82), y + 8),
                font,
                0.70,
                (180, 0, 180),
                2,
                lineType=cv2.LINE_AA,
            )
    for y_raw in calibration.y_gridline_px:
        y = int(round(y_raw))
        if plot.y0 - 3 <= y <= plot.y1 + 3:
            cv2.line(overlay, (plot.x0, y), (plot.x1, y), (160, 0, 160), 1, lineType=cv2.LINE_AA)
            cv2.drawMarker(
                overlay,
                (plot.x0 + 14, y),
                (160, 0, 160),
                markerType=cv2.MARKER_TILTED_CROSS,
                markerSize=16,
                thickness=2,
                line_type=cv2.LINE_AA,
            )
    return overlay


def _fmt_optional(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.4g}"

