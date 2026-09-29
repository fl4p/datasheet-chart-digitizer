#!/usr/bin/env python3
"""Re-trace the transfer-review25 parts whose human-verified GT was wrong.

Approved by Fab 2026-09-29.  Every axis is calibrated from printed labels
seated on the chart's own gridlines (``transfer_retrace.fit_review_axis``),
never from an assumed plot-box corner; vector charts are traced from their PDF
stroke paths and bound to temperatures by legend colour; raster charts reuse
the human-reviewed transfer-review25 tracing decisions (seeds, anchor repairs,
identity crossings) on a fresh 180 dpi render of the same page.

usage: .venv/bin/python tools/transfer_review/generate_transfer_retrace.py --output <dir>
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pymupdf
from PIL import Image

from datasheet_chart_digitizer.capacitance_axis import (
    _horizontal_gridline_candidates,
    _vertical_gridline_candidates,
)
from datasheet_chart_digitizer.capacitance_types import PlotBox
from datasheet_chart_digitizer.transfer_retrace import (
    ReviewAxis,
    assert_ticks_hit,
    bind_strokes_to_legend,
    fit_review_axis,
    ink_label_centers,
    legend_temperature_colors,
    vector_curve_strokes,
    vector_gridlines,
)
from datasheet_chart_digitizer.transfer_review import sustained_collapse_spans

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
import retrace_render as render  # noqa: E402

DPI = 180
S = DPI / 72.0
DATASHEETS = Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets")
OLD_PACKET = Path("/Users/fab/dev/pv/ee/dsdig-verify-backlog/transfer-review25-2026-07-17")
TICK_TOLERANCE_PX = 1.5


def _load_review25():
    script = HERE / "generate_transfer_review25.py"
    spec = importlib.util.spec_from_file_location("generate_transfer_review25", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@dataclass(frozen=True)
class Part:
    stem: str
    part: str
    manufacturer: str
    pdf: str
    page: int
    kind: str  # "vector" | "raster"
    crop_margin: tuple[int, int, int, int]  # left, top, right, bottom px around the frame
    frame_hint_pt: tuple[float, float, float, float] | None = None
    # page px offset of the transfer-review25 source raster in this 180 dpi
    # render (registered by FFT correlation, mean |diff| <= 1.3 grey levels);
    # raster only: the reviewer-read label values and the page-px bands their
    # glyphs occupy
    old_offset: tuple[int, int] | None = None
    x_values: tuple[float, ...] = ()
    y_values: tuple[float, ...] = ()
    x_band: tuple[int, int, int, int] | None = None
    y_band: tuple[int, int, int, int] | None = None
    x_gap: int = 12
    y_gap: int = 6
    temperatures_by_curve: tuple[float, ...] = ()
    defect: str = ""


PARTS = [
    Part(
        "03_crmicro_CRSS052N08N", "CRSS052N08N", "CR Micro", "crmicro/CRSS052N08N.pdf", 4, "raster",
        (72, 75, 40, 88), old_offset=(486, 200),
        x_values=tuple(0.5 * k for k in range(11)), y_values=(100, 80, 60, 40, 20, 0),
        x_band=(815, 763, 1400, 782), y_band=(790, 355, 832, 770),
        defect="x frame is 0.0..5.4 V (54 x 0.1 V minor intervals), recorded as 0..5.5 V with the box 4 px left of the 0.0 V line",
    ),
    Part(
        "04_crmicro_CRST065N08N", "CRST065N08N", "CR Micro", "crmicro/CRST065N08N.pdf", 4, "raster",
        (80, 75, 40, 70), old_offset=(715, 197),
        x_values=tuple(float(v) for v in range(3, 11)), y_values=(300, 250, 200, 150, 100, 50, 0),
        x_band=(815, 764, 1390, 784), y_band=(775, 360, 832, 770),
        defect="recorded plot box bottom 762 px sits 7.5 px below the 0 A frame line (754.5 px)",
    ),
    Part(
        "06_epc_space_EPC7018GSH", "EPC7018GSH", "EPC Space", "epc_space/EPC7018GSH.pdf", 4, "vector",
        (100, 60, 30, 70), frame_hint_pt=(61.97, 318.04, 281.98, 485.04), old_offset=(33, 616),
        defect="x labels 2.0/2.5 contradict the uniform 0.5 V grid; recorded linear 2..5 V over a 0.5..5.0 V frame; y frame ends at 345 A, not 350 A",
    ),
    Part(
        "07_epc_space_FBG10N30BC", "FBG10N30BC", "EPC Space", "epc_space/FBG10N30BC.pdf", 4, "vector",
        (100, 40, 30, 75), frame_hint_pt=(101.23, 532.7, 294.73, 688.18), old_offset=(132, 1361),
        defect="review raster cut at the 100 A gridline; the frame runs to 120 A and the legend rows 25/125 C were cut off",
    ),
    Part(
        "10_hxy_IPD65R380E6ATMA1-HXY", "IPD65R380E6ATMA1-HXY", "HXY", "hxy/IPD65R380E6ATMA1-HXY.pdf", 4, "raster",
        (80, 50, 50, 70), old_offset=(0, 1094),
        x_values=(0, 4, 8, 12), y_values=(20, 15, 10, 5, 0),
        x_band=(200, 1740, 640, 1762), y_band=(150, 1350, 212, 1745),
        defect="printed y labels are pasted patches off their gridlines by -9..+14 px; the 12 V gridline is missing",
    ),
    Part(
        "18_panjit_PSMB055N08NS1_R2_00601", "PSMB055N08NS1_R2_00601", "Panjit", "panjit/PSMB055N08NS1_R2_00601.pdf", 3,
        "vector", (100, 30, 40, 70), frame_hint_pt=(346.71, 125.98, 547.35, 267.66), old_offset=(747, 300),
        defect="review raster cut at the 300 A gridline; the frame runs to 350 A where the -40/25 C curves and legend labels continue",
    ),
]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _render_page(pdf: Path, page_number: int) -> np.ndarray:
    with pymupdf.open(pdf) as document:
        pixmap = document[page_number - 1].get_pixmap(matrix=pymupdf.Matrix(S, S), alpha=False)
        return np.frombuffer(pixmap.samples, np.uint8).reshape(pixmap.height, pixmap.width, 3).copy()


def _drawn_frame(page, hint: tuple[float, float, float, float]) -> pymupdf.Rect:
    """The panel's own drawn frame rectangle nearest the hint (fail closed)."""
    target = pymupdf.Rect(hint)
    best = None
    for drawing in page.get_drawings():
        for item in drawing.get("items", []):
            if item[0] != "re":
                continue
            rect = item[1]
            error = max(abs(rect.x0 - target.x0), abs(rect.y0 - target.y0), abs(rect.x1 - target.x1), abs(rect.y1 - target.y1))
            if error <= 1.0 and (best is None or error < best[0]):
                best = (error, pymupdf.Rect(rect))
    if best is None:
        raise RuntimeError(f"no drawn frame rectangle within 1 pt of {hint}")
    return best[1]


