"""Position-based axis calibration for vector datasheet C(V) charts.

Unlike a text-ORDER calibration (map normalized pixel extent linearly to
[min_tick, max_tick]), this associates each tick label with its actual pixel
POSITION and fits linear-X / log-Y on the (value, position) pairs. It is
therefore robust whether or not the detected plot frame coincides with the
extreme tick gridlines -- the text-order approach is correct only under that
(often-untrue) alignment assumption.

Tick labels on Infineon vector pages are exact text objects (decades render as
'10' + a unicode superscript), so no OCR is needed. Toshiba/TI pages write
plain decade numbers ('100000') instead, and Toshiba capacitance charts use a
LOG X axis with decimal decade ticks ('0.1 1 10 100'); both are handled here.
The fit residuals are returned so a bad calibration self-flags (x_resid is in
volts for a linear X axis and in DECADES when x_log is set).

The caller supplies chart-local text bands so a multi-chart datasheet page can
be calibrated without OCR or hardcoded datasheet-specific geometry. Pass
x_col_band to restrict X tick candidates to the plot's horizontal span --
required on two-charts-per-row pages where both tick rows share the y band.
"""
from __future__ import annotations
import math
import re
from dataclasses import dataclass
import numpy as np

_SUP = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")
_X_TICK_RE = re.compile(r"\d+(?:\.\d+)?")


@dataclass
class Calibration:
    mx: float; bx: float          # V = mx*px_x + bx; log10(V) = mx*px_x + bx when x_log
    my: float; by: float          # log10(C) = my*px_y + by    (log C[pF])
    x_ticks: tuple                # ((V, px_x), ...)
    y_decades: tuple              # ((exponent, px_y), ...)
    x_resid: float                # RMS fit residual: volts, or decades when x_log
    y_resid: float                # RMS fit residual, decades
    x_log: bool = False           # X axis is logarithmic (decade-spaced ticks)

    def v_of_x(self, px):  return 10.0 ** (self.mx * px + self.bx) if self.x_log else self.mx * px + self.bx
    def x_of_v(self, v):   return ((np.log10(v) if self.x_log else v) - self.bx) / self.mx
    def c_of_y(self, py):  return 10.0 ** (self.my * py + self.by)
    def y_of_c(self, c):   return (np.log10(c) - self.by) / self.my


def _is_plain_decade(value: float) -> bool:
    """True for 1, 10, 100, ... (plain-number decade labels used by TI/Toshiba)."""
    if value < 1.0:
        return False
    exponent = math.log10(value)
    return abs(exponent - round(exponent)) < 1e-9


def _x_ticks_look_log(values: list[float]) -> bool:
    """Decade-spaced positive ticks (0.1, 1, 10, 100) mark a log X axis."""
    if len(values) < 3 or min(values) <= 0.0:
        return False
    ordered = sorted(values)
    ratios = [hi / lo for lo, hi in zip(ordered, ordered[1:]) if lo > 0.0]
    return bool(ratios) and all(abs(r - 10.0) < 0.5 for r in ratios)


def _log_fit_beats_linear(xt: list[tuple[float, float]]) -> bool:
    """Dual-fit fallback for log X axes with sub-decade ticks (1-2-5-10-20...).

    `_x_ticks_look_log` only recognizes pure decade labels; the common 1-2-5
    convention fails its ratio test, the linear fit then has a huge residual,
    and the calibration is rejected. Fit both models on the (value, position)
    pairs and call the axis log only when the log fit is near-exact AND the
    linear fit is clearly worse -- a genuinely linear axis never satisfies
    both. Gated to all-positive ticks spanning >=1.5 decades, which no real
    linear voltage axis in this corpus does (they start at 0).
    """
    values = [v for v, _ in xt]
    if len(values) < 3 or min(values) <= 0.0 or max(values) / min(values) < 30.0:
        return False
    px = np.array([p for _, p in xt], dtype=float)
    lin_v = np.array(values, dtype=float)
    log_v = np.log10(lin_v)
    lin_fit = np.polyfit(px, lin_v, 1)
    log_fit = np.polyfit(px, log_v, 1)
    lin_rel = float(np.sqrt(np.mean((np.polyval(lin_fit, px) - lin_v) ** 2))) / max(1e-12, float(np.ptp(lin_v)))
    log_rel = float(np.sqrt(np.mean((np.polyval(log_fit, px) - log_v) ** 2))) / max(1e-12, float(np.ptp(log_v)))
    return log_rel < 0.02 and log_rel < 0.5 * lin_rel


def calibrate_axes(page, x_row_band, y_label_x_band, plot_y_band, x_col_band=None):
    """Fit calibration from tick-label text positions on a PyMuPDF page.

    x_row_band   : (y0, y1) pixel band containing the X (VDS) tick-number row
    y_label_x_band: (x0, x1) pixel band containing the left Y decade labels
    plot_y_band  : (y0, y1) pixel span over which Y decade labels may appear
    x_col_band   : optional (x0, x1) span for X tick candidates; keeps the
                   neighbor chart's tick row out on two-charts-per-row pages
    Bands isolate one chart on a multi-chart page; get them from the plot bbox.
    """
    xt, yd_sup, yd_plain = [], [], []
    for w in page.get_text("words"):
        cx, cy = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
        t = w[4].strip()
        if (
            x_row_band[0] < cy < x_row_band[1]
            and (x_col_band is None or x_col_band[0] < cx < x_col_band[1])
            and _X_TICK_RE.fullmatch(t)
        ):
            xt.append((float(t), cx))
        tt = t.translate(_SUP)
        if y_label_x_band[0] < cx < y_label_x_band[1] and plot_y_band[0] < cy < plot_y_band[1]:
            # Disambiguate arithmetically, not typographically: Infineon's
            # exponent digit extracts as a PLAIN digit in the same word, so
            # '10^4' reads as '104'. A power-of-ten token (10, 100, 1000...)
            # is a plain decade label (TI/Toshiba); a '10x' token that is NOT
            # a power of ten (102, 104, ...) is the exponent form. Both yield
            # correct exponents, so they can share one fit.
            if t.isdigit() and _is_plain_decade(float(t)):
                yd_plain.append((math.log10(float(t)), cy))
            elif re.fullmatch(r"10\d+", tt):
                yd_sup.append((float(tt[2:]), cy))
    yd = yd_sup + yd_plain
    if len(xt) < 2:
        raise RuntimeError("need >=2 X tick labels for a position fit")
    if len(yd) < 2:
        raise RuntimeError("need >=2 Y decade labels for a position fit")
    xt.sort(); yd.sort(key=lambda z: z[1])
    x_log = _x_ticks_look_log([v for v, _ in xt]) or _log_fit_beats_linear(xt)
    x_fit_vals = [math.log10(v) if x_log else v for v, _ in xt]
    mx, bx = np.polyfit([px for _, px in xt], x_fit_vals, 1)
    my, by = np.polyfit([py for _, py in yd], [e for e, _ in yd], 1)
    x_resid = float(np.sqrt(np.mean([(mx * px + bx - v) ** 2 for v, px in zip(x_fit_vals, [px for _, px in xt])])))
    y_resid = float(np.sqrt(np.mean([(my * py + by - e) ** 2 for e, py in yd])))
    return Calibration(mx, bx, my, by, tuple(xt), tuple(yd), x_resid, y_resid, x_log=x_log)


if __name__ == "__main__":
    raise SystemExit("axis_calibration is a library module; use the package CLI to digitize charts")
