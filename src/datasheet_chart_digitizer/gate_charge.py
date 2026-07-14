"""Package-owned gate-charge chart digitization API."""

from __future__ import annotations

import math
import re
import tempfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import pymupdf
import numpy as np
from PIL import Image

from .find_charts import ChartPanel, process_pdf
from .gate_charge_estimation import (
    _best_x_axis_for_panel,
    _best_y_axis_for_panel,
    _estimate_vpl_from_curve,
    _local_y_ticks_for_plot,
)
from .gate_charge_trace import (
    _curve_score,
    _detect_inner_plot_box,
    _mask_page_text,
    _pdf_to_px,
    _smooth_polyline,
    _trace_gate_curve,
    _trace_vector_gate_curve,
)


@dataclass(frozen=True)
class GateChargeResult:
    """One package-native gate-charge chart result.

    Page numbers follow the public chart manifest and are therefore 1-based.
    Pixel coordinates are relative to ``crop_box_pt`` rendered at ``dpi``.
    """

    pdf: str
    panel: ChartPanel
    vpl: float | None
    status: str
    score: float
    trace_source: str
    dpi: int
    crop_box_pt: tuple[float, float, float, float]
    plot_box_px: tuple[int, int, int, int]
    curve_px: tuple[tuple[int, int], ...]
    vpl_y_px: float | None
    y_tick_count: int
    diagnostics: tuple[str, ...] = ()

    def to_manifest(self) -> dict[str, object]:
        payload = asdict(self)
        payload["panel"] = asdict(self.panel)
        return payload


def digitize_gate_charge(
    pdf_path: str | Path,
    *,
    dpi: int = 220,
    finder_dpi: int = 120,
) -> list[GateChargeResult]:
    """Find and digitize every plausible gate-charge chart in ``pdf_path``."""

    pdf = Path(pdf_path).expanduser().resolve()
    if not pdf.exists():
        raise FileNotFoundError(pdf)

    with tempfile.TemporaryDirectory(prefix="dsdig-gate-charge-") as tmp:
        panels = [
            _detach_transient_panel_artifacts(panel)
            for panel in process_pdf(pdf, Path(tmp), dpi=finder_dpi)
            if panel.kind == "gate_charge"
        ]

    results: list[GateChargeResult] = []
    with pymupdf.open(pdf) as doc:
        for panel in panels:
            result = _digitize_panel(pdf, doc, panel, dpi)
            if result is not None:
                results.append(result)
    return sorted(results, key=_result_sort_key)


def _detach_transient_panel_artifacts(panel: ChartPanel) -> ChartPanel:
    """Clear finder paths whose temporary output directory will be deleted."""

    return replace(panel, crop_png="")


def find_vpl_result(
    pdf_path: str | Path,
    *,
    dpi: int = 220,
    finder_dpi: int = 120,
) -> GateChargeResult | None:
    """Return the highest-ranked experimental Vpl result, if one exists.

    Callers must inspect ``status`` and ``diagnostics``. The package-native
    numeric corpus has known wrong-axis cases, including results whose status
    is currently ``ok``; this API is not a validated scalar replacement for
    the legacy dslib estimator yet.
    """

    results = digitize_gate_charge(pdf_path, dpi=dpi, finder_dpi=finder_dpi)
    return next((result for result in results if result.vpl is not None), None)


