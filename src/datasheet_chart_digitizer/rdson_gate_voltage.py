"""Digitize MOSFET RDS(on)-versus-VGS charts, validated against the spec table.

Usage::

    dsdig digitize-rds-vgs charts.json --out DIR      # PDFs named in a find index
    dsdig digitize-rds-vgs --pdf a.pdf b.pdf --out DIR

What a panel yields (``rdson_gate_voltage.json``, one CSV and one overlay each):

  * calibrated (VGS [V], RDS(on) [mOhm]) points per curve, each curve carrying
    the Tj / ID its label binds to, or ``None`` when no label binds -- never a
    guess;
  * readouts at VGS = 2.5, 3.3 and 4.5 V per curve, interpolated ALONG the
    curve, or "not_on_chart" when that VGS is outside the curve's plotted
    span (no extrapolation, ever). Every readout is labelled a typical-curve
    reading, not a guaranteed value;
  * a tri-state validation against the datasheet's RDS(on) table rows:
    ``verified`` / ``inconsistent`` / ``not_evaluable``. A missing anchor is
    ``not_evaluable``, never a pass.

Panel status is ``ok`` only when every gate passes AND validation is
``verified``. Otherwise ``review_required`` (data served, with the reasons a
human must look at) or ``refused`` (nothing trustworthy to serve: the axes
could not be calibrated, the RDS unit could not be read, no curve was traced,
or a curve rises with VGS -- which RDS(on) does not do). This is an
unvalidated chart class: ``ok`` means the automated gates passed, not that a
human has verified the overlay.

Guard checklist (~/.claude/CLAUDE.md), answered for this plugin:
  1. Unevaluable input -> refused / review_required / not_evaluable with a
     named reason; no path defaults to ok.
  2. Monotonicity: a larger rise, more clipping, more unbound labels, a
     larger table residual can only move a panel further from ok.
  3. Preconditions: the x axis must be owned (title names VGS, not ID), linear;
     the y unit must be read from the panel itself.
  4./5. No cache.  6. Every result records its trace method (vector / raster),
     tick source (text layer / OCR), label-binding method per parameter and the
     table row it was checked against.
  7./8. Known-bad fixtures are in tests/test_rdson_gate_voltage.py.
"""

from __future__ import annotations

import argparse
import json
import os
import math
import re
from dataclasses import asdict
from pathlib import Path

import cv2
import numpy as np
import pymupdf

from .capacitance_types import PlotBox
from .crop_transform import CropTransform
from .diode_forward_voltage import TextLabel, _full_span_grid_lines
from .find_charts import PageText, group_words_into_lines, line_bbox, line_text
from .finder_types import Word
from .numeric_axis import axis_to_json
from .rdson_gate_voltage_axes import (
    CROP_DPI,
    Calibration,
    PanelRefused,
    _BoxedLabel,
    _calibrate,
    _ocr_axis_band_labels,
    _ocr_crop_labels,
    _plot_frame_px,
    _text_labels,
    _y_unit,
    ocr_plot_labels,
)
from .rdson_gate_voltage_locate import KIND, LocatedPanel, locate_panels
from .rdson_gate_voltage_report import (
    READOUT_NOTE,
    readouts,
    validate_against_table,
    write_overlay,
    write_points,
)
from .rdson_gate_voltage_traces import (
    Label,
    Trace,
    backtrack_px,
    bind_labels,
    PARAM_START_RE,
    parse_label_params,
    raster_traces,
    vector_traces,
)
from .rdson_spec_table import RdsonSpecRow, parse_rdson_spec_rows

MAX_RISE_FRACTION = 0.04
FRAME_CONTACT_PX = 2.5
FRAME_RUN_FRACTION = 0.03
MAX_BACKTRACK_PX = 3.0
MAX_MERGED_FRACTION = 0.05
MAX_PLAUSIBLE_VGS_AXIS_V = 30.0   # beyond any gate rating: a scale misread (e.g. "05" for 0.5)

# --------------------------------------------------------------------------- driver