def _numeric_words(page, rect: pymupdf.Rect, along: str) -> list[tuple[str, float, float]]:
    out = []
    for word in page.get_text("words"):
        text = word[4].strip()
        try:
            value = float(text)
        except ValueError:
            continue
        cx, cy = (word[0] + word[2]) / 2, (word[1] + word[3]) / 2
        if rect.contains(pymupdf.Point(cx, cy)):
            out.append((text, value, S * (cx if along == "x" else cy)))
    return out


def _arc_resample(points_px: list[tuple[float, float]], step: float = 1.5) -> list[tuple[float, float]]:
    out = [points_px[0]]
    for (x0, y0), (x1, y1) in zip(points_px, points_px[1:]):
        pieces = max(1, int(np.ceil(np.hypot(x1 - x0, y1 - y0) / step)))
        for k in range(1, pieces + 1):
            t = k / pieces
            out.append((x0 + t * (x1 - x0), y0 + t * (y1 - y0)))
    return out


def _row_trace(points_px: list[tuple[float, float]], frame: PlotBox):
    """Integer-row positions of a vector curve for the collapse detector (no smoothing)."""
    rising = [(x, y) for x, y in points_px if y < frame.y1 - 2]
    by_row: dict[int, list[float]] = {}
    for x, y in _arc_resample(rising, 0.5):
        by_row.setdefault(int(round(y)), []).append(x)
    return SimpleNamespace(pixels=[(int(round(np.mean(v))), y) for y, v in sorted(by_row.items())])