def _digitize_panel(
    pdf: Path,
    doc: pymupdf.Document,
    panel: ChartPanel,
    dpi: int,
) -> GateChargeResult | None:
    page_index = panel.page - 1
    if not 0 <= page_index < len(doc):
        return None
    page = doc[page_index]
    panel_rect = pymupdf.Rect(panel.bbox_pt)
    panel_axis = _best_y_axis_for_panel(page, panel_rect)
    panel_y_ticks, axis_x = panel_axis if panel_axis is not None else ([], None)
    if panel_y_ticks:
        tick_ys = [y for _value, y in panel_y_ticks]
        if len(tick_ys) >= 3:
            spacing = float(np.median(np.diff(sorted(tick_ys))))
            pad_y = max(9.0, 0.6 * spacing)
            panel_rect.y0 = max(page.rect.y0, min(tick_ys) - pad_y)
            panel_rect.y1 = min(page.rect.y1, max(tick_ys) + pad_y)
        else:
            panel_rect.y0 = max(page.rect.y0, min(panel_rect.y0, min(tick_ys) - 14.0))
            panel_rect.y1 = min(page.rect.y1, max(panel_rect.y1, max(tick_ys) + 14.0))
        if axis_x is not None:
            distance_to_left = abs(axis_x - panel_rect.x0)
            distance_to_right = abs(axis_x - panel_rect.x1)
            if distance_to_left <= distance_to_right:
                panel_rect.x0 = max(panel_rect.x0, axis_x + 3.0)
            else:
                panel_rect.x1 = min(panel_rect.x1, axis_x - 3.0)
    panel_x_axis = _best_x_axis_for_panel(page, panel_rect)
    if panel_x_axis is not None:
        x_ticks, _axis_y = panel_x_axis
        tick_xs = sorted(x for _value, x in x_ticks)
        spacing = float(np.median(np.diff(tick_xs)))
        pad_x = max(6.0, 0.45 * spacing)
        if axis_x is None:
            panel_rect.x0 = max(page.rect.x0, tick_xs[0] - pad_x)
        panel_rect.x1 = min(page.rect.x1, tick_xs[-1] + pad_x)

    crop_rect = (panel_rect + (-46, -38, 42, 52)) & page.rect
    scale = dpi / 72.0
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=crop_rect, alpha=False)
    crop = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    trace_crop = _mask_page_text(crop, page, crop_rect, panel_rect, scale)

    bx0, by0 = _pdf_to_px(crop_rect, scale, panel_rect.x0, panel_rect.y0)
    bx1, by1 = _pdf_to_px(crop_rect, scale, panel_rect.x1, panel_rect.y1)
    loose_plot_box = (int(round(bx0)), int(round(by0)), int(round(bx1)), int(round(by1)))
    plot_box = _detect_inner_plot_box(crop, loose_plot_box)
    local_y_ticks = _local_y_ticks_for_plot(page, crop_rect, scale, plot_box)
    if len(local_y_ticks) < 2:
        local_y_ticks = panel_y_ticks
    measured_y_tick_count = len(local_y_ticks)
    axis_assumed = measured_y_tick_count < 2
    if axis_assumed:
        _x0, y0, _x1, y1 = plot_box
        local_y_ticks = [
            (10.0, crop_rect.y0 + y0 / scale),
            (0.0, crop_rect.y0 + y1 / scale),
        ]

    raster_curve = _smooth_polyline(_trace_gate_curve(trace_crop, plot_box))
    vector_curve = _smooth_polyline(
        _trace_vector_gate_curve(page, crop_rect, scale, plot_box), stride=1
    )
    choices = [
        (raster_curve, "raster", _curve_score(raster_curve, crop.height, crop.width)),
        (vector_curve, "vector", _curve_score(vector_curve, crop.height, crop.width)),
    ]
    curve, trace_source, trace_score = max(choices, key=lambda choice: choice[2])
    vpl, vpl_y_px = _estimate_vpl_from_curve(
        curve, panel, crop_rect, scale, plot_box, local_y_ticks
    )
    if vpl is not None and not math.isfinite(vpl):
        vpl = None

    diagnostics: list[str] = []
    low_trace_confidence = trace_score <= -1e8
    if len(curve) < 20:
        diagnostics.append("insufficient_curve_points")
    elif low_trace_confidence:
        diagnostics.append("low_trace_confidence")
    if axis_assumed:
        diagnostics.append("axis_assumed_0_10")
    if vpl is None:
        diagnostics.append("vpl_unresolved")
    elif not 1.0 <= vpl <= 12.0:
        diagnostics.append("vpl_outside_expected_range")

    score = trace_score + min(4.0, 0.45 * measured_y_tick_count)
    score += _title_score(panel)
    if vpl is None:
        score -= 30.0
    elif not 1.0 <= vpl <= 12.0:
        score -= 12.0

    if vpl is None or len(curve) < 20:
        status = "unresolved"
    elif axis_assumed:
        status = "axis_assumed"
    elif low_trace_confidence:
        status = "low_confidence"
    else:
        status = "ok"
    return GateChargeResult(
        pdf=str(pdf),
        panel=panel,
        vpl=vpl,
        status=status,
        score=round(score, 6),
        trace_source=trace_source,
        dpi=dpi,
        crop_box_pt=tuple(float(value) for value in crop_rect),
        plot_box_px=plot_box,
        curve_px=tuple(curve),
        vpl_y_px=vpl_y_px,
        y_tick_count=measured_y_tick_count,
        diagnostics=tuple(diagnostics),
    )


def _title_score(panel: ChartPanel) -> float:
    title = re.sub(r"\s+", " ", panel.title.lower())
    penalty = 0.0
    if "test circuit" in title or "waveform definition" in title:
        penalty -= 35.0
    elif "waveform" in title and "versus" not in title and " vs" not in title:
        penalty -= 18.0
    if "behavior" in title:
        penalty -= 12.0
    if "characteristic" in title or " vs" in title or "versus" in title:
        penalty += 4.0
    if panel.diagram >= 950:
        penalty -= 1.0
    return penalty


def _result_sort_key(result: GateChargeResult) -> tuple[int, float, int, int]:
    finite = result.vpl is not None
    plausible = finite and 1.0 <= float(result.vpl) <= 12.0
    return (0 if plausible else 1 if finite else 2, -result.score, result.panel.page, result.panel.diagram)