def digitize_pdf(pdf: Path, out_dir: Path) -> tuple[list[dict], list[dict]]:
    """Locate and digitize every owned RDS(VGS) panel in ``pdf``."""
    # Tesseract's multi-threaded LSTM returned different digit readings for the
    # same tick image from run to run (measured on RQ3E180AJ, 2026-09-28);
    # one thread makes every OCR-dependent result reproducible.
    os.environ.setdefault("OMP_THREAD_LIMIT", "1")
    work = out_dir / "work"
    located, refusals, panel_text = locate_panels(pdf, work)
    spec_rows = parse_rdson_spec_rows(pdf)
    results = []
    for panel in located:
        results.append(digitize_panel(panel, panel_text[(panel.page, panel.diagram)], spec_rows, out_dir))
    return results, [r.to_json() for r in refusals]


def digitize_panel(
    panel: LocatedPanel, words: PageText, spec_rows: list[RdsonSpecRow], out_dir: Path
) -> dict:
    stem = f"p{panel.page:02d}_d{panel.diagram}"
    crop_path = out_dir / "crops" / panel.part / f"{stem}.png"
    crop_path.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(panel.pdf) as document:
        page = document[panel.page - 1]
        clip = pymupdf.Rect(panel.bbox_pt)
        pix = page.get_pixmap(dpi=CROP_DPI, clip=clip, alpha=False)
        pix.save(crop_path)
        image = cv2.imread(str(crop_path), cv2.IMREAD_COLOR)
        transform = CropTransform.for_chart({"crop_box_pt": list(panel.bbox_pt)}, image.shape)
        row: dict = {
            "kind": KIND,
            "pdf": panel.pdf,
            "part": panel.part,
            "page": panel.page,
            "diagram": panel.diagram,
            "title": panel.title,
            "locator": panel.to_json(),
            "crop_png": str(crop_path.relative_to(out_dir)),
            "crop_box_pt": list(panel.bbox_pt),
            "crop_dpi": CROP_DPI,
            "pixel_mapping": (
                "crop pixel (u, v) <-> page point (crop_box_pt.x0 + u / scale, "
                "crop_box_pt.y0 + v / scale), scale = crop_dpi / 72; "
                "x_axis/y_axis map crop pixels to values: coordinate = m * pixel + b "
                "(value = coordinate on a linear axis, 10**coordinate on log10)"
            ),
            "readout_note": READOUT_NOTE,
            "spec_rows": [r.to_json() for r in spec_rows],
        }
        try:
            _digitize(panel, page, words, image, transform, spec_rows, row, out_dir, stem)
        except PanelRefused as error:
            row.update({"status": "refused", "reasons": [str(error)], "curves": row.get("curves", [])})
            row.setdefault("validation", {"verdict": "not_evaluable", "reason": "panel refused", "anchors": []})
            write_overlay(image, row, out_dir, panel, stem)
    return row


def _panel_words(page, ocr_words: PageText) -> PageText:
    """Text-layer words (PyMuPDF) plus the locator's OCR words for this panel."""
    text_layer = [
        Word(str(w[4]), float(w[0]), float(w[1]), float(w[2]), float(w[3]))
        for w in page.get_text("words") if str(w[4]).strip()
    ]
    return PageText(ocr_words.page_num, ocr_words.width_pt, ocr_words.height_pt,
                    [*text_layer, *ocr_words.words], ocr_words.text_source)