def _vector_part(part: Part, page, rgb: np.ndarray):
    frame_pt = _drawn_frame(page, part.frame_hint_pt)  # type: ignore[arg-type]
    gx, gy = vector_gridlines(page, frame_pt)
    x_labels = _numeric_words(page, pymupdf.Rect(frame_pt.x0 - 10, frame_pt.y1, frame_pt.x1 + 10, frame_pt.y1 + 14), "x")
    y_labels = _numeric_words(page, pymupdf.Rect(frame_pt.x0 - 30, frame_pt.y0 - 8, frame_pt.x0, frame_pt.y1 + 8), "y")
    x_axis = fit_review_axis(f"{part.part}.Vgs", x_labels, [g * S for g in gx], (frame_pt.x0 * S, frame_pt.x1 * S))
    y_axis = fit_review_axis(f"{part.part}.Id", y_labels, [g * S for g in gy], (frame_pt.y0 * S, frame_pt.y1 * S))
    strokes = vector_curve_strokes(page, frame_pt)
    legend = legend_temperature_colors(page, frame_pt + (-2, -12, 2, 2))
    bound = bind_strokes_to_legend(strokes, legend)
    curves = []
    for temperature, stroke in sorted(bound.items(), key=lambda item: item[0]):
        points_px = [(x * S, y * S) for x, y in stroke.points_pt]
        curves.append(
            {
                "temperature_c": temperature,
                "points_px": _arc_resample(points_px),
                "stroke_rgb": [round(c, 3) for c in stroke.color],
                "legend_rgb": [round(c, 3) for c in legend[temperature]],
            }
        )
    frame = PlotBox(*(int(round(v * S)) for v in (frame_pt.x0, frame_pt.y0, frame_pt.x1, frame_pt.y1)))
    collapse_input = [_row_trace(c["points_px"], frame) for c in curves]
    _spans, collapsed_rows = sustained_collapse_spans(collapse_input)  # type: ignore[arg-type]
    for curve, rows in zip(curves, collapsed_rows):
        curve["collapsed_rows"] = rows
    provenance = {
        "kind": "pdf-vector-paths",
        "frame_pt": [round(v, 3) for v in (frame_pt.x0, frame_pt.y0, frame_pt.x1, frame_pt.y1)],
        "temperature_assignment": "legend-colour-bound (decisive nearest legend colour, one-to-one)",
        "label_source": "pdf text layer (word centres)",
        "gridline_source": "pdf vector rules spanning the frame",
    }
    return frame, x_axis, y_axis, curves, provenance


def _raster_part(part: Part, rgb: np.ndarray, review25, pdf: Path):
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    sample = next(s for s in review25.SAMPLES if s.stem == part.stem)
    ox, oy = part.old_offset  # type: ignore[misc]
    hint = PlotBox(sample.plot[0] + ox, sample.plot[1] + oy, sample.plot[2] + ox, sample.plot[3] + oy)
    vertical = [v for v in _vertical_gridline_candidates(bgr, hint) if hint.x0 - 12 <= v <= hint.x1 + 12]
    horizontal = [v for v in _horizontal_gridline_candidates(bgr, hint) if hint.y0 - 12 <= v <= hint.y1 + 12]
    frame_x = (min(vertical), max(vertical))
    frame_y = (min(horizontal), max(horizontal))
    source_px = _source_raster_px(pdf, part.page, frame_x, frame_y)
    axes = []
    for name, values, band, gap, along, grid, frame_span in (
        ("Vgs", part.x_values, part.x_band, part.x_gap, "x", vertical, frame_x),
        ("Id", part.y_values, part.y_band, part.y_gap, "y", horizontal, frame_y),
    ):
        centres = ink_label_centers(gray, band, along, min_gap_px=gap)  # type: ignore[arg-type]
        if len(centres) != len(values):
            raise RuntimeError(f"{part.part}.{name}: {len(centres)} label glyph groups for {len(values)} read values")
        ordered_values = sorted(values) if along == "x" else list(values)
        labels = [(f"{v:g}", float(v), c) for v, c in zip(ordered_values, centres)]
        axes.append((name, labels, grid, frame_span))
    fitted: list[object] = []
    refusal = None
    for name, labels, grid, frame_span in axes:
        try:
            fitted.append(fit_review_axis(f"{part.part}.{name}", labels, grid, frame_span, source_px=source_px))
        except RuntimeError as exc:
            refusal = (refusal + "; " if refusal else "") + str(exc)
            fitted.append(SimpleNamespace(error=str(exc), labels=labels, grid=grid, frame=frame_span))
    frame = PlotBox(*(int(round(v)) for v in (frame_x[0], frame_y[0], frame_x[1], frame_y[1])))
    provenance = {
        "kind": "raster-branch-tracking (transfer-review25 tracing decisions on a fresh 180 dpi render)",
        "source_raster_px_per_render_px": round(source_px, 3),
        "temperature_assignment": "manual-branch-label-mapped (unchanged from transfer-review25)",
        "label_source": "values read by the reviewer; positions measured from glyph ink (ink_label_centers)",
        "gridline_source": "raster rule detector (capacitance_axis gridline candidates)",
    }
    if refusal is not None:
        return frame, fitted[0], fitted[1], [], {**provenance, "refused": refusal}
    crop = rgb[oy:, ox:]
    local_frame = PlotBox(frame.x0 - ox, frame.y0 - oy, frame.x1 - ox, frame.y1 - oy)
    local_sample = replace(sample, plot=(local_frame.x0, local_frame.y0, local_frame.x1, local_frame.y1))
    traces = review25.trace_sample(local_sample, np.ascontiguousarray(crop), _crop_png(crop, part))
    spans, collapsed_rows = sustained_collapse_spans(traces)
    curves = []
    for index, trace in enumerate(traces):
        curves.append(
            {
                "temperature_c": float(review25.TEMPERATURE_BY_CURVE[sample.part][index]),
                "points_px": [(x + ox, y + oy) for x, y in trace.pixels],
                "collapsed_rows": {y + oy for y in collapsed_rows[index]},
            }
        )
    return frame, fitted[0], fitted[1], curves, provenance


