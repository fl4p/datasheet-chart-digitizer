"""Digitize powder-core "Core Loss Curves" panels (KDM / Micrometals / CSC style).

These panels plot core loss (mW/cm^3) against peak AC flux density (gauss) on a
log-log grid, with one straight curve per test frequency. The vendor almost always
prints the closed-form law next to the curves, e.g. KDM's Sendust panel:

    P = B^2.225 * (4.584 f + 0.0238 f^1.966)     P: mW/cm^3, B: kGauss, f: kHz

so the digitizer's job is NOT to recover a law nobody published. It is to answer a
narrower and much more useful question: **do the drawn curves actually agree with
the printed law, over the flux range they are drawn across?** A panel that does not
agree with its own formula is a panel whose curves carry information the formula
does not, and that changes which of the two you are entitled to extrapolate from.

Accordingly the flux exponent is ALWAYS fit free. Fitting it to the printed value
and reporting the agreement would be circular, and this module exists precisely
because that check was wanted.

Two properties of the chart class make it tractable without OCR:

* **Calibration comes from the gridlines, not from tick text.** These panels are
  scanned, so there is usually no text layer at all and OCR of small axis numerals
  is the least reliable step available. But a log grid draws minor lines at
  2,3,...,9 within every decade, and their spacing is a rigid, highly redundant
  pattern: matching detected gridlines against that ladder and least-squares
  fitting `pixel = m*log10(value) + b` calibrates from tens of constraints rather
  than four numerals. On the KDM sheet this lands at 0.98 px rms over 35 y-gridlines.
* **The curves are straight in log-log,** so tracking only has to survive local
  occlusion (gridlines crossing, the circled legend markers that sit ON the curves,
  the formula inset), and a straight-line fit with sigma-clipping cleans up the rest.

The traps this module is shaped around, all of which produced wrong answers first:

* **Lower-frequency curves leave the plot.** On the KDM panel the 8 kHz curve is at
  P ~= 0.93 mW/cm^3 at B = 190 G, i.e. below the P = 1 floor. A "take every column
  where exactly N runs are visible" strategy therefore silently substitutes legend
  markers for the missing curves and produced slopes of 2.04 with 0.27 dex scatter
  (versus 2.23 at 0.002 dex once tracked properly). Curve count is not constant
  across the panel; do not assume it is.
* **Adjacent curves can be ~30 px apart** (8 vs 10 kHz differ by only a factor 1.25
  in loss). Seeded tracking swaps between them wherever a legend marker covers one,
  so the lowest curve is re-extracted under an explicit "must lie below its
  neighbour's fitted line" constraint.
* **The frequency<->curve binding is checked, never assumed.** Ordering by height
  is only a hypothesis; it is verified by comparing each curve's fitted intercept
  against the printed law's value at a reference flux, which is a different
  quantity from the slope being measured.

Output: per-curve free-fit exponent, the digitized/printed ratio at a reference
flux, a residual figure in pixels (the review gate), and a QA overlay.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass, asdict
from pathlib import Path

import cv2
import numpy as np

# A curve is ink; ink on these panels is black on a light-cyan field.
_INK_MAX_CHANNEL = 120
#: Curve strokes are a few px at 600 dpi; anything taller is a legend blob or text.
_MAX_RUN_HEIGHT_PX = 40
#: Vertical gap that separates two distinct runs in one column.
_RUN_SPLIT_PX = 3
#: A gridline may occlude a curve for this many columns before tracking gives up.
_MAX_OCCLUSION_COLS = 40
#: Tracking accepts a candidate this far from the predicted row.
_TRACK_TOL_PX = 12


@dataclass
class CurveFit:
    """One frequency curve: free-fit power law plus its agreement with the print."""

    frequency_khz: float
    n_columns: int
    exponent: float           # free-fit alpha in  log10(P) = alpha*log10(B) + c
    exponent_stderr: float
    intercept: float
    b_min_gauss: float
    b_max_gauss: float
    fit_rms_dex: float
    #: Median distance from the fitted line to the nearest ink, in pixels. THIS is
    #: the review gate: it is measured against the image, not against the fit's own
    #: residuals, so it cannot be made small by a model that fits its own mistakes.
    ink_offset_median_px: float
    ink_offset_p95_px: float
    printed_law_ratio: float | None   # digitized / printed at the reference flux


@dataclass
class AxisCalibration:
    """`pixel = scale*log10(value) + offset`, fit from the log-decade gridlines."""

    scale: float
    offset: float
    n_gridlines: int
    rms_px: float

    def pixel(self, value):
        return self.scale * np.log10(value) + self.offset

    def value(self, pixel):
        return 10.0 ** ((np.asarray(pixel, dtype=float) - self.offset) / self.scale)


def kdm_sendust_law(b_kgauss, f_khz: float) -> float:
    """The law KDM prints on the Sendust panel. mW/cm^3, kGauss, kHz."""
    return b_kgauss ** 2.225 * (4.584 * f_khz + 0.0238 * f_khz ** 1.966)


def find_panel(rgb: np.ndarray, right_half: bool = False) -> tuple[int, int, int, int]:
    """Locate the tinted plot field. Returns (row0, row1, col0, col1) inclusive.

    The plot field is a solid tint (cyan on KDM/CSC sheets) and is by a wide margin
    the largest such block on the page, so a density threshold on the tint mask
    isolates it without needing the caption geometry the `find` command uses for
    vector datasheets. Density, not extent: the vendor logo is the same tint.
    """
    a = rgb.astype(int)
    tint = (a[:, :, 2] > 170) & (a[:, :, 1] > 170) & (a[:, :, 0] < a[:, :, 2] - 25)
    width = tint.shape[1]
    half = tint[:, width // 2:] if right_half else tint[:, : width // 2]
    rows = np.nonzero(half.sum(1) > 800)[0]
    cols = np.nonzero(half.sum(0) > 800)[0]
    if not len(rows) or not len(cols):
        raise ValueError("no tinted plot field found")
    shift = width // 2 if right_half else 0
    return int(rows.min()), int(rows.max()), int(cols.min()) + shift, int(cols.max()) + shift


def _gridline_centres(tint_line: np.ndarray, threshold: int) -> list[float]:
    """Centres of the white gaps in a tint profile -- i.e. the gridlines."""
    idx = np.nonzero(tint_line > threshold)[0]
    return [float(idx[i] + idx[i + 1]) / 2 for i in np.nonzero(np.diff(idx) > 5)[0]]


def calibrate_log_axis(p_lo: float, p_hi: float, v_lo: float, v_hi: float,
                       centres: list[float], name: str) -> AxisCalibration:
    """Fit pixel<->value from gridlines matched against the 1..9 log ladder.

    `p_lo`/`p_hi` are the pixel positions of `v_lo`/`v_hi` taken from the plot-field
    edges. They are only a SEED, accurate to a few px; every matched gridline then
    constrains the least-squares fit, so the returned calibration is far better than
    the seed. Outliers are rejected once: a gap widened by overlapping legend text
    biases its own centre (the KDM x-axis 5000 line, 17 px off) but nothing else.
    """
    decades = int(round(math.log10(v_hi / v_lo)))
    ticks = [v_lo * (10 ** d) * m for d in range(decades) for m in range(1, 10)] + [v_hi]
    per = (p_hi - p_lo) / decades
    tol = abs(per) * 0.05
    scale = offset = rms = 0.0

    pairs = []
    for centre in centres:
        err, tick = min((abs(p_lo + per * math.log10(t / v_lo) - centre), t) for t in ticks)
        if err < tol:
            pairs.append((math.log10(tick), centre))
    if len(pairs) < 4:
        raise ValueError(f"{name}: only {len(pairs)} gridlines matched the log ladder")

    for _ in range(2):
        A = np.array([[lv, 1.0] for lv, _ in pairs])
        y = np.array([px for _, px in pairs])
        scale, offset = np.linalg.lstsq(A, y, rcond=None)[0]
        resid = A @ [scale, offset] - y
        rms = float(np.sqrt((resid ** 2).mean()))
        keep = np.abs(resid) < max(3 * rms, 2.0)
        if keep.all():
            break
        pairs = [p for p, k in zip(pairs, keep) if k]
    return AxisCalibration(float(scale), float(offset), len(pairs), rms)


def _column_runs(ink: np.ndarray, col: int) -> list[float]:
    """Centres of the ink runs in one column, dropping blobs too tall to be a stroke."""
    idx = np.nonzero(ink[:, col])[0]
    if not len(idx):
        return []
    spans, start, prev = [], idx[0], idx[0]
    for v in idx[1:]:
        if v - prev > _RUN_SPLIT_PX:
            spans.append((start, prev))
            start = v
        prev = v
    spans.append((start, prev))
    return [(s + e) / 2.0 for s, e in spans if (e - s) <= _MAX_RUN_HEIGHT_PX]


def _track(ink: np.ndarray, x0: int, y0: float, step: int) -> list[tuple[int, float]]:
    """Follow one stroke from a seed, coasting through gridline/marker occlusions."""
    width = ink.shape[1]
    pts = [(x0, y0)]
    y, slope, x = y0, None, x0
    while True:
        x += step
        if not (0 <= x < width):
            break
        predicted = y + (slope * step if slope is not None else 0.0)
        cands = [c for c in _column_runs(ink, x) if abs(c - predicted) < _TRACK_TOL_PX]
        if not cands:
            probe, missed = x, 0
            while missed < _MAX_OCCLUSION_COLS and 0 <= probe < width:
                ahead = y + (slope * (probe - pts[-1][0]) if slope is not None else 0.0)
                if [c for c in _column_runs(ink, probe) if abs(c - ahead) < _TRACK_TOL_PX]:
                    break
                probe += step
                missed += 1
            if missed >= _MAX_OCCLUSION_COLS or not (0 <= probe < width):
                break
            x = probe
            ahead = y + (slope * (x - pts[-1][0]) if slope is not None else 0.0)
            cands = [c for c in _column_runs(ink, x) if abs(c - ahead) < _TRACK_TOL_PX]
            if not cands:
                break
        new_y = min(cands, key=lambda c: abs(c - predicted))
        new_slope = (new_y - y) / (x - pts[-1][0])
        slope = new_slope if slope is None else 0.7 * slope + 0.3 * new_slope
        y = new_y
        pts.append((x, y))
    return pts


def _fit_line(log_b: np.ndarray, log_p: np.ndarray) -> tuple[float, float, float, np.ndarray]:
    """Sigma-clipped straight-line fit. Returns slope, intercept, stderr, keep-mask."""
    keep = np.ones(len(log_b), bool)
    slope = intercept = 0.0
    for _ in range(8):
        A = np.vstack([log_b[keep], np.ones(keep.sum())]).T
        slope, intercept = np.linalg.lstsq(A, log_p[keep], rcond=None)[0]
        resid = log_p - (slope * log_b + intercept)
        sd = resid[keep].std()
        if sd == 0:
            break
        new = np.abs(resid) < 3 * sd
        if (new == keep).all():
            break
        keep = new
    A = np.vstack([log_b[keep], np.ones(keep.sum())]).T
    slope, intercept = np.linalg.lstsq(A, log_p[keep], rcond=None)[0]
    resid = A @ [slope, intercept] - log_p[keep]
    n = int(keep.sum())
    denom = ((log_b[keep] - log_b[keep].mean()) ** 2).sum()
    stderr = float(np.sqrt((resid ** 2).sum() / max(n - 2, 1) / denom)) if denom else float("nan")
    return float(slope), float(intercept), stderr, keep


def _ink_offsets(ink: np.ndarray, x_cal: AxisCalibration, y_cal: AxisCalibration,
                 slope: float, intercept: float, col0: int, row0: int) -> np.ndarray:
    """Distance from the fitted line to the nearest ink, per column. The review gate."""
    height, width = ink.shape
    out: list[float] = []
    for x in range(width):
        log_b = ((x + col0) - x_cal.offset) / x_cal.scale
        row = (y_cal.scale * (slope * log_b + intercept) + y_cal.offset) - row0
        if not (0 <= row < height):
            continue
        found = np.nonzero(ink[:, x])[0]
        if not len(found):
            continue
        near = found - row
        near = near[np.abs(near) < 25]
        if len(near):
            out.append(float(np.abs(near).min()))
    return np.array(out)


def digitize(page_rgb: np.ndarray, frequencies_khz: list[float], *,
             x_range=(100.0, 10000.0), y_range=(1.0, 10000.0),
             inset_boxes: Sequence[Sequence[int]] | None = None,
             reference_b_gauss: float = 1000.0,
             printed_law=None) -> tuple[list[CurveFit], AxisCalibration, AxisCalibration, dict]:
    """Digitize one core-loss panel. `frequencies_khz` is ordered top curve first.

    `inset_boxes` are panel-relative (row0, row1, col0, col1) regions to blank --
    the printed formula box and any opaque legend that would otherwise be tracked
    as ink.
    """
    row0, row1, col0, col1 = find_panel(page_rgb)
    sub = page_rgb[row0:row1 + 1, col0:col1 + 1].astype(int)
    ink = sub.max(2) < _INK_MAX_CHANNEL
    for r0, r1, c0, c1 in (inset_boxes or []):
        ink[r0:r1, c0:c1] = False

    tint = (sub[:, :, 2] > 170) & (sub[:, :, 1] > 170) & (sub[:, :, 0] < sub[:, :, 2] - 25)
    # A gridline is a column/row the tint does NOT fill. Thresholding at a third of
    # the panel's extent keeps curves and legend blobs from reading as gridlines.
    panel_h, panel_w = tint.shape
    x_cal = calibrate_log_axis(col0, col1, x_range[0], x_range[1],
                               [c + col0 for c in _gridline_centres(tint.sum(0), panel_h // 3)], "x")
    y_cal = calibrate_log_axis(row1, row0, y_range[0], y_range[1],
                               [r + row0 for r in _gridline_centres(tint.sum(1), panel_w // 3)], "y")

    # Seed where the most strokes are simultaneously visible and separated.
    counts = {x: len(_column_runs(ink, x)) for x in range(ink.shape[1])}
    want = len(frequencies_khz)
    seeds = [x for x, n in counts.items() if n == want]
    if not seeds:
        raise ValueError(f"no column shows all {want} curves; check inset_boxes")
    seed_x = seeds[len(seeds) // 2]

    fits: list[CurveFit] = []
    tracks: dict[int, np.ndarray] = {}
    for i, y_seed in enumerate(sorted(_column_runs(ink, seed_x))[:want]):
        pts = sorted(set(_track(ink, seed_x, y_seed, -1) + _track(ink, seed_x, y_seed, +1)))
        tracks[i] = np.array(pts, dtype=float)

    for i, freq in enumerate(frequencies_khz):
        pts = tracks[i]
        log_b = ((pts[:, 0] + col0) - x_cal.offset) / x_cal.scale
        log_p = ((pts[:, 1] + row0) - y_cal.offset) / y_cal.scale
        slope, intercept, stderr, keep = _fit_line(log_b, log_p)

        # A track whose scatter is an order of magnitude worse than a clean stroke
        # has swapped onto a neighbour somewhere. Re-extract it under the constraint
        # that it lies BELOW the previous curve's fitted line -- the swap direction.
        resid_rms = float(np.sqrt(((slope * log_b[keep] + intercept - log_p[keep]) ** 2).mean()))
        if resid_rms > 0.01 and i > 0:
            prev_s, prev_c = fits[i - 1].exponent, fits[i - 1].intercept
            redo = []
            for x in range(ink.shape[1]):
                lb = ((x + col0) - x_cal.offset) / x_cal.scale
                prev_row = (y_cal.scale * (prev_s * lb + prev_c) + y_cal.offset) - row0
                cand = [c for c in _column_runs(ink, x) if 18 < (c - prev_row) < 48]
                if len(cand) == 1:
                    redo.append((x, cand[0]))
            if len(redo) > 50:
                pts = np.array(redo, dtype=float)
                log_b = ((pts[:, 0] + col0) - x_cal.offset) / x_cal.scale
                log_p = ((pts[:, 1] + row0) - y_cal.offset) / y_cal.scale
                slope, intercept, stderr, keep = _fit_line(log_b, log_p)

        offsets = _ink_offsets(ink, x_cal, y_cal, slope, intercept, col0, row0)
        ratio = None
        if printed_law is not None:
            digit = 10 ** (slope * math.log10(reference_b_gauss) + intercept)
            ratio = float(digit / printed_law(reference_b_gauss / 1000.0, freq))
        fits.append(CurveFit(
            frequency_khz=freq, n_columns=int(keep.sum()),
            exponent=slope, exponent_stderr=stderr, intercept=intercept,
            b_min_gauss=float(10 ** log_b[keep].min()), b_max_gauss=float(10 ** log_b[keep].max()),
            fit_rms_dex=float(np.sqrt(((slope * log_b[keep] + intercept - log_p[keep]) ** 2).mean())),
            ink_offset_median_px=float(np.median(offsets)) if len(offsets) else float("nan"),
            ink_offset_p95_px=float(np.percentile(offsets, 95)) if len(offsets) else float("nan"),
            printed_law_ratio=ratio,
        ))
        tracks[i] = pts
    return fits, x_cal, y_cal, {"panel": (row0, row1, col0, col1)}


_OVERLAY_COLORS = [(0, 45, 255), (0, 138, 255), (0, 200, 216),
                   (74, 192, 0), (255, 160, 0), (255, 0, 192)]  # BGR


def render_overlay(page_rgb: np.ndarray, fits: list[CurveFit], x_cal: AxisCalibration,
                   y_cal: AxisCalibration, panel: tuple[int, int, int, int],
                   x_ticks: Sequence[float], y_ticks: Sequence[float], out_path: Path) -> None:
    """QA overlay: digitized curves plus the digitizer's OWN tick positions.

    The tick marks are the point of the figure. Drawing the fitted curves alone
    shows only that the tracker followed some ink; drawing where the calibration
    thinks each decade lies lets a reviewer check it against the numerals the
    vendor printed, which is the half that silently ruins values when it is wrong.
    """
    row0, row1, col0, col1 = panel
    pad_l, pad_r, pad_t, pad_b = 210, 110, 100, 200
    y0 = max(row0 - pad_t, 0)
    x0 = max(col0 - pad_l, 0)
    crop = page_rgb[y0:row1 + pad_b, x0:col1 + pad_r].copy()
    img = cv2.cvtColor(crop, cv2.COLOR_RGB2BGR)

    def px(b):
        return int(round(float(x_cal.pixel(b)) - x0))

    def py(p):
        return int(round(float(y_cal.pixel(p)) - y0))

    red = (0, 0, 210)
    for v in x_ticks:
        X = px(v)
        cv2.line(img, (X, py(y_ticks[0])), (X, py(y_ticks[0]) + 30), red, 3)
        cv2.putText(img, f"{v:g}", (X - 34, py(y_ticks[0]) + 84),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.1, red, 3, cv2.LINE_AA)
    for v in y_ticks:
        Y = py(v)
        cv2.line(img, (px(x_ticks[0]) - 30, Y), (px(x_ticks[0]), Y), red, 3)
        cv2.putText(img, f"{v:g}", (px(x_ticks[0]) - 190, Y + 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.1, red, 3, cv2.LINE_AA)

    for i, fit in enumerate(fits):
        color = _OVERLAY_COLORS[i % len(_OVERLAY_COLORS)]
        b = np.logspace(math.log10(x_ticks[0]), math.log10(x_ticks[-1]), 900)
        p = 10 ** (fit.exponent * np.log10(b) + fit.intercept)
        ok = (p >= y_ticks[0]) & (p <= y_ticks[-1])
        pts = [(px(bb), py(pp)) for bb, pp in zip(b[ok], p[ok])]
        for k in range(0, len(pts) - 8, 16):     # dashed, so the ink stays visible
            cv2.line(img, pts[k], pts[k + 8], color, 3, cv2.LINE_AA)
        cv2.putText(img, f"{fit.frequency_khz:g}kHz a={fit.exponent:.4f}",
                    (img.shape[1] - 640, 60 + 46 * i),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, color, 3, cv2.LINE_AA)
    cv2.imwrite(str(out_path), img)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="dsdig digitize-core-loss",
        description="Digitize a powder-core core-loss panel and check it against the printed law.")
    ap.add_argument("pdf", help="datasheet PDF (scanned is fine; no text layer needed)")
    ap.add_argument("--page", type=int, default=1)
    ap.add_argument("--dpi", type=int, default=600)
    ap.add_argument("--frequencies", default="100,50,25,16,10,8",
                    help="curve frequencies in kHz, TOP curve first (default KDM Sendust)")
    ap.add_argument("--x-range", default="100,10000", help="flux axis min,max in gauss")
    ap.add_argument("--y-range", default="1,10000", help="loss axis min,max in mW/cm^3")
    ap.add_argument("--inset", action="append", default=[],
                    help="panel-relative r0,r1,c0,c1 region to blank (formula box, legend)")
    ap.add_argument("--law", choices=["kdm-sendust", "none"], default="kdm-sendust")
    ap.add_argument("--out", default="out/core_loss")
    a = ap.parse_args(argv)

    import fitz
    doc = fitz.open(a.pdf)
    pix = doc[a.page - 1].get_pixmap(dpi=a.dpi)
    rgb = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3]

    freqs = [float(v) for v in a.frequencies.split(",")]
    xr = tuple(float(v) for v in a.x_range.split(","))
    yr = tuple(float(v) for v in a.y_range.split(","))
    insets: list[Sequence[int]] = [[int(v) for v in s.split(",")] for s in a.inset]
    law = kdm_sendust_law if a.law == "kdm-sendust" else None

    fits, x_cal, y_cal, meta = digitize(rgb, freqs, x_range=xr, y_range=yr,
                                        inset_boxes=insets, printed_law=law)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    print(f"x-axis: {x_cal.n_gridlines} gridlines, {x_cal.rms_px:.2f} px rms")
    print(f"y-axis: {y_cal.n_gridlines} gridlines, {y_cal.rms_px:.2f} px rms")
    print(f"{'f/kHz':>7} {'n':>6} {'alpha (free)':>18} {'rms/dex':>9} "
          f"{'ink med px':>11} {'p95':>6} {'vs printed':>11}")
    for f in fits:
        ratio = "-" if f.printed_law_ratio is None else f"{f.printed_law_ratio:.4f}"
        print(f"{f.frequency_khz:7g} {f.n_columns:6d} "
              f"{f.exponent:10.4f} +-{f.exponent_stderr:.4f} {f.fit_rms_dex:9.4f} "
              f"{f.ink_offset_median_px:11.2f} {f.ink_offset_p95_px:6.2f} {ratio:>11}")
    alphas = np.array([f.exponent for f in fits])
    print(f"\nmean exponent {alphas.mean():.4f} sd {alphas.std(ddof=1):.4f} over {len(fits)} curves")

    xt = [float(v) for v in (100, 200, 300, 500, 1000, 2000, 3000, 5000, 10000) if xr[0] <= v <= xr[1]]
    yt = [float(v) for v in (1, 3, 10, 30, 100, 300, 1000, 3000, 10000) if yr[0] <= v <= yr[1]]
    overlay_path = out / (Path(a.pdf).stem + "-core-loss-overlay.png")
    render_overlay(rgb, fits, x_cal, y_cal, meta["panel"], xt, yt, overlay_path)
    (out / (Path(a.pdf).stem + "-core-loss.json")).write_text(json.dumps(
        {"source": a.pdf, "page": a.page, "dpi": a.dpi,
         "x_calibration": asdict(x_cal), "y_calibration": asdict(y_cal),
         "curves": [asdict(f) for f in fits]}, indent=2))
    print(f"\nOVERLAY: {overlay_path}")
    print("NOTE: agent review never sets human_verified -- the overlay is the review gate.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
