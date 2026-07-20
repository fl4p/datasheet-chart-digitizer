"""Digitize MOSFET RDS(on)-versus-drain-current charts."""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path

import cv2
import numpy as np
import pymupdf

from .capacitance_types import PlotBox
from .crop_transform import CropTransform
from .diode_forward_voltage import (
    _anchor_linear_axis_to_plot_frame,
    _full_span_grid_lines,
    _page_labels,
    _select_axis,
    _snap_axis_to_grid,
)
from .find_charts import ChartPanel, DiagramTitle, PageText, process_pdf
from .chart_classifier import rdson_formula_direction
from .overlay import draw_axis_ticks, draw_plot_frame
from .rdson_temperature import (
    MAX_AXIS_RESIDUAL_PX,
    DIAG_NO_FULL_SPAN_CURVE,
    PanelCalibration,
    _digitize_rds_panel,
    _rdson_titles_matching,
)

MIN_CURRENT_SPAN_FRACTION = 0.70
MIN_RDS_SPAN_FRACTION = 0.20
MAX_LOCAL_DROP_FRACTION = 0.035

DIAG_AXIS_IDENTITY = "rdson_current_axis_identity_unverified"
DIAG_AXIS_RESIDUAL = "axis_residual_exceeds_threshold"
DIAG_CURRENT_SPAN = "drain_current_span_below_threshold"
DIAG_RDS_SPAN = "rdson_span_below_threshold"
DIAG_MONOTONIC = "rdson_not_nondecreasing_with_drain_current"

_RDS_CURRENT_TITLE_RE = re.compile(
    r"(?:drain(?:-source|\s+to\s+source)\s+)?"
    r"on(?:[- ]state)?[- ]?resistance\s+"
    r"(?:variation\s+)?(?:vs\.?|with|as\s+a\s+function\s+of)\s+"
    r"drain\s+current",
    re.IGNORECASE,
)
_RDS_CURRENT_FORMULA_RE = re.compile(
    r"r\s*ds\s*\(\s*on\s*\)\s*=\s*f\s*\(\s*i\s*d\s*\)",
    re.IGNORECASE,
)


def digitize_pdf(pdf: Path, out_dir: Path, dpi: int = 180) -> list[dict[str, object]]:
    """Digitize every supported RDS(on)-versus-ID panel in ``pdf``."""
    results, _errors = _digitize_pdf(pdf, out_dir, dpi, fail_closed=False)
    return results