def _digitize(panel, page, words, image, transform, spec_rows, row, out_dir, stem) -> None:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    words = _panel_words(page, words)
    ocr_labels: list[TextLabel] | None = None
    labels = _text_labels(words, transform, image.shape)

    def ocr() -> list[TextLabel]:
        nonlocal ocr_labels
        if ocr_labels is None:
            ocr_labels = _ocr_crop_labels(out_dir, panel, stem, image.shape)
        return ocr_labels

    hint = _plot_frame_px(panel, transform, gray)
    try:
        calibration = _calibrate(gray, hint, labels, "text_layer", transform, page)
    except (RuntimeError, ValueError) as first:
        try:
            calibration = _calibrate(gray, hint, labels + ocr(), "text_layer+ocr", transform, page)
        except (RuntimeError, ValueError) as second:
            try:
                bands = _ocr_axis_band_labels(gray, hint, out_dir, panel, stem)
                calibration = _calibrate(gray, hint, labels + bands, "text_layer+ocr_axis_bands", transform, page)
            except (RuntimeError, ValueError) as third:
                row["calibration_error"] = f"text layer: {first}; crop OCR: {second}; axis-band OCR: {third}"
                raise PanelRefused(f"axes_uncalibrated: {third}") from third
    row["plot_box_px"] = asdict(calibration.plot)
    row["calibration"] = {
        "x_axis": axis_to_json(calibration.x_axis),
        "y_axis": axis_to_json(calibration.y_axis),
        "tick_source": calibration.tick_source,
        "grid_binding": calibration.grid_binding,
        "x_quantity": "VGS [V]",
    }
    reasons: list[str] = []
    if calibration.x_axis.model != "linear":
        raise PanelRefused("x_axis_not_linear: the VGS axis calibrated as logarithmic")
    residual = calibration.tick_scatter_px
    row["calibration"]["tick_scatter_px_before_frame_anchoring"] = round(residual, 3)
    if residual > calibration.residual_limit_px:
        raise PanelRefused(f"axis_residual_{residual:.2f}px_exceeds_{calibration.residual_limit_px:.2f}px")
    if calibration.grid_binding != "snapped_to_full_span_grid":
        reasons.append(f"axis_ticks_not_bound_to_grid: {calibration.grid_binding}")
    vgs_max = max(t.value for t in calibration.x_axis.ticks)
    if vgs_max > MAX_PLAUSIBLE_VGS_AXIS_V:
        reasons.append(f"vgs_axis_reaches_{vgs_max:g}V_implausible_for_a_gate_drive_axis")

    unit_text, unit, scale = _y_unit(page, words, transform, calibration.plot, image, out_dir, panel, stem)
    row["calibration"]["y_quantity"] = f"RDS(on) [{unit}]" if unit else "RDS(on) [unit unread]"
    row["calibration"]["y_unit_evidence"] = unit_text
    if unit is None:
        raise PanelRefused(f"rdson_unit_unreadable: y-axis title {unit_text!r}")
    row["calibration"]["y_to_mohm"] = scale

    traces, swatches, leaders, method = _traces(page, transform, calibration, image, gray, words, labels, ocr)
    row["trace_method"] = method
    if not traces:
        raise PanelRefused(f"no_curve_traced ({method})")
    extra = None
    if method == "raster":
        extra = (ocr_labels or []) + ocr_plot_labels(gray, calibration.plot, out_dir, panel, stem)
    plot_labels = _plot_labels(words, transform, calibration.plot, extra)
    binding_notes = bind_labels(traces, plot_labels, swatches, calibration.plot, leaders, transform.scale_x)
    binding_notes.extend(_apply_page_temperature_note(traces, page))
    curves, curve_reasons, refusal = _curves(traces, calibration, scale)
    row["curves"] = curves
    row["label_binding_notes"] = binding_notes
    row["labels_seen"] = [{"text": l.text, "params": l.params} for l in plot_labels if l.params]
    reasons.extend(curve_reasons)
    if refusal:
        raise PanelRefused(refusal)
    reasons.extend(_binding_reasons(curves, plot_labels))
    row["validation"] = validate_against_table(curves, spec_rows, calibration, scale)
    if row["validation"]["verdict"] != "verified":
        reasons.append(f"validation_{row['validation']['verdict']}")
    row["status"] = "ok" if not reasons else "review_required"
    row["reasons"] = reasons
    row["points_csv"] = write_points(curves, out_dir, panel, stem)
    write_overlay(image, row, out_dir, panel, stem, calibration, unit)


# --------------------------------------------------------------------------- calibration


# --------------------------------------------------------------------------- traces


