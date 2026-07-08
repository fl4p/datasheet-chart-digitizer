"""Position-based axis calibration for vector datasheet C(V) charts.

Unlike a text-ORDER calibration (map normalized pixel extent linearly to
[min_tick, max_tick]), this associates each tick label with its actual pixel
POSITION and fits linear-X / log-Y on the (value, position) pairs. It is
therefore robust whether or not the detected plot frame coincides with the
extreme tick gridlines -- the text-order approach is correct only under that
(often-untrue) alignment assumption.

Tick labels on Infineon vector pages are exact text objects (decades render as
'10' + a unicode superscript), so no OCR is needed. The fit residuals are
returned so a bad calibration self-flags.

The caller supplies chart-local text bands so a multi-chart datasheet page can
be calibrated without OCR or hardcoded datasheet-specific geometry.
"""
from __future__ import annotations
import re
from dataclasses import dataclass
import numpy as np

_SUP = str.maketrans("\u2070\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079", "0123456789")


@dataclass
class Calibration:
    mx: float; bx: float          # V = mx*px_x + bx           (linear VDS)
    my: float; by: float          # log10(C) = my*px_y + by    (log C[pF])
    x_ticks: tuple                # ((V, px_x), ...)
    y_decades: tuple              # ((exponent, px_y), ...)
    x_resid: float                # RMS fit residual, volts
    y_resid: float                # RMS fit residual, decades

    def v_of_x(self, px):  return self.mx * px + self.bx
    def x_of_v(self, v):   return (v - self.bx) / self.mx
    def c_of_y(self, py):  return 10.0 ** (self.my * py + self.by)
    def y_of_c(self, c):   return (np.log10(c) - self.by) / self.my


def calibrate_axes(page, x_row_band, y_label_x_band, plot_y_band):
    """Fit calibration from tick-label text positions on a PyMuPDF page.

    x_row_band   : (y0, y1) pixel band containing the X (VDS) tick-number row
    y_label_x_band: (x0, x1) pixel band containing the left Y decade labels
    plot_y_band  : (y0, y1) pixel span over which Y decade labels may appear
    Bands isolate one chart on a multi-chart page; get them from the plot bbox.
    """
    xt, yd = [], []
    for w in page.get_text("words"):
        cx, cy = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
        t = w[4].strip()
        if x_row_band[0] < cy < x_row_band[1] and t.isdigit():
            xt.append((float(t), cx))
        tt = t.translate(_SUP)
        if (y_label_x_band[0] < cx < y_label_x_band[1]
                and plot_y_band[0] < cy < plot_y_band[1]
                and re.fullmatch(r"10\d+", tt)):
            yd.append((float(tt[2:]), cy))
    if len(xt) < 2:
        raise RuntimeError("need >=2 X tick labels for a position fit")
    if len(yd) < 2:
        raise RuntimeError("need >=2 Y decade labels for a position fit")
    xt.sort(); yd.sort(key=lambda z: z[1])
    mx, bx = np.polyfit([px for _, px in xt], [v for v, _ in xt], 1)
    my, by = np.polyfit([py for _, py in yd], [e for e, _ in yd], 1)
    x_resid = float(np.sqrt(np.mean([(mx * px + bx - v) ** 2 for v, px in xt])))
    y_resid = float(np.sqrt(np.mean([(my * py + by - e) ** 2 for e, py in yd])))
    return Calibration(mx, bx, my, by, tuple(xt), tuple(yd), x_resid, y_resid)


if __name__ == "__main__":
    raise SystemExit("axis_calibration is a library module; use the package CLI to digitize charts")