def digitize_pdf_fail_closed(
    pdf: Path, out_dir: Path, dpi: int = 180
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Digitize owned panels independently and retain explicit refusals."""

    return _digitize_pdf(pdf, out_dir, dpi, fail_closed=True)


def _digitize_pdf(
    pdf: Path, out_dir: Path, dpi: int, *, fail_closed: bool
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    results: list[dict[str, object]] = []
    errors: list[dict[str, object]] = []
    for panel in process_pdf(pdf, out_dir, dpi):
        if not _is_rdson_current_panel(panel):
            continue
        try:
            crop_path = out_dir / panel.crop_png
            results.append(_digitize_rds_panel(
                panel, crop_path, calibrate_panel(panel, crop_path), out_dir,
                overlay_group="rdson_current_overlays",
                point_columns=["drain_current_a", "rdson_mohm"],
                validation=_validation_reasons,
                overlay_drawer=_draw_overlay,
                success_diagnostics=[
                "vgs_identity_bound_by_local_label_and_trace_geometry",
                "rdson_current_axes_and_monotonicity_validated",
                ],
                thresholds={
                    "maximum_axis_residual_px": MAX_AXIS_RESIDUAL_PX,
                    "minimum_current_span_fraction": MIN_CURRENT_SPAN_FRACTION,
                    "minimum_rdson_span_fraction": MIN_RDS_SPAN_FRACTION,
                    "maximum_local_drop_fraction": MAX_LOCAL_DROP_FRACTION,
                },
            ))
        except Exception as error:
            if not fail_closed:
                raise
            errors.append({
                "kind": "rds_on_current",
                "page": panel.page,
                "diagram": panel.diagram,
                "error": str(error),
            })
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "rdson_current.json").write_text(json.dumps(results, indent=2) + "\n")
    return results, errors


def _rdson_current_titles(page: PageText) -> list[DiagramTitle]:
    return _rdson_titles_matching(page, _RDS_CURRENT_TITLE_RE)


def _is_rdson_current_panel(panel: ChartPanel) -> bool:
    """Route only absolute RDS(on)=f(ID) panels into the mOhm plugin."""

    if panel.kind != "rds_on":
        return False
    local = _local_axis_text(panel)
    owned = " ".join((panel.title, panel.formula, panel.text))
    return _rdson_current_direction_is_evidenced(panel, local) and (
        "normalized" not in owned.lower()
        and not _has_owned_normalized_axis_label(panel)
    )


def _has_owned_normalized_axis_label(panel: ChartPanel) -> bool:
    """Recognize a normalized Y label in this panel's left axis gutter."""

    with pymupdf.open(panel.pdf) as document:
        page = document[panel.page - 1]
        bbox = pymupdf.Rect(panel.bbox_pt)
        gutter = pymupdf.Rect(max(0, bbox.x0 - 35), bbox.y0, bbox.x0 + 2, bbox.y1)
        return any(
            "normalized" in str(word[4]).lower()
            for word in page.get_text("words", clip=gutter)
        )


def _rdson_current_direction_is_evidenced(
    panel: ChartPanel, local_axis_text: str | None = None
) -> bool:
    """Accept explicit title/formula direction or owned ID/RDS axis labels."""

    if panel.kind != "rds_on":
        return False
    local = local_axis_text if local_axis_text is not None else _local_axis_text(panel)
    if (
        _RDS_CURRENT_TITLE_RE.search(panel.title)
        or _RDS_CURRENT_FORMULA_RE.search(panel.formula)
        or _RDS_CURRENT_FORMULA_RE.search(local)
        or rdson_formula_direction(panel.title) == "current"
    ):
        return True
    compact = re.sub(r"[^a-z0-9]", "", local.lower())
    return (
        ("draincurrent" in compact or "ida" in compact)
        and ("rdson" in compact or "dson" in compact)
    )


def _local_axis_text(panel: ChartPanel) -> str:
    """Read owned axis labels just outside the finder panel's tight bbox."""

    with pymupdf.open(panel.pdf) as document:
        page = document[panel.page - 1]
        clip = (pymupdf.Rect(panel.bbox_pt) + (-30, -30, 30, 30)) & page.rect
        return page.get_text("text", clip=clip)


def calibrate_panel(panel: ChartPanel, crop_path: Path) -> PanelCalibration:
    """Calibrate sparse linear RDS(ID) grids from printed tick centers and frame rails."""
    gray = cv2.imread(str(crop_path), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise RuntimeError(f"could not read crop: {crop_path}")
    transform = CropTransform.for_chart(asdict(panel), gray.shape)
    with pymupdf.open(panel.pdf) as document:
        labels = _page_labels(document[panel.page - 1], transform)
    height, width = gray.shape
    broad = PlotBox(int(0.20 * width), int(0.02 * height), int(0.96 * width), int(0.86 * height))
    raw_x = _select_axis(labels, broad, "x")
    raw_y = _select_axis(labels, broad, "y")
    tick_box = PlotBox(
        round(min(tick.pixel for tick in raw_x.ticks)),
        round(min(tick.pixel for tick in raw_y.ticks)),
        round(max(tick.pixel for tick in raw_x.ticks)),
        round(max(tick.pixel for tick in raw_y.ticks)),
    )
    search = PlotBox(
        max(0, tick_box.x0 - 5), max(0, tick_box.y0 - 5),
        min(width - 1, tick_box.x1 + 5), min(height - 1, tick_box.y1 + 5),
    )
    vertical, horizontal = _full_span_grid_lines(gray, search, tick_box)
    x_axis = _snap_axis_to_grid(raw_x, vertical, "X axis", authoritative=True)
    y_axis = _snap_axis_to_grid(raw_y, horizontal, "Y axis", authoritative=True)
    plot = PlotBox(
        round(min(tick.pixel for tick in x_axis.ticks)),
        round(min(horizontal) if len(horizontal) >= 2 else min(tick.pixel for tick in y_axis.ticks)),
        round(max(tick.pixel for tick in x_axis.ticks)),
        round(max(horizontal) if len(horizontal) >= 2 else max(tick.pixel for tick in y_axis.ticks)),
    )
    x_axis = _anchor_linear_axis_to_plot_frame(x_axis, plot, "x")
    y_axis = _anchor_linear_axis_to_plot_frame(y_axis, plot, "y")
    return PanelCalibration(plot, x_axis, y_axis, tick_box)


def _axis_identity_is_evidenced(panel: ChartPanel) -> bool:
    local = _local_axis_text(panel).lower()
    compact = re.sub(r"[^a-z0-9ωΩΩ\uf057]", "", local)
    has_current_axis = "draincurrent" in compact or "ida" in compact
    has_rdson_axis = (
        "rdson" in compact or "dson" in compact
    ) and any(unit in compact for unit in ("mω", "mΩ", "mΩ", "m\uf057"))
    return _is_rdson_current_panel(panel) and has_current_axis and has_rdson_axis


def _validation_reasons(
    panel: ChartPanel,
    calibration: PanelCalibration,
    curves: list[dict[str, object]],
) -> list[str]:
    if not curves:
        return [DIAG_NO_FULL_SPAN_CURVE]
    reasons: list[str] = []
    x_ticks = [tick.value for tick in calibration.x_axis.ticks]
    y_ticks = [tick.value for tick in calibration.y_axis.ticks]
    if not (
        calibration.x_axis.model == "linear"
        and calibration.y_axis.model == "linear"
        and min(x_ticks) <= 0.0 < max(x_ticks)
        and min(y_ticks) <= 0.0 < max(y_ticks)
        and _axis_identity_is_evidenced(panel)
    ):
        reasons.append(DIAG_AXIS_IDENTITY)
    if max(calibration.x_axis.residual_px, calibration.y_axis.residual_px) > MAX_AXIS_RESIDUAL_PX:
        reasons.append(DIAG_AXIS_RESIDUAL)

    x_span = max(x_ticks) - min(x_ticks)
    y_span = max(y_ticks) - min(y_ticks)
    for curve in curves:
        points = np.asarray(curve["points"], dtype=float)
        order = np.argsort(points[:, 0])
        current = points[order, 0]
        rdson = points[order, 1]
        if (current[-1] - current[0]) / x_span < MIN_CURRENT_SPAN_FRACTION:
            reasons.append(DIAG_CURRENT_SPAN)
        if (float(rdson.max()) - float(rdson.min())) / y_span < MIN_RDS_SPAN_FRACTION:
            reasons.append(DIAG_RDS_SPAN)
        if float(np.min(np.diff(rdson))) < -MAX_LOCAL_DROP_FRACTION * y_span:
            reasons.append(DIAG_MONOTONIC)
    return list(dict.fromkeys(reasons))


def _draw_overlay(
    crop_path: Path,
    panel: ChartPanel,
    calibration: PanelCalibration,
    curves: list[dict[str, object]],
    status: str,
) -> np.ndarray:
    image = cv2.imread(str(crop_path), cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError(f"could not read crop: {crop_path}")
    plot = calibration.plot
    draw_plot_frame(image, plot, (0, 190, 0))
    for curve in curves:
        points = np.asarray(curve["points_px"], dtype=np.int32).reshape((-1, 1, 2))
        cv2.polylines(image, [points], False, (255, 80, 0), 2, cv2.LINE_AA)
        x, y = map(int, points[-1, 0])
        cv2.putText(
            image, f"{float(curve['gate_voltage_v']):g}V",
            (min(x + 3, image.shape[1] - 40), max(12, y)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.34, (255, 80, 0), 1, cv2.LINE_AA,
        )
    draw_axis_ticks(
        image,
        plot,
        x_ticks=[(tick.pixel, tick.value) for tick in calibration.x_axis.ticks],
        y_ticks=[(tick.pixel, tick.value) for tick in calibration.y_axis.ticks],
        color=(255, 0, 0),
        font_scale=0.23,
        marker_size=5,
        unit_x="A",
        unit_y="mΩ",
    )
    color = (0, 0, 0) if status == "ok" else (0, 0, 190)
    cv2.putText(
        image,
        f"SELECTED p{panel.page} FIGURE {panel.diagram} RDS(on) vs ID  {status.upper()}",
        (4, 13),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.30,
        color,
        1,
        cv2.LINE_AA,
    )
    return image