def _crop_png(crop: np.ndarray, part: Part) -> Path:
    path = Path("/tmp") / f"retrace-{part.stem}.png"
    Image.fromarray(crop).save(path)
    return path


def _source_raster_px(pdf: Path, page_number: int, frame_x, frame_y) -> float:
    """Render px per embedded-image px for the image under the frame (1.0 if none)."""
    with pymupdf.open(pdf) as document:
        page = document[page_number - 1]
        centre = pymupdf.Point((frame_x[0] + frame_x[1]) / 2 / S, (frame_y[0] + frame_y[1]) / 2 / S)
        for info in page.get_image_info():
            bbox = pymupdf.Rect(info["bbox"])
            if bbox.contains(centre) and info["width"]:
                return bbox.width * S / info["width"]
    return 1.0


def _served_points(curves, x_axis: ReviewAxis, y_axis: ReviewAxis):
    rows = []
    for index, curve in enumerate(curves, 1):
        for x, y in curve["points_px"]:
            vgs, current = x_axis.value(x), y_axis.value(y)
            served = x_axis.is_served(x) and y_axis.is_served(y)
            rows.append(
                {
                    "curve_id": f"curve_{index}",
                    "temperature_c": curve["temperature_c"],
                    "Vgs_V": vgs,
                    "Id_A": current,
                    "collapsed": int(int(round(y)) in curve["collapsed_rows"]),
                    "served": served,
                    "px": (x, y),
                }
            )
    return rows


def _old_points(stem: str) -> dict[float, np.ndarray]:
    grouped: dict[float, list[tuple[float, float]]] = {}
    with (OLD_PACKET / "points" / f"{stem}.csv").open() as handle:
        for row in csv.DictReader(handle):
            grouped.setdefault(float(row["temperature_c"]), []).append((float(row["Vgs_V"]), float(row["Id_A"])))
    return {t: np.array(v) for t, v in grouped.items()}


def _interp_unique(x: np.ndarray, y: np.ndarray, at: np.ndarray) -> np.ndarray:
    order = np.argsort(x, kind="stable")
    xs, ys = x[order], y[order]
    unique, inverse = np.unique(xs, return_inverse=True)
    means = np.bincount(inverse, ys) / np.bincount(inverse)
    return np.interp(at, unique, means)


def _delta(stem: str, rows, y_full_scale: float) -> dict[str, object]:
    """Old-vs-new GT difference over the shared range, per temperature."""
    old = _old_points(stem)
    result = {}
    for temperature, old_points in sorted(old.items()):
        new = np.array([(r["Vgs_V"], r["Id_A"]) for r in rows if r["served"] and r["temperature_c"] == temperature])
        if not len(new):
            result[f"{temperature:g}"] = {"shared": False}
            continue
        floor = 0.02 * y_full_scale
        id_lo = max(old_points[:, 1].min(), new[:, 1].min(), floor)
        id_hi = min(old_points[:, 1].max(), new[:, 1].max())
        vgs_lo = max(old_points[:, 0].min(), new[:, 0].min())
        vgs_hi = min(old_points[:, 0].max(), new[:, 0].max())
        entry: dict[str, object] = {"shared_id_a": [round(id_lo, 3), round(id_hi, 3)], "shared_vgs_v": [round(vgs_lo, 4), round(vgs_hi, 4)]}
        if id_hi > id_lo:
            grid = np.linspace(id_lo, id_hi, 400)
            dv = _interp_unique(new[:, 1], new[:, 0], grid) - _interp_unique(old_points[:, 1], old_points[:, 0], grid)
            k = int(np.argmax(np.abs(dv)))
            entry["max_abs_dVgs_v"] = round(float(abs(dv[k])), 4)
            entry["at_id_a"] = round(float(grid[k]), 2)
            entry["signed_dVgs_v_new_minus_old"] = round(float(dv[k]), 4)
        if vgs_hi > vgs_lo:
            grid = np.linspace(vgs_lo, vgs_hi, 400)
            di = _interp_unique(new[:, 0], new[:, 1], grid) - _interp_unique(old_points[:, 0], old_points[:, 1], grid)
            k = int(np.argmax(np.abs(di)))
            entry["max_abs_dId_a"] = round(float(abs(di[k])), 3)
            entry["at_vgs_v"] = round(float(grid[k]), 4)
            entry["signed_dId_a_new_minus_old"] = round(float(di[k]), 3)
        entry["old_id_max_a"] = round(float(old_points[:, 1].max()), 2)
        entry["new_id_max_a"] = round(float(new[:, 1].max()), 2)
        result[f"{temperature:g}"] = entry
    return result


