"""Digitize Hornresp "SPL Response (dB)" screenshots (raster, log-x / linear-y).

New chart category for this library: the input is a raster screenshot of a
Hornresp SPL export, not a vector PDF page, so `axis_calibration.calibrate_axes`
(which needs a PyMuPDF page) does not apply. Frame and gridlines are recovered
from the bitmap instead, then validated against the expected log-decade spacing.

The traced curve is returned as a per-column envelope (min/max/mid) because a
resonant horn response contains near-vertical segments at deep nulls, where a
single-valued reading is genuinely ambiguous.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np


@dataclass
class Frame:
    x0: int
    x1: int
    y0: int
    y1: int


@dataclass
class SplCalibration:
    frame: Frame
    f_left: float
    f_right: float
    db_top: float
    db_bottom: float

    def freq(self, x: float) -> float:
        t = (x - self.frame.x0) / (self.frame.x1 - self.frame.x0)
        return self.f_left * (self.f_right / self.f_left) ** t

    def db(self, y: float) -> float:
        t = (y - self.frame.y0) / (self.frame.y1 - self.frame.y0)
        return self.db_top + t * (self.db_bottom - self.db_top)

    def x_of(self, f: float) -> float:
        t = math.log10(f / self.f_left) / math.log10(self.f_right / self.f_left)
        return self.frame.x0 + t * (self.frame.x1 - self.frame.x0)


def detect_frame(gray: np.ndarray, search_h: int, dark: int = 128) -> Frame:
    """Locate the axis box as the outermost long dark rules."""
    panel = gray[:search_h]
    d = panel < dark
    h = panel.shape[0]
    cols = d[: int(0.95 * h)].sum(0)
    vlines = np.where(cols > 0.6 * h)[0]
    if len(vlines) < 2:
        raise ValueError("could not find left/right frame rules")
    x0, x1 = int(vlines.min()), int(vlines.max())
    inner = d[:, x0 : x1 + 1]
    w = inner.shape[1]
    rows = inner.sum(1)
    hlines = np.where(rows > 0.7 * w)[0]
    if len(hlines) < 2:
        raise ValueError("could not find top/bottom frame rules")
    return Frame(x0, x1, int(hlines.min()), int(hlines.max()))


def validate_log_axis(gray: np.ndarray, cal: SplCalibration, grey: int = 230,
                      tol_px: float = 3.0) -> list[tuple[float, float, float]]:
    """Check detected vertical gridlines against expected decade positions.

    Returns (decade_hz, expected_x, nearest_detected_x). Raises if a decade
    gridline is missing or misplaced beyond `tol_px`.
    """
    f = cal.frame
    inner = gray[f.y0 + 2 : f.y1 - 1, f.x0 : f.x1 + 1]
    h = inner.shape[0]
    cols = (inner < grey).sum(0)
    detected = np.where(cols > 0.9 * h)[0] + f.x0
    out = []
    d = cal.f_left
    while d <= cal.f_right:
        if d > cal.f_left:
            ex = cal.x_of(d)
            if len(detected) == 0:
                raise ValueError("no vertical gridlines detected")
            near = float(detected[np.argmin(np.abs(detected - ex))])
            if abs(near - ex) > tol_px:
                raise ValueError(
                    f"decade {d:g} Hz expected at x={ex:.1f} but nearest gridline "
                    f"is x={near:.1f} ({abs(near-ex):.1f} px off)"
                )
            out.append((d, ex, near))
        d *= 10
    return out


def trace(gray: np.ndarray, cal: SplCalibration, dark: int = 100
          ) -> list[tuple[float, float, float, float]]:
    """Trace the black curve. Returns (freq, db_mid, db_hi, db_lo) per column."""
    f = cal.frame
    y0, y1 = f.y0 + 2, f.y1 - 2
    band = gray[y0:y1, f.x0 + 2 : f.x1 - 1] < dark
    pts = []
    for i in range(band.shape[1]):
        ys = np.where(band[:, i])[0]
        if len(ys) == 0:
            continue
        x = f.x0 + 2 + i
        top, bot = int(ys.min()), int(ys.max())
        mid = float(np.median(ys))
        pts.append((cal.freq(x), cal.db(y0 + mid), cal.db(y0 + top), cal.db(y0 + bot)))
    return pts