def _traces(page, transform, calibration: Calibration, image, gray, words, labels, ocr):
    traces, swatches, leaders = vector_traces(page, transform, calibration.plot)
    if traces:
        return traces, swatches, leaders, "vector"
    xs, ys = _full_span_grid_lines(gray, calibration.plot)
    boxes = _text_boxes_px(words, transform, calibration.plot)
    boxes += [
        (l.x0, l.y0, l.x1, l.y1) for l in ocr()
        if isinstance(l, _BoxedLabel) and calibration.plot.x0 < l.cx < calibration.plot.x1
        and calibration.plot.y0 < l.cy < calibration.plot.y1
    ]
    return raster_traces(image, calibration.plot, xs, ys, boxes), [], [], "raster"


def _text_boxes_px(words: PageText, transform, plot: PlotBox) -> list[tuple[float, float, float, float]]:
    boxes = []
    for w in words.words:
        x0, y0 = transform.to_px(w.x0, w.y0)
        x1, y1 = transform.to_px(w.x1, w.y1)
        if plot.x0 <= 0.5 * (x0 + x1) <= plot.x1 and plot.y0 <= 0.5 * (y0 + y1) <= plot.y1:
            boxes.append((x0, y0, x1, y1))
    return boxes


def _plot_labels(words: PageText, transform, plot: PlotBox, ocr_labels) -> list[Label]:
    """Label lines inside the plot (legends included) with their parameters.

    Words are grouped into lines in PAGE POINTS (the shared line grouper's
    tolerance is in points), and an OCR word is dropped where a word from a
    better source already covers it, so one label is never read twice.
    """
    x0p, y0p = transform.to_pt(plot.x0 - 4, plot.y0 - 4)
    x1p, y1p = transform.to_pt(plot.x1 + 4, plot.y1 + 4)

    def inside(w) -> bool:
        return x0p <= 0.5 * (w.x0 + w.x1) <= x1p and y0p <= 0.5 * (w.y0 + w.y1) <= y1p

    selected: list = [w for w in words.words if inside(w)]
    for l in ocr_labels or []:
        if not isinstance(l, _BoxedLabel):
            continue
        ax, ay = transform.to_pt(l.x0, l.y0)
        bx, by = transform.to_pt(l.x1, l.y1)
        word = Word(l.text, ax, ay, bx, by)
        if inside(word):
            selected.append(word)
    selected = _drop_overlapping_duplicates(selected)
    out = []
    for line in group_words_into_lines(selected):
        for segment in (piece for gap_part in _split_gaps(line) for piece in _split_parameters(gap_part)):
            text = line_text(segment)
            ax, ay, bx, by = line_bbox(segment)
            px0, py0 = transform.to_px(ax, ay)
            px1, py1 = transform.to_px(bx, by)
            out.append(Label(text, px0, py0, px1, py1, parse_label_params(text)))
    return out


def _drop_overlapping_duplicates(words: list) -> list:
    """Keep the first of two words whose boxes overlap by more than 40 %."""
    kept: list = []
    for word in words:
        area = max(1e-6, (word.x1 - word.x0) * (word.y1 - word.y0))
        clash = False
        for other in kept:
            ix = max(0.0, min(word.x1, other.x1) - max(word.x0, other.x0))
            iy = max(0.0, min(word.y1, other.y1) - max(word.y0, other.y0))
            other_area = max(1e-6, (other.x1 - other.x0) * (other.y1 - other.y0))
            if ix * iy > 0.4 * min(area, other_area):
                clash = True
                break
        if not clash:
            kept.append(word)
    return kept


_PAGE_TEMPERATURE_NOTE_RE = re.compile(
    r"T\s*[JCA]?\s*=\s*([+-]?\d{1,3})\s*(?:°|º|o)?\s*C\s*,?\s*unless\s+otherwise\s+(?:noted|specified|stated)",
    re.IGNORECASE,
)


def _apply_page_temperature_note(traces: list[Trace], page) -> list[str]:
    """Use a page-level "TC = 25°C unless otherwise noted" when NO curve names a temperature.

    Only when exactly one such note is on the page and no label on the panel
    printed a temperature at all; a panel that prints its own temperatures
    never falls back to the page note.
    """
    if any("tj_c" in trace.params for trace in traces):
        return []
    notes = {float(m.group(1)) for m in _PAGE_TEMPERATURE_NOTE_RE.finditer(" ".join(page.get_text("text").split()))}
    if len(notes) != 1:
        return []
    value = notes.pop()
    for trace in traces:
        trace.params["tj_c"] = value
        trace.binding["tj_c"] = "page_note_unless_otherwise_noted"
    return [f"tj_c={value:g}_from_page_note"]


