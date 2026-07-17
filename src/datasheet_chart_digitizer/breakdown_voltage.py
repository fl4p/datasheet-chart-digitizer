"""Digitize drain-source breakdown-voltage charts (V(BR)DSS vs Tj).

Targets the Infineon "Diagram 15: Drain-source breakdown voltage" style: a
single stroked vector line on a linear/linear grid, V(BR)DSS on Y versus
junction temperature on X (typically -75..200 C), condition ID=1 mA.

Differences from the existing pipelines that justify a plugin:
  * both axes are LINEAR and X ticks are NEGATIVE numbers — the C(V)
    position calibration (linear-X digits only / log-Y decades) cannot fit
    them,
  * exactly ONE curve is expected — the C(V) vector extractor demands three
    full-span candidates and refuses this chart,
  * the value downstream consumers need is not the trace itself but the
    fitted line (V at 25 C, slope in mV/K) plus a spec-table anchor verdict:
    on the known Infineon parts the 25 C chart value equals the V(BR)DSS
    MINIMUM from the parameter table (the chart is the spec floor over
    temperature, not a typical curve). The anchor check verifies that
    min-anchored interpretation per part instead of assuming it.

Usage:
    dsdig digitize-breakdown-voltage work/charts/charts.json --out work/bv

Outputs per panel: an overlay PNG (digitized line + calibration ticks), a
calibrated (Tj, V) CSV, and breakdown_voltage_digitization.json with fit,
anchor verdict, and calibration diagnostics.

Anchor verdicts are tri-state and absence never passes:
  * "verified"   — chart V(25 C) matches the parsed spec minimum,
  * "FAIL"       — spec minimum found but the chart contradicts it,
  * "unverified" — no spec minimum found; the values are NOT validated.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .capacitance_traces import find_plot_box
from .capacitance_types import PlotBox
from .capacitance_vector import (
    _chain_vector_components,
    _load_fitz,
    _mostly_inside_plot,
    _path_length,
    _resample_vector_trace_pixels,
    _vector_curve_edges,
)
from .crop_transform import CropTransform
from .numeric_axis import AxisTick, fit_axis_ticks
from .overlay import draw_axis_ticks, draw_plot_frame

INT_RE = re.compile(r"^-?\d+$")
NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
# Parameter-table row. New layout: "V(BR)DSS  <min> - - V". Older layouts put
# the test conditions in between: "V (BR)DSS V GS =0 V, I D =1 mA  <min> - -".
# The window scan skips condition values (digits glued to '=' or a word) and
# takes the first free-standing plausible voltage.
SPEC_ROW_RE = re.compile(r"V\s*\(\s*BR\s*\)\s*DSS(.{0,80})")
SPEC_VALUE_RE = re.compile(r"(?<![=\w.])(-?\d+(?:\.\d+)?)")

# Hard gates. Vector tick text is exact, so calibration residuals beyond a
# small fraction of the labeled span mean the label-position pairing is wrong,
# not merely noisy — refuse rather than emit shifted values.
MAX_CAL_RESID_FRAC = 0.02
MIN_TICKS_PER_AXIS = 4
MIN_X_SPAN_FRAC = 0.5
ANCHOR_TOL = 0.02


@dataclass
class LinearAxis:
    """value = m * px + b, fitted from tick-label positions."""

    m: float
    b: float
    ticks: list[tuple[float, float]]  # (value, px)
    resid: float                      # max |fit - label| in value units

    def value(self, px: float) -> float:
        return self.m * px + self.b


def _fit_axis(ticks: list[tuple[float, float]], what: str) -> LinearAxis:
    """Linear pixel->value calibration via the ONE shared numeric_axis fitter.

    Delegates the least-squares fit to ``numeric_axis.fit_axis_ticks`` instead of
    re-deriving np.polyfit + the monotonicity check here (axis-fitter
    consolidation), then keeps breakdown's own policy on top: a stricter
    ``>=MIN_TICKS_PER_AXIS`` (4) min-count and the value-space residual gate (max
    abs error <= MAX_CAL_RESID_FRAC of the value span). V(BR)/Tj axes are always
    linear, so the fit is FORCED linear (``model="linear"``) — this is required,
    not cosmetic: a valid narrow-positive V axis is near-indistinguishable from
    log, and the shared fitter's "auto" ambiguity gate would false-refuse it. A
    genuinely non-linear tick set forced to linear still fails the residual gate.
    Ticks are (value, pixel) pairs; breakdown raises on any refusal (transfer
    relies on that)."""
    # dedup exact-duplicate (value, pixel) ticks (doubled text layers) so the
    # shared fitter's strict-monotone gate doesn't reject a zero gap; a
    # same-value/different-pixel or same-pixel/different-value pair is a real
    # conflict and stays fail-closed (fit_axis_ticks raises non-monotone).
    ticks = list(dict.fromkeys(ticks))
    if len(ticks) < MIN_TICKS_PER_AXIS:
        raise RuntimeError(f"{what}: only {len(ticks)} tick labels, need >={MIN_TICKS_PER_AXIS}")
    fit = fit_axis_ticks([AxisTick(f"{v:g}", v, px) for v, px in ticks], what, model="linear")
    values = np.array([v for v, _ in ticks], dtype=float)
    pixels = np.array([p for _, p in ticks], dtype=float)
    order = np.argsort(pixels)
    values, pixels = values[order], pixels[order]
    resid = float(np.max(np.abs(fit.m * pixels + fit.b - values)))
    span = float(np.max(values) - np.min(values))
    if span <= 0 or resid > MAX_CAL_RESID_FRAC * span:
        raise RuntimeError(
            f"{what}: calibration residual {resid:.3g} exceeds {MAX_CAL_RESID_FRAC:.0%} "
            f"of the {span:g}-unit tick span — label/position pairing untrusted"
        )
    return LinearAxis(float(fit.m), float(fit.b), [(float(v), float(p)) for v, p in zip(values, pixels)], resid)


def _calibrate(words_px: list[tuple[str, float, float]], plot: PlotBox, img_h: int,
               x_pattern=INT_RE) -> tuple[LinearAxis, LinearAxis]:
    """Fit X (Tj) and Y (V) axes from tick-label words in crop-pixel space."""
    x_band = (plot.y1 + 0.004 * img_h, plot.y1 + 0.055 * img_h)
    x_ticks = [
        (float(text), cx)
        for text, cx, cy in words_px
        if x_pattern.match(text) and x_band[0] <= cy <= x_band[1] and plot.x0 - 0.03 * plot.width <= cx <= plot.x1 + 0.03 * plot.width
    ]
    y_ticks = [
        (float(text), cy)
        for text, cx, cy in words_px
        if NUM_RE.match(text)
        and plot.x0 - 0.22 * plot.width <= cx <= plot.x0 - 2
        and plot.y0 - 0.02 * plot.height <= cy <= plot.y1 + 0.02 * plot.height
    ]
    return _fit_axis(x_ticks, "X axis (Tj)"), _fit_axis(y_ticks, "Y axis (V(BR)DSS)")


def _uniform_family_extent(positions: list[float], min_count: int = 5) -> tuple[float, float] | None:
    """Extent of a uniform-pitch line family (a plot GRID), or None.

    Long strokes in a chart panel are either the gridline family (uniform
    pitch, frame lines included) or stray rules — the outer panel border,
    title/footer separators. Requiring a uniform family separates the two:
    end lines whose gap does not match the grid pitch are trimmed (at most
    two per side), and a family that still is not uniform is rejected.
    """
    merged: list[float] = []
    for pos in sorted(positions):
        if merged and pos - merged[-1] <= 2.0:
            merged[-1] = (merged[-1] + pos) / 2
        else:
            merged.append(pos)
    for _ in range(2):
        if len(merged) < max(min_count, 3):
            return None
        gaps = [b - a for a, b in zip(merged, merged[1:])]
        pitch = sorted(gaps)[len(gaps) // 2]
        if pitch <= 0:
            return None
        if abs(gaps[0] - pitch) > 0.35 * pitch:
            merged = merged[1:]
            continue
        if abs(gaps[-1] - pitch) > 0.35 * pitch:
            merged = merged[:-1]
            continue
        break
    if len(merged) < min_count:
        return None
    gaps = [b - a for a, b in zip(merged, merged[1:])]
    pitch = sorted(gaps)[len(gaps) // 2]
    if any(abs(g - pitch) > 0.35 * pitch for g in gaps):
        return None
    return merged[0], merged[-1]


def _vector_plot_frame(page, transform: CropTransform, image_shape: tuple[int, int]) -> PlotBox | None:
    """Detect the plot frame from the vector gridline family (exact).

    The raster find_plot_box rejects frame lines within 8% of the crop edge;
    tight caption-derived crops (older Infineon layouts) put the real frame
    exactly there, which silently CLIPPED the digitized curve at an interior
    gridline. Vector geometry has no such margin assumption. Only a
    uniform-pitch family counts as a grid: layouts whose long strokes are
    just the outer panel border (newer Infineon pages draw the grid another
    way) yield None here and use the raster detector instead.
    """
    height, width = image_shape
    verticals: list[float] = []
    horizontals: list[float] = []
    for drawing in page.get_drawings():
        if drawing.get("type") not in {"s", "fs"}:
            continue
        for item in drawing.get("items", []):
            if item[0] != "l":
                continue
            x0, y0 = transform.to_px(float(item[1].x), float(item[1].y))
            x1, y1 = transform.to_px(float(item[2].x), float(item[2].y))
            if not (-2 <= min(x0, x1) and max(x0, x1) <= width + 2 and -2 <= min(y0, y1) and max(y0, y1) <= height + 2):
                continue
            if abs(x1 - x0) <= 1.5 and abs(y1 - y0) >= height * 0.55:
                verticals.append((x0 + x1) / 2)
            elif abs(y1 - y0) <= 1.5 and abs(x1 - x0) >= width * 0.55:
                horizontals.append((y0 + y1) / 2)
    x_extent = _uniform_family_extent(verticals)
    y_extent = _uniform_family_extent(horizontals)
    if x_extent is None or y_extent is None:
        return None
    box = PlotBox(
        x0=int(round(x_extent[0])),
        y0=int(round(y_extent[0])),
        x1=int(round(x_extent[1])),
        y1=int(round(y_extent[1])),
    )
    if box.width < width * 0.5 or box.height < height * 0.4:
        return None
    return box


def _extract_single_trace(page, transform: CropTransform, plot: PlotBox, fitz) -> list[tuple[int, int]]:
    """Return the one full-span stroked curve inside the plot, in crop pixels."""
    px0, py0 = transform.to_pt(plot.x0, plot.y0)
    px1, py1 = transform.to_pt(plot.x1, plot.y1)
    plot_rect = fitz.Rect(px0, py0, px1, py1)
    edges = _vector_curve_edges(page.get_drawings(), plot_rect)
    components = _chain_vector_components(edges)
    candidates: list[tuple[float, list[tuple[int, int]]]] = []
    for component in components:
        if len(component) < 2:
            continue
        xs = [p[0] for p in component]
        if max(xs) - min(xs) < plot_rect.width * MIN_X_SPAN_FRAC:
            continue
        if not _mostly_inside_plot(component, plot_rect):
            continue
        raw = []
        for x, y in component:
            fx, fy = transform.to_px(x, y)
            raw.append((int(round(fx)), int(round(fy))))
        points = _resample_vector_trace_pixels(raw, plot)
        if len(points) < plot.width * MIN_X_SPAN_FRAC:
            continue
        candidates.append((_path_length(component), points))
    if len(candidates) != 1:
        raise RuntimeError(
            f"expected exactly 1 full-span breakdown curve, found {len(candidates)} — "
            "not silently picking one; check the crop"
        )
    return candidates[0][1]


def _spec_min_vbrdss(doc) -> tuple[float, int] | None:
    """Parse the parameter-table V(BR)DSS minimum; None means NOT verified."""
    for page_num in range(min(len(doc), 6)):
        text = re.sub(r"\s+", " ", doc[page_num].get_text("text"))
        for row in SPEC_ROW_RE.finditer(text):
            for match in SPEC_VALUE_RE.finditer(row.group(1)):
                value = float(match.group(1))
                if 10.0 <= value <= 2000.0:
                    return value, page_num + 1
    return None


def _words_in_crop_px(page, transform: CropTransform, image_shape: tuple[int, int]) -> list[tuple[str, float, float]]:
    height, width = image_shape
    out = []
    for w in page.get_text("words"):
        cx, cy = transform.to_px((w[0] + w[2]) / 2, (w[1] + w[3]) / 2)
        if -width * 0.05 <= cx <= width * 1.05 and -height * 0.05 <= cy <= height * 1.05:
            out.append((w[4].strip(), cx, cy))
    return out


def _draw_overlay(image: np.ndarray, plot: PlotBox, points: list[tuple[int, int]],
                  x_axis: LinearAxis, y_axis: LinearAxis) -> np.ndarray:
    overlay = image.copy()
    draw_plot_frame(overlay, plot, (0, 180, 0), 1)
    for x, y in points:
        cv2.circle(overlay, (x, y), 1, (0, 0, 255), -1)
    # LinearAxis.ticks are (value, pixel); the shared renderer wants (pixel, value)
    draw_axis_ticks(
        overlay,
        plot,
        x_ticks=[(px, value) for value, px in x_axis.ticks],
        y_ticks=[(py, value) for value, py in y_axis.ticks],
        color=(255, 0, 0),
        marker_size=10,
        font_scale=0.35,
    )
    return overlay


def process_chart(chart: dict, crop_path: Path, out_dir: Path, rel_stem: Path) -> dict:
    fitz = _load_fitz()
    if fitz is None:
        raise RuntimeError("PyMuPDF is not available")
    image = cv2.imread(str(crop_path), cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError(f"could not read crop {crop_path}")
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    transform = CropTransform.for_chart(chart, gray.shape)
    doc = fitz.open(str(chart["pdf"]))
    try:
        page = doc[int(chart["page"]) - 1]
        frame_method = "vector"
        plot = _vector_plot_frame(page, transform, gray.shape)
        if plot is None:
            frame_method = "raster"
            plot = find_plot_box(gray)
        words_px = _words_in_crop_px(page, transform, gray.shape)
        x_axis, y_axis = _calibrate(words_px, plot, gray.shape[0])
        points_px = _extract_single_trace(page, transform, plot, fitz)
        spec = _spec_min_vbrdss(doc)
    finally:
        doc.close()

    data = sorted(
        ((x_axis.value(x), y_axis.value(y)) for x, y in points_px),
        key=lambda p: p[0],
    )
    tj = np.array([p[0] for p in data])
    v = np.array([p[1] for p in data])
    tj_min, tj_max = float(tj.min()), float(tj.max())
    if not (tj_min <= 25.0 <= tj_max):
        raise RuntimeError(f"digitized Tj range [{tj_min:.0f}, {tj_max:.0f}] C does not cover 25 C")

    slope, intercept = np.polyfit(tj, v, 1)
    fit_rms = float(np.sqrt(np.mean((slope * tj + intercept - v) ** 2)))
    v25_data = float(np.interp(25.0, tj, v))
    v25_fit = float(slope * 25.0 + intercept)

    warnings: list[str] = []
    px_xs = [x for x, _ in points_px]
    if min(px_xs) <= plot.x0 + 2 or max(px_xs) >= plot.x1 - 2:
        warnings.append(
            "digitized curve touches the plot frame edge — the trace may be CLIPPED "
            "by a mis-detected frame; verify the overlay before using the end points"
        )
    if fit_rms > 0.01 * float(np.mean(v)):
        warnings.append(
            f"line fit RMS {fit_rms:.3g} V is large — curve is visibly non-linear; "
            "use the CSV, not the slope/intercept summary"
        )

    if spec is None:
        anchor_verdict = "unverified"
        anchor: dict = {"verdict": anchor_verdict,
                        "note": "no V(BR)DSS minimum found in the parameter table — chart values NOT validated"}
        warnings.append("spec anchor unavailable: V(BR)DSS(25 C) could not be checked against the table minimum")
    else:
        spec_min, spec_page = spec
        err = (v25_data - spec_min) / spec_min
        anchor_verdict = "verified" if abs(err) <= ANCHOR_TOL else "FAIL"
        anchor = {"verdict": anchor_verdict, "spec_min_v": spec_min, "spec_page": spec_page,
                  "chart_v25_v": round(v25_data, 3), "err": round(err, 5)}
        if anchor_verdict == "FAIL":
            warnings.append(
                f"chart V(25 C)={v25_data:.2f} V does not match the table minimum {spec_min:g} V "
                f"({err:+.1%}) — the min-anchored interpretation does NOT hold for this part"
            )

    csv_path = out_dir / "points" / rel_stem.parent / f"{rel_stem.name}.bv_points.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["Tj_C", "VBR_DSS_V"])
        for t, vv in data:
            writer.writerow([f"{t:.2f}", f"{vv:.4f}"])

    overlay = _draw_overlay(image, plot, points_px, x_axis, y_axis)
    overlay_path = out_dir / "overlays" / rel_stem.parent / f"{rel_stem.name}.bv_overlay.png"
    overlay_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(overlay_path), overlay)

    return {
        "method": "vector",
        "frame_method": frame_method,
        "n_points": len(data),
        "tj_range_c": [round(tj_min, 1), round(tj_max, 1)],
        "v_at_25c": round(v25_data, 3),
        "v_at_25c_linefit": round(v25_fit, 3),
        "slope_mv_per_k": round(float(slope) * 1e3, 2),
        "line_fit_rms_v": round(fit_rms, 4),
        "calibration": {
            "x_ticks": len(x_axis.ticks), "x_resid": round(x_axis.resid, 4),
            "y_ticks": len(y_axis.ticks), "y_resid": round(y_axis.resid, 4),
        },
        "anchor": anchor,
        "status": anchor_verdict,
        "warnings": warnings,
        "csv": str(csv_path),
        "overlay": str(overlay_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("charts_json", type=Path, help="charts.json from `dsdig find`")
    parser.add_argument("--out", type=Path, default=None, help="output directory (default: alongside charts.json)")
    args = parser.parse_args()

    base_dir = args.charts_json.parent
    out_dir = args.out or base_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    charts = json.loads(args.charts_json.read_text())

    results: list[dict] = []
    errors: list[dict] = []
    for chart in charts:
        if chart.get("kind") != "breakdown_voltage":
            continue
        crop_rel = Path(chart["crop_png"])
        rel_stem = crop_rel.with_suffix("")
        print(f"digitize {chart['part']} diagram {chart['diagram']}: {crop_rel}")
        try:
            result = process_chart(chart, base_dir / crop_rel, out_dir, rel_stem)
        except Exception as exc:
            print(f"  ERROR: {exc}")
            errors.append({"part": chart.get("part"), "crop": str(crop_rel), "error": str(exc)})
        else:
            result.update({"part": chart["part"], "page": chart["page"], "diagram": chart["diagram"],
                           "pdf": chart["pdf"]})
            results.append(result)
            print(
                f"  V(25C)={result['v_at_25c']:.2f} V, slope={result['slope_mv_per_k']:.1f} mV/K, "
                f"anchor={result['anchor']['verdict']}"
            )
            for warning in result["warnings"]:
                print(f"  WARNING: {warning}")

    manifest = {"panels": results, "errors": errors}
    (out_dir / "breakdown_voltage_digitization.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"wrote {out_dir / 'breakdown_voltage_digitization.json'}")
    if errors:
        raise SystemExit(1)
    if not results:
        print("ERROR: no breakdown_voltage panels in the index — nothing digitized")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
