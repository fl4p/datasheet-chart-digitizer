"""Never serve a gate-charge trace that runs between two source strokes.

IPB180N04S4 p7 Figure 15 draws two thick (1.92 pt) VGS(Qg) curves, VDD = 8 V
and 32 V, that share the initial ramp. The vector tracer joins them into one
component and reduces each pixel column to its median y; with two curves the
median is their average, a line on neither curve. It was served ``ok``.

This check measures the served curve against the source strokes themselves:
every served point inside the plot should lie on some dark stroke centreline
(within half the stroke width plus a small margin; a raster trace that rides a
thick stroke's upper edge is still on it). When the panel's curves are vector
strokes (most served points lie on one) and a material fraction of the served
points lies on none, the served curve is a blend.

The caller runs it only when the vector gate tracer found a curve in the
plot (EPC2023 / EPC2934C are raster curves whose only dark vector strokes are
frame-corner fragments). It also returns None when the plot holds no dark
vector strokes that could be a curve (they must span >= 30 % of the plot width and
>= 20 % of its height, the vector tracer's own component gate). That is "not
applicable" -- a raster chart -- decided from the panel, never from how far
the served curve is from the strokes, so a worse trace can only score worse.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Served points farther than (half stroke width + this) from every stroke
# centreline are off-stroke.
OFF_STROKE_MARGIN_PX = 1.5
# Fraction of off-stroke points that makes the served curve a blend.
BLEND_FRACTION = 0.10
# The vector layer holds a curve only if its non-rule strokes span this much
# of the plot (the vector gate tracer's component gate).
APPLICABLE_X_SPAN = 0.30
APPLICABLE_Y_SPAN = 0.20
# Dark-stroke colour ceiling (same as the vector gate tracer).
DARK_COLOR_MAX = 0.45


@dataclass(frozen=True)
class BlendVerdict:
    off_fraction: float
    points: int
    strokes: int

    @property
    def blended(self) -> bool:
        return self.off_fraction >= BLEND_FRACTION


def _segments_px(page, rect, scale: float, plot_box) -> list[tuple[np.ndarray, np.ndarray, float]]:
    x0, y0, x1, y1 = plot_box
    plot_w = max(1.0, (x1 - x0) / scale)
    plot_h = max(1.0, (y1 - y0) / scale)
    segments: list[tuple[np.ndarray, np.ndarray, float]] = []
    for drawing in page.get_drawings():
        color = drawing.get("color")
        if color is None or max(color) > DARK_COLOR_MAX:
            continue
        half_width_px = 0.5 * float(drawing.get("width") or 0.0) * scale
        for item in drawing.get("items", []):
            if item[0] == "l":
                pts = [(item[1].x, item[1].y), (item[2].x, item[2].y)]
            elif item[0] == "c":
                p = [(pt.x, pt.y) for pt in item[1:5]]
                pts = []
                for step in range(9):
                    t = step / 8.0
                    u = 1.0 - t
                    pts.append((
                        u**3 * p[0][0] + 3 * u * u * t * p[1][0] + 3 * u * t * t * p[2][0] + t**3 * p[3][0],
                        u**3 * p[0][1] + 3 * u * u * t * p[1][1] + 3 * u * t * t * p[2][1] + t**3 * p[3][1],
                    ))
            else:
                continue
            for (ax, ay), (bx, by) in zip(pts, pts[1:]):
                dx, dy = abs(bx - ax), abs(by - ay)
                # frame and gridlines: long axis-aligned rules
                if dy < 0.8 and dx > 0.55 * plot_w:
                    continue
                if dx < 0.8 and dy > 0.55 * plot_h:
                    continue
                a = np.array([(ax - rect.x0) * scale, (ay - rect.y0) * scale])
                b = np.array([(bx - rect.x0) * scale, (by - rect.y0) * scale])
                if max(a[0], b[0]) < x0 - 2 or min(a[0], b[0]) > x1 + 2:
                    continue
                if max(a[1], b[1]) < y0 - 2 or min(a[1], b[1]) > y1 + 2:
                    continue
                segments.append((a, b, half_width_px))
    return segments


def served_curve_blend(page, rect, scale: float, plot_box, curve) -> BlendVerdict | None:
    """Fraction of served points that lie on no source stroke, or None if N/A."""
    x0, y0, x1, y1 = plot_box
    points = np.array(
        [(x, y) for x, y in curve if x0 <= x <= x1 and y0 <= y <= y1], dtype=float
    )
    if len(points) < 20:
        return None
    segments = _segments_px(page, rect, scale, plot_box)
    if len(segments) < 2:
        return None
    ends = np.array([point for segment in segments for point in segment[:2]])
    if (
        np.ptp(ends[:, 0]) < APPLICABLE_X_SPAN * (x1 - x0)
        or np.ptp(ends[:, 1]) < APPLICABLE_Y_SPAN * (y1 - y0)
    ):
        return None
    a = np.array([s[0] for s in segments])
    b = np.array([s[1] for s in segments])
    half = np.array([s[2] for s in segments])
    d = b - a
    length2 = np.maximum((d**2).sum(axis=1), 1e-12)
    rel = points[:, None, :] - a[None, :, :]
    t = np.clip((rel * d[None, :, :]).sum(axis=2) / length2[None, :], 0.0, 1.0)
    nearest = a[None, :, :] + t[:, :, None] * d[None, :, :]
    distance = np.sqrt(((points[:, None, :] - nearest) ** 2).sum(axis=2))
    on = (distance <= half[None, :] + OFF_STROKE_MARGIN_PX).any(axis=1)
    on_fraction = float(on.mean())
    return BlendVerdict(1.0 - on_fraction, len(points), len(segments))


BLEND_DIAGNOSTIC = "served_trace_blends_source_strokes"