def _split_parameters(words: list) -> list[list]:
    """Split one text line where a second "T..=" / "I..=" assignment begins.

    Two curve labels printed on one baseline ("TJ=100C   TJ=125C") must stay
    two labels; parsed as one they name two temperatures and bind to nothing.
    """
    parts: list[list] = [[]]
    for word in words:
        if parts[-1] and PARAM_START_RE.match(word.text) and parse_label_params(line_text(parts[-1])):
            parts.append([])
        parts[-1].append(word)
    return [part for part in parts if part]


def _split_gaps(line):
    parts, current = [], [line[0]]
    for word in line[1:]:
        if word.x0 - current[-1].x1 > 3.0 * max(1.0, current[-1].y1 - current[-1].y0):
            parts.append(current)
            current = [word]
        else:
            current.append(word)
    parts.append(current)
    return parts


# --------------------------------------------------------------------------- curves


def _curves(traces: list[Trace], calibration: Calibration, scale: float):
    plot = calibration.plot
    reasons: list[str] = []
    curves: list[dict] = []
    y_ticks = [t.value * scale for t in calibration.y_axis.ticks]
    log_y = calibration.y_axis.model == "log10"
    y_span = (math.log10(max(y_ticks)) - math.log10(min(y_ticks))) if log_y else (max(y_ticks) - min(y_ticks))
    x_values = [t.value for t in calibration.x_axis.ticks]
    x_span = max(x_values) - min(x_values)
    refusal = None
    ordered = sorted(traces, key=lambda t: float(np.median([p[1] for p in t.points_px])))
    for index, trace in enumerate(ordered):
        at_frame = [
            (x, y) for x, y in trace.points_px
            if y <= plot.y0 + FRAME_CONTACT_PX or y >= plot.y1 - FRAME_CONTACT_PX
            or x <= plot.x0 + FRAME_CONTACT_PX or x >= plot.x1 - FRAME_CONTACT_PX
        ]
        interior = [p for p in trace.points_px if p not in set(at_frame)]
        runs_along = _longest_frame_run(at_frame, plot)
        points = [
            (calibration.x_axis.value(x), calibration.y_axis.value(y) * scale, x, y)
            for x, y in interior
        ]
        points.sort(key=lambda p: p[0])
        curve = {
            "curve_index": index,
            "trace_method": trace.method,
            "tj_c": trace.params.get("tj_c"),
            "id_a": trace.params.get("id_a"),
            "parameter_binding": dict(trace.binding),
            "label": _curve_label(trace),
            "stroke_style": list(trace.style) if trace.style else None,
            "n_points": len(points),
            "frame_contact_points_dropped": len(at_frame),
            "points": [[round(v, 5), round(r, 5)] for v, r, _x, _y in points],
            "points_px": [[round(x, 2), round(y, 2)] for _v, _r, x, y in points],
        }
        if not points:
            reasons.append(f"curve_{index}_has_no_interior_points")
            curves.append(curve)
            continue
        curve["vgs_range_v"] = [round(points[0][0], 4), round(points[-1][0], 4)]
        if runs_along > FRAME_RUN_FRACTION * (plot.x1 - plot.x0):
            reasons.append(f"curve_{index}_runs_along_the_frame_{runs_along:.0f}px_(clipped)")
        if trace.method == "vector" and backtrack_px(trace.points_px) > MAX_BACKTRACK_PX:
            reasons.append(f"curve_{index}_not_single_valued_in_vgs")
        if trace.merged_columns > MAX_MERGED_FRACTION * (plot.x1 - plot.x0):
            reasons.append(f"curve_{index}_shares_{trace.merged_columns}_columns_with_another_curve")
        rise = _max_rise([p[1] for p in points], log_y)
        curve["max_rise_fraction_of_axis"] = round(rise / y_span, 4) if y_span else None
        if y_span and rise / y_span > MAX_RISE_FRACTION:
            refusal = refusal or (
                f"curve_{index}_rdson_rises_with_vgs_by_{rise / y_span:.1%}_of_axis "
                "(RDS(on) does not increase with VGS: the trace is not a curve of this chart)"
            )
        curve["readouts"] = readouts(points, log_y, x_span)
        curves.append(curve)
    return curves, reasons, refusal