def _defect_evidence(part: Part, review25, rgb, frame: PlotBox, x_axis: ReviewAxis, y_axis: ReviewAxis, output: Path) -> dict:
    """Old transfer-review25 calibration vs the printed ticks, with 4x evidence crops."""
    sample = next(s for s in review25.SAMPLES if s.stem == part.stem)
    ox, oy = part.old_offset  # type: ignore[misc]
    bx0, by0, bx1, by1 = (sample.plot[0] + ox, sample.plot[1] + oy, sample.plot[2] + ox, sample.plot[3] + oy)
    vmin, vmax, imin, imax, _scale = sample.axis
    old_source = Image.open(OLD_PACKET / "sources" / f"{part.stem}.webp")
    old_extent = [ox, oy, ox + old_source.width, oy + old_source.height]
    report: dict[str, object] = {
        "old_plot_box_page_px": [bx0, by0, bx1, by1],
        "old_axis": {"Vgs_V": [vmin, vmax], "Id_A": [imin, imax]},
        "old_source_extent_page_px": old_extent,
        "new_frame_page_px": [frame.x0, frame.y0, frame.x1, frame.y1],
        "new_frame_values": {"Vgs_V": [round(v, 4) for v in x_axis.frame_values], "Id_A": [round(v, 3) for v in y_axis.frame_values]},
        "ticks": {},
        "crops": [],
    }
    for name, axis, lo_px, hi_px, lo_v, hi_v, is_x in (
        ("x", x_axis, bx0, bx1, vmin, vmax, True),
        ("y", y_axis, by1, by0, imin, imax, False),
    ):
        rows = []
        for tick in sorted(axis.inliers, key=lambda t: t.value):
            old_px = lo_px + (tick.value - lo_v) / (hi_v - lo_v) * (hi_px - lo_px)
            inside_old = min(lo_px, hi_px) - 0.5 <= old_px <= max(lo_px, hi_px) + 0.5
            rows.append(
                {
                    "value": tick.value,
                    "gridline_px": round(tick.grid_px, 2),
                    "old_px": round(old_px, 2),
                    "old_error_px": round(old_px - tick.grid_px, 2),
                    "old_value_at_gridline": round(lo_v + (tick.grid_px - lo_px) / (hi_px - lo_px) * (hi_v - lo_v), 4),
                    "inside_old_range": inside_old,
                    "new_error_px": round(axis.pixel(tick.value) - tick.grid_px, 2),
                }
            )
        report["ticks"][name] = rows  # type: ignore[index]
        worst = max((r for r in rows if r["inside_old_range"]), key=lambda r: abs(r["old_error_px"]), default=None)
        if worst is None or abs(worst["old_error_px"]) < 1.0:
            continue
        g, o = worst["gridline_px"], worst["old_px"]
        if is_x:
            center, half = ((g + o) / 2, frame.y1 + 6), (max(60, int(abs(g - o) / 2) + 40), 30)
            marks = [(g, frame.y1, (0, 150, 0), "v"), (o, frame.y1, render.OLD, "v")]
        else:
            center, half = ((frame.x0 + 5), (g + o) / 2), (55, max(30, int(abs(g - o) / 2) + 25))
            marks = [(frame.x0, g, (0, 150, 0), "h"), (frame.x0, o, render.OLD, "h")]
        label = (
            f"{part.part} {'Vgs' if is_x else 'Id'}={worst['value']:g}: gridline {g}px (green), "
            f"old GT calibration {o}px (red), miss {worst['old_error_px']:+.2f}px\n"
            f"old GT reads this gridline as {worst['old_value_at_gridline']:g}; new served calibration err {worst['new_error_px']:+.2f}px"
        )
        path = render.defect_zoom(rgb, center, half, marks, label, output / "evidence" / f"{part.stem}.{name}-tick.webp")
        report["crops"].append(str(path.relative_to(output)))  # type: ignore[union-attr]
    for name, axis, is_x in (("x", x_axis, True), ("y", y_axis, False)):
        if not axis.conflicts:
            continue
        grid = list(axis.gridlines_px)
        if is_x:
            center, half = ((frame.x0 + frame.x1) / 2, frame.y1 + 4), ((frame.x1 - frame.x0) // 2 + 20, 40)
            marks = [(g, 0, (0, 150, 0), "v") for g in grid] + [(c.label_px, 0, render.OLD, "v") for c in axis.conflicts]
        else:
            center, half = (frame.x0 - 10, (frame.y0 + frame.y1) / 2), (40, (frame.y1 - frame.y0) // 2 + 20)
            marks = [(0, g, (0, 150, 0), "h") for g in grid] + [(0, c.label_px, render.OLD, "h") for c in axis.conflicts]
        label = f"{part.part} {name}-axis labels: gridlines green, NOT-consumed label centres red\n" + "; ".join(
            f"'{c.text}' {c.reason[:60]}" for c in axis.conflicts
        )
        path = render.defect_zoom(rgb, center, half, marks, label[:260], output / "evidence" / f"{part.stem}.{name}-labels.webp", zoom=2)
        report["crops"].append(str(path.relative_to(output)))  # type: ignore[union-attr]
    cut = None
    if old_extent[1] > frame.y0 + 1:
        cut = old_extent[1]
        label = (
            f"{part.part}: the old review raster STARTED at page y={old_extent[1]}px (red dashes), below the "
            f"chart frame top y={frame.y0}px (green) = {y_axis.frame_values[1]:.4g} A"
        )
    elif by0 > frame.y0 + 2:
        cut = by0
        label = (
            f"{part.part}: old GT plot-box top y={by0}px (red dashes, = {imax:g} A) vs the chart frame top "
            f"y={frame.y0}px (green) = {y_axis.frame_values[1]:.4g} A; the curves continue above the old box"
        )
    if cut is not None:
        path = render.defect_zoom(
            rgb, ((frame.x0 + frame.x1) / 2, frame.y0 + 10), ((frame.x1 - frame.x0) // 2 + 60, 60),
            [(frame.x0, cut, render.OLD, "h"), (frame.x0, frame.y0, (0, 150, 0), "h")], label,
            output / "evidence" / f"{part.stem}.truncation.webp", zoom=2,
        )
        report["crops"].append(str(path.relative_to(output)))  # type: ignore[union-attr]
    return report


def _trace_px_delta(part: Part, review25, curves, frame: PlotBox) -> dict:
    """Pixel-space distance between the OLD traced centreline and the new one.

    Old points are mapped back to page pixels through the OLD calibration, so
    this isolates tracing differences from the calibration change.
    """
    sample = next(s for s in review25.SAMPLES if s.stem == part.stem)
    ox, oy = part.old_offset  # type: ignore[misc]
    bx0, by0, bx1, by1 = (sample.plot[0] + ox, sample.plot[1] + oy, sample.plot[2] + ox, sample.plot[3] + oy)
    vmin, vmax, imin, imax, _scale = sample.axis
    out = {}
    for temperature, points in _old_points(part.stem).items():
        curve = next((c for c in curves if c["temperature_c"] == temperature), None)
        if curve is None:
            continue
        old_rows: dict[int, list[float]] = {}
        for vgs, current in points:
            x = bx0 + (vgs - vmin) / (vmax - vmin) * (bx1 - bx0)
            y = by1 - (current - imin) / (imax - imin) * (by1 - by0)
            old_rows.setdefault(int(round(y)), []).append(x)
        new_rows: dict[int, list[float]] = {}
        for x, y in curve["points_px"]:
            new_rows.setdefault(int(round(y)), []).append(x)
        common = [y for y in sorted(set(old_rows) & set(new_rows)) if y < frame.y1 - 2]
        if not common:
            continue
        dx = np.array([np.mean(new_rows[y]) - np.mean(old_rows[y]) for y in common])
        k = int(np.argmax(np.abs(dx)))
        out[f"{temperature:g}"] = {
            "rows": len(common),
            "median_abs_dx_px": round(float(np.median(np.abs(dx))), 2),
            "p95_abs_dx_px": round(float(np.quantile(np.abs(dx), 0.95)), 2),
            "max_abs_dx_px": round(float(abs(dx[k])), 2),
            "at_row_px": common[k],
        }
    return out


def _git_head() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, check=True).stdout.strip()


def _write_points(path: Path, rows) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["curve_id", "temperature_c", "Vgs_V", "Id_A", "collapsed"])
        for row in rows:
            if row["served"]:
                writer.writerow([row["curve_id"], f"{row['temperature_c']:g}", f"{row['Vgs_V']:.6g}", f"{row['Id_A']:.6g}", row["collapsed"]])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    for sub in ("sources", "overlays", "points", "crops"):
        (output / sub).mkdir(parents=True, exist_ok=True)
    review25 = _load_review25()
    manifest = []
    for part in PARTS:
        pdf = DATASHEETS / part.pdf
        rgb = _render_page(pdf, part.page)
        with pymupdf.open(pdf) as document:
            page = document[part.page - 1]
            if part.kind == "vector":
                frame, x_axis, y_axis, curves, provenance = _vector_part(part, page, rgb)
            else:
                frame, x_axis, y_axis, curves, provenance = _raster_part(part, rgb, review25, pdf)
        L, T, R, B = part.crop_margin
        crop_box = (max(0, frame.x0 - L), max(0, frame.y0 - T), min(rgb.shape[1], frame.x1 + R), min(rgb.shape[0], frame.y1 + B))
        source = Image.fromarray(rgb).crop(crop_box)
        source_path = output / "sources" / f"{part.stem}.webp"
        render.save_webp(source, source_path)
        record: dict[str, object] = {
            "manufacturer": part.manufacturer,
            "part": part.part,
            "supersedes": f"transfer-review25-2026-07-17 {part.stem} (human-GREEN 2026-07-17)",
            "defect_in_old_gt": part.defect,
            "source_pdf": str(pdf),
            "source_pdf_sha256": _sha256(pdf),
            "page": part.page,
            "render_dpi": DPI,
            "source": str(source_path.relative_to(output)),
            "source_crop_page_px": list(crop_box),
            "frame_page_px": [frame.x0, frame.y0, frame.x1, frame.y1],
            "extractor_commit": _git_head(),
            "extractor_sha256": {
                name: _sha256(REPO / name)
                for name in (
                    "src/datasheet_chart_digitizer/transfer_retrace.py",
                    "src/datasheet_chart_digitizer/transfer_review.py",
                    "tools/transfer_review/generate_transfer_retrace.py",
                    "tools/transfer_review/generate_transfer_review25.py",
                )
            },
            "provenance": provenance,
            "human_verified": False,
        }
        refused = isinstance(x_axis, SimpleNamespace) or isinstance(y_axis, SimpleNamespace)
        if refused:
            evidence = render.refusal_evidence(rgb, crop_box, frame, x_axis, y_axis, output / "overlays" / f"{part.stem}.refused.webp", part.part)
            y_zoom = render.defect_zoom(
                rgb, ((crop_box[0] + frame.x0) / 2 + 20, (frame.y0 + frame.y1) / 2), (70, (frame.y1 - frame.y0) // 2 + 30),
                [(frame.x0 - 40, g, (0, 190, 190), "h") for g in y_axis.grid],
                f"{part.part} y labels vs the chart's own gridlines (cyan dashes), 3x\n" + y_axis.error[:300],
                output / "evidence" / f"{part.stem}.y-labels.webp", zoom=3,
            )
            record.update(
                {
                    "status": "refused-axis-unanchorable",
                    "refusal": provenance.get("refused"),
                    "overlay": str(evidence.relative_to(output)),
                    "evidence": [str(y_zoom.relative_to(output))],
                    "points_csv": None,
                    "axis": {
                        "x": x_axis.to_json() if isinstance(x_axis, ReviewAxis) else {"error": x_axis.error},
                        "y": y_axis.to_json() if isinstance(y_axis, ReviewAxis) else {"error": y_axis.error},
                    },
                }
            )
            manifest.append(record)
            print(f"{part.part}: REFUSED ({provenance.get('refused')})")
            continue
        hits = {"x": assert_ticks_hit(x_axis, TICK_TOLERANCE_PX), "y": assert_ticks_hit(y_axis, TICK_TOLERANCE_PX)}
        rows = _served_points(curves, x_axis, y_axis)
        points_path = output / "points" / f"{part.stem}.csv"
        _write_points(points_path, rows)
        crossings = render.curve_crossings(curves, frame, x_axis, y_axis)
        for crossing in crossings:
            crossing["served"] = x_axis.is_served(crossing["px"][0]) and y_axis.is_served(crossing["px"][1])
        overlay_path = output / "overlays" / f"{part.stem}.retrace.webp"
        render.overlay(rgb, crop_box, frame, x_axis, y_axis, curves, rows, crossings, part.part, overlay_path)
        crop_records = render.zoom_crops(rgb, frame, x_axis, y_axis, curves, crossings, rows, output / "crops" / part.stem)
        evidence = _defect_evidence(part, review25, rgb, frame, x_axis, y_axis, output)
        served_x, served_y = x_axis.served_values, y_axis.served_values
        status = "digitized-review-required"
        if x_axis.unserved_reasons or y_axis.unserved_reasons:
            status = "partial-axis-served-review-required"
        record.update(
            {
                "status": status,
                "overlay": str(overlay_path.relative_to(output)),
                "points_csv": str(points_path.relative_to(output)),
                "points_sha256": _sha256(points_path),
                "overlay_sha256": _sha256(overlay_path),
                "axis": {"x": x_axis.to_json(), "y": y_axis.to_json()},
                "tick_check": {
                    axis_name: {
                        "tolerance_px": TICK_TOLERANCE_PX,
                        "max_abs_error_px": max(abs(float(r["error_px"])) for r in axis_rows if r["consumed"]),
                        "n_consumed": sum(1 for r in axis_rows if r["consumed"]),
                        "n_not_consumed": sum(1 for r in axis_rows if not r["consumed"]),
                        # the overlay draws each crosshair on a 2x canvas: certify the
                        # RENDERED integer marker too, not only the float mapping
                        "rendered_marker_max_abs_error_px": round(max(
                            abs(
                                round((float(r["served_px"]) - (crop_box[0] if axis_name == "x" else crop_box[1])) * render.UP) / render.UP
                                + (crop_box[0] if axis_name == "x" else crop_box[1])
                                - float(r["grid_px"])
                            )
                            for r in axis_rows
                            if r["consumed"]
                        ), 2),
                    }
                    for axis_name, axis_rows in hits.items()
                },
                "served_range": {"Vgs_V": [round(v, 4) for v in served_x], "Id_A": [round(v, 3) for v in served_y]},
                "curve_identification": {
                    f"curve_{i + 1}": {k: v for k, v in c.items() if k in ("temperature_c", "stroke_rgb", "legend_rgb")}
                    for i, c in enumerate(curves)
                },
                "curve_counts": {
                    f"curve_{i + 1}": {
                        "served_points": sum(1 for r in rows if r["curve_id"] == f"curve_{i + 1}" and r["served"]),
                        "unserved_points": sum(1 for r in rows if r["curve_id"] == f"curve_{i + 1}" and not r["served"]),
                        "collapsed_points": sum(1 for r in rows if r["curve_id"] == f"curve_{i + 1}" and r["served"] and r["collapsed"]),
                    }
                    for i in range(len(curves))
                },
                "crossings": [{k: v for k, v in c.items() if k != "px"} | {"px": [round(c["px"][0], 1), round(c["px"][1], 1)]} for c in crossings],
                "zoom_crops": crop_records,
                "old_calibration_vs_ticks": evidence,
                "old_trace_vs_new_trace_px": _trace_px_delta(part, review25, curves, frame),
                "old_vs_new": _delta(part.stem, rows, served_y[1]),
            }
        )
        manifest.append(record)
        print(f"{part.part}: {status}; ticks x<= {record['tick_check']['x']['max_abs_error_px']} px, y<= {record['tick_check']['y']['max_abs_error_px']} px")
    (output / "manifest.json").write_text(json.dumps(manifest, indent=1, default=float) + "\n")
    review_state = {
        "created": "2026-09-29",
        "approved_by": "Fab 2026-09-29 (re-trace of six transfer-review25 parts with wrong GT)",
        "reviewer": None,
        "note": "agent review never sets human_verified; every item is pending Fab's review",
        "parts": [
            {
                "part": record["part"],
                "human_verified": False,
                "status": record["status"],
                "supersedes": record["supersedes"],
                "points_sha256": record.get("points_sha256"),
                "overlay": record.get("overlay"),
                "overlay_sha256": record.get("overlay_sha256") or _sha256(output / str(record["overlay"])),
                "source_pdf_sha256": record["source_pdf_sha256"],
                "page": record["page"],
                "frame_page_px": record["frame_page_px"],
                "extractor_commit": record["extractor_commit"],
                "extractor_sha256": record["extractor_sha256"],
                "verdict": None,
            }
            for record in manifest
        ],
    }
    (output / "review-state.json").write_text(json.dumps(review_state, indent=1) + "\n")


if __name__ == "__main__":
    main()
