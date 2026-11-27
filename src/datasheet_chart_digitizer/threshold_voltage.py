"""Digitize gate-threshold-voltage charts: Vth (VGS(th)) versus temperature.

One curve on linear/linear axes, the same panel shape as breakdown voltage, so
the calibration and trace come from ``breakdown_voltage.calibrated_temperature_trace``
(vector panels, and whole-figure raster panels via ``raster_figure``).

Validation: Fab approved the overlays of the Toshiba raster packet on 2026-11-21
("all overlays pass", merge 32aa388).  The packet's part list is not recorded; the
tests cover TPH1R306PL, TK100E10N1, TPCC8105 and XPW4R10ANB.  Other vendors and
vector layouts are unvalidated.  Every served panel stays ``overlay-review-required``
(per-panel review, as for transfer curves); nothing is ever ``ok``.

Per panel:
  * the calibrated (T, Vth) curve as printed, with the temperature kind the
    caption names (Ta, Tj, Tc, Tch),
  * readouts at fixed temperatures, interpolated along the trace, never
    extrapolated (``not_on_chart`` outside the traced span),
  * a tri-state table check: the chart at 25 C against the spec table's Vth
    min/max (``consistent`` / ``inconsistent`` / ``not_evaluable``).  A typical
    curve inside the guaranteed window proves the axis scale only coarsely; it
    is recorded, never used to adjust the curve.

Usage:
    dsdig digitize-threshold-voltage work/charts/charts.json --out work/vth
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

import cv2
import numpy as np

from . import served_axis_guard
from .breakdown_voltage import _draw_overlay, calibrated_temperature_trace
from .capacitance_vector import _load_fitz
from . import visible_text

READOUT_TEMPERATURES_C = (-50.0, 25.0, 100.0, 150.0)
_TEMP_KIND_RE = re.compile(r"t\s*(ch|a|j|c)\s*$", re.IGNORECASE)
_ROW_LABEL_RE = re.compile(r"gate\s+threshold\s+voltage", re.IGNORECASE)
_NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
_ID_COND_RE = re.compile(r"I\s*D\s*=\s*-?\s*(\d+(?:\.\d+)?)\s*(mA|µA|uA|A)", re.IGNORECASE)


def _validate_panel_semantics(chart: dict) -> None:
    if chart.get("kind") != "threshold_voltage":
        raise RuntimeError(f"panel kind {chart.get('kind')!r} is not threshold_voltage")


def temperature_kind(title: str) -> str | None:
    """'Ta'/'Tj'/'Tc'/'Tch' from a caption such as 'Vth - Ta', else None."""
    match = _TEMP_KIND_RE.search(re.sub(r"[^A-Za-z]+", " ", title).strip())
    return f"T{match.group(1).lower()}" if match else None


def spec_vth_window(doc) -> dict | None:
    """The spec table's Vth row: min/typ/max by column header, and its ID condition.

    Values are bound to the Min/Typ/Max header words of the same page by x
    position; a row whose numbers do not sit under a header refuses (None).
    """
    for page in doc:
        words = visible_text.words(page)
        lines: dict[tuple[int, int], list] = {}
        for w in words:
            lines.setdefault((w[5], w[6]), []).append(w)
        headers: dict[str, float] = {}
        for w in words:
            token = w[4].strip(".").lower()
            if token in ("min", "typ", "max") and token not in headers:
                headers[token] = 0.5 * (w[0] + w[2])
        if len(headers) < 2:
            continue
        for line in lines.values():
            text = " ".join(w[4] for w in line)
            if not _ROW_LABEL_RE.search(text):
                continue
            row_y = 0.5 * (line[0][1] + line[0][3])
            row = [w for w in words if abs(0.5 * (w[1] + w[3]) - row_y) < 3.0]
            row.sort(key=lambda w: w[0])
            row_text = " ".join(w[4] for w in row)
            values: dict[str, float] = {}
            for w in row:
                if not _NUM_RE.match(w[4]):
                    continue
                cx = 0.5 * (w[0] + w[2])
                column, dx = min(((k, abs(cx - x)) for k, x in headers.items()), key=lambda item: item[1])
                if dx <= 15.0:
                    values[column] = float(w[4])
            if not values:
                continue
            cond = _ID_COND_RE.search(row_text)
            id_a = None
            if cond:
                scale = {"ma": 1e-3, "µa": 1e-6, "ua": 1e-6, "a": 1.0}[cond.group(2).lower()]
                id_a = float(cond.group(1)) * scale
            return {"page": page.number + 1, "row_text": row_text, "id_a": id_a, **values}
    return None


def _readouts(data: list[tuple[float, float]]) -> list[dict]:
    t = np.array([p[0] for p in data])
    v = np.array([p[1] for p in data])
    out = []
    for temp in READOUT_TEMPERATURES_C:
        if t.min() <= temp <= t.max():
            out.append({"t_c": temp, "vth_v": round(float(np.interp(temp, t, v)), 4), "state": "read"})
        else:
            out.append({"t_c": temp, "vth_v": None, "state": "not_on_chart"})
    return out


def _table_check(v25: float | None, spec: dict | None) -> dict:
    if spec is None or v25 is None or ("min" not in spec and "max" not in spec):
        return {"verdict": "not_evaluable", "reason": "no Vth row with min/max" if spec is None or v25 is not None
                else "chart does not reach 25 C", "spec": spec}
    lo, hi = spec.get("min"), spec.get("max")
    if lo is not None and hi is not None:
        lo, hi = min(lo, hi), max(lo, hi)  # P-channel tables print min -0.8, max -2.0
    elif lo is not None:
        lo, hi = (lo, np.inf) if lo > 0 else (-np.inf, lo)
    else:
        lo, hi = (-np.inf, hi) if hi > 0 else (hi, np.inf)
    verdict = "consistent" if lo <= v25 <= hi else "inconsistent"
    return {"verdict": verdict, "chart_v_at_25c": round(v25, 4), "spec": spec,
            "assumption": "chart temperature at 25 C compared with the table's 25 C row; "
                          "chart and table drain currents compared only if both are printed"}


def process_chart(chart: dict, crop_path: Path, out_dir: Path, rel_stem: Path) -> dict:
    _validate_panel_semantics(chart)
    traced = calibrated_temperature_trace(chart, crop_path, "Y axis (Vth)", "X axis (T)")
    _failed, unverified = served_axis_guard.problems(traced.grid_checks)
    data = sorted(
        ((traced.x_axis.value(x), traced.y_axis.value(y)) for x, y in traced.points_px),
        key=lambda p: p[0],
    )
    if len(data) < 20:
        raise RuntimeError(f"only {len(data)} calibrated points")
    temps = np.array([p[0] for p in data])
    volts = np.array([p[1] for p in data])
    if not (np.all(volts > 0) or np.all(volts < 0)):
        raise RuntimeError("calibrated Vth changes sign or touches 0 V; axis or trace is wrong")
    readouts = _readouts(data)
    v25 = next((r["vth_v"] for r in readouts if r["t_c"] == 25.0), None)
    slope = float(np.polyfit(temps, volts, 1)[0])
    fitz = _load_fitz()
    with fitz.open(str(chart["pdf"])) as doc:
        spec = spec_vth_window(doc)
    check = _table_check(v25, spec)

    warnings = [("HUMAN REVIEW REQUIRED: class approved on Toshiba raster figures only; "
                "verify axes and the trace on the overlay")]
    if slope * np.sign(volts[0]) >= 0:
        warnings.append("|Vth| rises with temperature: physically unexpected; check the axis direction")
    if check["verdict"] == "inconsistent":
        warnings.append("chart Vth at 25 C lies outside the table's min..max window")
    if unverified:
        warnings.append("axis grid check unverified: " + "; ".join(unverified))
    if traced.withheld_points:
        warnings.append(f"withheld {traced.withheld_points} source points beyond one unlabeled X interval")

    csv_path = out_dir / "points" / rel_stem.parent / f"{rel_stem.name}.vth_points.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    kind = temperature_kind(str(chart.get("title", "")))
    with csv_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([f"{kind or 'T'}_C", "Vth_V"])
        for t, v in data:
            writer.writerow([f"{t:.2f}", f"{v:.4f}"])
    overlay = _draw_overlay(traced.image, traced.plot, traced.points_px, traced.x_axis, traced.y_axis,
                            x_labels_below=traced.axis_text_source == "native")
    overlay_path = out_dir / "overlays" / rel_stem.parent / f"{rel_stem.name}.vth_overlay.png"
    overlay_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(overlay_path), overlay)

    result = {
        "status": "overlay-review-required",
        "method": traced.trace_method,
        "frame_method": traced.frame_method,
        "axis_text_source": traced.axis_text_source,
        "temperature_kind": kind,
        "n_points": len(data),
        "t_range_c": [round(float(temps.min()), 1), round(float(temps.max()), 1)],
        "readouts": readouts,
        "slope_mv_per_k": round(slope * 1e3, 2),
        "table_check": check,
        "calibration": {
            "x_ticks": len(traced.x_axis.ticks), "y_ticks": len(traced.y_axis.ticks),
            "x_axis": {"model": "linear", "m": traced.x_axis.m, "b": traced.x_axis.b},
            "y_axis": {"model": "linear", "m": traced.y_axis.m, "b": traced.y_axis.b},
            "exact_center_assertions": traced.center_assertions,
            "grid_check": served_axis_guard.payload(traced.grid_checks),
        },
        "warnings": warnings,
        "csv": str(csv_path),
        "overlay": str(overlay_path),
    }
    if traced.raster is not None:
        result["raster_figure"] = traced.raster
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("charts_json", type=Path, help="charts.json from `dsdig find`")
    parser.add_argument("--out", type=Path, default=None, help="output directory (default: alongside charts.json)")
    args = parser.parse_args()
    base_dir = args.charts_json.parent
    out_dir = args.out or base_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    errors: list[dict] = []
    for chart in json.loads(args.charts_json.read_text()):
        if chart.get("kind") != "threshold_voltage":
            continue
        crop_rel = Path(chart["crop_png"])
        print(f"digitize {chart['part']} diagram {chart['diagram']}: {crop_rel}")
        try:
            result = process_chart(chart, base_dir / crop_rel, out_dir, crop_rel.with_suffix(""))
        except Exception as exc:
            print(f"  ERROR: {exc}")
            errors.append({"part": chart.get("part"), "crop": str(crop_rel), "error": str(exc)})
            continue
        result.update({"part": chart["part"], "page": chart["page"], "diagram": chart["diagram"], "pdf": chart["pdf"]})
        results.append(result)
        reads = ", ".join(
            f"{r['t_c']:g}C={r['vth_v']:.3f}V" if r["vth_v"] is not None else f"{r['t_c']:g}C=n/c"
            for r in result["readouts"]
        )
        print(f"  {result['status']}: {reads}; table={result['table_check']['verdict']}")
    out = out_dir / "threshold_voltage_digitization.json"
    out.write_text(json.dumps({"panels": results, "errors": errors}, indent=2) + "\n")
    print(f"wrote {out}")
    if errors:
        raise SystemExit(1)
    if not results:
        print("ERROR: no threshold_voltage panels in the index - nothing digitized")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