def _longest_frame_run(points, plot: PlotBox) -> float:
    """Longest horizontal stretch of a trace hugging the top or bottom frame rail."""
    best = 0.0
    for rail in (plot.y0, plot.y1):
        xs = sorted(x for x, y in points if abs(y - rail) <= FRAME_CONTACT_PX)
        start = prev = None
        for x in xs:
            if prev is None or x - prev > 3:
                start = x
            prev = x
            best = max(best, prev - start)
    return best


def _max_rise(rds: list[float], log_y: bool) -> float:
    values = [math.log10(r) for r in rds if r > 0] if log_y else rds
    worst, running = 0.0, math.inf
    for value in values:
        running = min(running, value)
        worst = max(worst, value - running)
    return worst


def _curve_label(trace: Trace) -> str:
    parts = []
    if "tj_c" in trace.params:
        tj = trace.params["tj_c"]
        parts.append(f"Tj={tj:g}C" if tj is not None else "Tj=unknown")
    if "id_a" in trace.params:
        current = trace.params["id_a"]
        parts.append(f"ID={current:g}A" if current is not None else "ID=unknown")
    return ", ".join(parts) or "unlabelled"


def _binding_reasons(curves: list[dict], labels: list[Label]) -> list[str]:
    reasons = []
    keys = {k for c in curves for k in ("tj_c", "id_a") if k in c["parameter_binding"]}
    if len(curves) > 1 and not keys:
        reasons.append("curve_parameters_unlabelled: several curves, no Tj/ID label binds to any")
    for curve in curves:
        for key in keys:
            if curve.get(key) is None:
                reasons.append(f"curve_{curve['curve_index']}_{key}_unknown ({curve['parameter_binding'].get(key, 'no label')})")
    return reasons


# --------------------------------------------------------------------------- CLI


def _pdfs_from_index(charts_json: Path) -> list[Path]:
    charts = json.loads(charts_json.read_text())
    seen: list[Path] = []
    for chart in charts:
        path = Path(chart["pdf"])
        if path not in seen:
            seen.append(path)
    return seen


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Digitize MOSFET RDS(on)-versus-VGS charts")
    parser.add_argument("charts_json", nargs="?", type=Path, help="charts.json from `dsdig find` (its PDFs are scanned)")
    parser.add_argument("--pdf", nargs="+", type=Path, default=[], help="datasheet PDFs to scan directly")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    pdfs = list(args.pdf)
    if args.charts_json is not None:
        pdfs.extend(p for p in _pdfs_from_index(args.charts_json) if p not in pdfs)
    if not pdfs:
        parser.error("give a charts.json or --pdf")
    args.out.mkdir(parents=True, exist_ok=True)
    panels, refusals, errors = [], [], []
    for pdf in pdfs:
        print(f"scan {pdf}")
        try:
            results, refused = digitize_pdf(pdf, args.out)
        except Exception as error:  # noqa: BLE001 - serialized, not swallowed
            errors.append({"pdf": str(pdf), "error": f"{type(error).__name__}: {error}"})
            print(f"  ERROR {error}")
            continue
        panels.extend(results)
        refusals.extend(refused)
        for result in results:
            print(f"  p{result['page']} fig {result['diagram']}: {result['status']} "
                  f"validation={result.get('validation', {}).get('verdict')} overlay: {args.out / result['overlay']}")
        for refusal in refused:
            print(f"  located-but-refused p{refusal['page']} fig {refusal['diagram']}: {refusal['reason']}")
    manifest = {"kind": KIND, "panels": panels, "locator_refusals": refusals, "errors": errors}
    path = args.out / "rdson_gate_voltage.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"wrote {path}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
