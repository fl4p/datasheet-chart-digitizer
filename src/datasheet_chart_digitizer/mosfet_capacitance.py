#!/usr/bin/env python3
"""Digitize capacitance chart traces from find_charts.py output.

This plugin extracts the three dominant dark trace components from each
`capacitances` chart crop and overlays colored centerlines on the original crop:

- red: Ciss
- blue: Coss
- green: Crss
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import cv2

if __package__ in (None, ""):  # pragma: no cover - direct script compatibility
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "datasheet_chart_digitizer"

try:
    from .coss_integrator import coss_metrics, validate_axis
except Exception:  # pragma: no cover - optional when this file is copied alone
    try:
        from coss_integrator import coss_metrics, validate_axis
    except Exception:
        coss_metrics = None  # type: ignore
        validate_axis = None  # type: ignore

from .capacitance_axis import (
    _clip01,
    _interval_coverage_fraction,
    _is_power_ten_exponent,
    _is_uniform_tick_run,
    _major_horizontal_gridline_centers,
    _major_horizontal_gridline_fit,
    _number_tokens,
    _parse_x_ticks_from_chart_text,
    _parse_y_decades_from_chart_text,
    arrays_for_trace_data,
    calibrate_axes,
    axis_calibration_is_trusted,
    axis_calibration_to_json,
    calibration_delta_to_json,
    calibration_log_c_of_y,
    calibration_v_of_x,
    calibration_x_of_v,
    calibration_y_of_log_c,
    infer_gridline_axis_calibration,
    infer_position_axis_calibration,
    infer_text_order_axis_calibration,
    reject_bad_position_calibration,
    trace_data_points,
)
from .capacitance_overlay import _fmt_optional, draw_axis_debug_overlay, draw_trace_overlay
from .capacitance_refs import (
    _anchor_csv_path,
    _extract_reference_vint,
    _first_number_after_symbol_before_unit,
    _first_number_before_unit,
    _first_positive_number,
    _symbol_position,
    output_charge_reference_to_json,
    parse_capacitance_anchors,
    parse_output_charge_reference,
)
from .capacitance_traces import (
    _changed_repair_runs,
    _changed_repair_segment,
    _cluster_column_runs,
    _cluster_row_runs,
    _enforce_low_v_coss_monotone,
    _interp_y,
    _interp_y_in_range,
    _is_bottom_branch,
    _low_v_nonfolding,
    _nearest_separated_coss_sample,
    _overlaps_peer_for_too_long,
    _predict_y,
    _repair_coss_ciss_overlap_gap,
    _repair_leading_coss_upper_envelope,
    _repair_leading_steep_coss,
    _repair_leading_steep_crss,
    _repair_missing_leading_knee,
    _repair_shape_guard,
    _seed_x_from_anchors,
    _seed_x_from_middle,
    _single_valued_by_x,
    _smooth_points,
    _splice_continuity_ok,
    _splice_pair_continuous,
    _trace_candidates,
    _trace_fragment_mask,
    _track_direction,
    _track_directional_traces,
    _track_one_trace,
    _trim_repair_points_on_peer,
    extract_trace_components,
    find_plot_box,
    trace_semantic_diagnostics,
    trace_validation_summary,
)
from .capacitance_types import (
    TRACE_COLORS_BGR,
    AxisCalibration,
    CapAnchor,
    GridlineFit,
    OutputChargeReference,
    PlotBox,
    Trace,
    VectorEdge,
)
from .capacitance_validation import (
    _vendor_qoss_curve_path,
    coss_metrics_to_json,
    qoss_validation_status,
    top_decade_clip_diagnostic,
    vendor_qoss_tail_validation,
)
from .capacitance_vector import (
    _chain_vector_components,
    _dedupe_adjacent_points,
    _distance,
    _is_curve_stroke_color,
    _is_dark_stroke,
    _is_long_orthogonal_segment,
    _load_fitz,
    _mostly_inside_plot,
    _order_edge_component,
    _path_length,
    _point_key,
    _resample_vector_trace_pixels,
    _right_edge_y,
    _right_edge_y_pixels,
    _sample_cubic,
    _segment_relevant,
    _vector_curve_edges,
    extract_vector_trace_components,
)


def process_chart(
    chart: dict[str, object],
    crop_path: Path,
    out_dir: Path,
    rel_stem: Path,
    datasheet_root: Path,
    debug_axis_overlays: bool = False,
) -> dict[str, object]:
    image = cv2.imread(str(crop_path), cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError(f"could not read crop {crop_path}")
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    plot = find_plot_box(gray)
    anchors = parse_capacitance_anchors(str(chart["part"]), datasheet_root)
    output_ref = parse_output_charge_reference(str(chart["part"]), datasheet_root)
    axis_text_order: AxisCalibration | None = None
    axis_text_order_error: str | None = None
    axis_grid_error: str | None = None
    axis_position_error: str | None = None
    axis_error: str | None = None
    axis_warning: str | None = None
    try:
        axis_text_order = infer_text_order_axis_calibration(chart)
    except Exception as text_exc:
        axis_text_order_error = str(text_exc)
    try:
        axis_calibration = infer_position_axis_calibration(chart, image, plot)
        rejection = reject_bad_position_calibration(axis_calibration)
        if rejection is not None:
            axis_position_error = rejection
            try:
                axis_calibration = infer_gridline_axis_calibration(chart, image, plot)
            except Exception as grid_exc:
                axis_grid_error = str(grid_exc)
                axis_calibration = axis_text_order
    except Exception as position_exc:
        axis_position_error = str(position_exc)
        try:
            axis_calibration = infer_gridline_axis_calibration(chart, image, plot)
        except Exception as grid_exc:
            axis_grid_error = str(grid_exc)
            axis_calibration = axis_text_order
    if axis_calibration is None:
        axis_error = f"position: {axis_position_error}; grid: {axis_grid_error}; text_order: {axis_text_order_error}"
    axis_trusted = axis_calibration_is_trusted(axis_calibration)
    if axis_calibration is not None and not axis_trusted:
        axis_warning = (
            "untrusted text-order axis fallback; physical vds_V/cap_pF columns "
            "and Qoss validation are disabled"
        )
    extraction_method = "vector"
    try:
        traces = extract_vector_trace_components(chart, image, plot)
    except Exception as vector_exc:
        extraction_method = "raster"
        traces = extract_trace_components(gray, plot, anchors)
        vector_error = str(vector_exc)
    else:
        vector_error = None
    overlay = draw_trace_overlay(image, plot, traces)

    overlay_path = out_dir / "overlays" / rel_stem.with_suffix(".overlay.png")
    points_path = out_dir / "points" / rel_stem.with_suffix(".points.csv")
    axis_debug_path: Path | None = None
    overlay_path.parent.mkdir(parents=True, exist_ok=True)
    points_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(overlay_path), overlay)
    if debug_axis_overlays and axis_calibration is not None:
        axis_debug_path = out_dir / "axis_debug_overlays" / rel_stem.with_suffix(".axis.png")
        axis_debug_path.parent.mkdir(parents=True, exist_ok=True)
        axis_overlay = draw_axis_debug_overlay(
            image,
            plot,
            axis_calibration,
            f"{chart.get('part', '')} {chart.get('diagram', '')}",
        )
        cv2.imwrite(str(axis_debug_path), axis_overlay)

    trace_data: dict[str, list[tuple[float, float]]] = {}
    if axis_trusted and axis_calibration is not None:
        trace_data = {trace.name: trace_data_points(trace, plot, axis_calibration) for trace in traces}

    with points_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["trace", "x_px", "y_px", "x_norm", "y_norm_log_axis", "vds_V", "cap_pF"])
        for trace in traces:
            data_points = trace_data.get(trace.name)
            for idx, (x, y) in enumerate(trace.points):
                vds, cap = data_points[idx] if data_points is not None else ("", "")
                writer.writerow(
                    [
                        trace.name,
                        x,
                        y,
                        (x - plot.x0) / max(1, plot.width - 1),
                        (plot.y1 - y) / max(1, plot.height - 1),
                        vds,
                        cap,
                    ]
                )

    qoss_metrics: dict[str, float] | None = None
    qoss_validation_error: str | None = None
    qoss_vendor_tail_validation: dict[str, object] | None = None
    metrics = None
    coss_clip_diag = top_decade_clip_diagnostic(trace_data, axis_calibration)
    if not axis_trusted and axis_calibration is not None:
        qoss_validation_error = "untrusted axis calibration"
    elif axis_calibration is not None and "Coss" in trace_data and output_ref.vint_v:
        try:
            vds, coss = arrays_for_trace_data(trace_data["Coss"])
            if coss_metrics is None or validate_axis is None:
                raise RuntimeError("coss_integrator is not available")
            clip_ceiling = None
            if coss_clip_diag and coss_clip_diag.get("near_axis_top"):
                clip_ceiling = float(coss_clip_diag["axis_top_pf"])
            metrics = coss_metrics(vds, coss, output_ref.vint_v, clip_ceiling=clip_ceiling)
            qoss_metrics = coss_metrics_to_json(metrics)
            qoss_vendor_tail_validation = vendor_qoss_tail_validation(
                str(chart["part"]),
                metrics,
                output_ref,
                tol=0.25,
            )
            try:
                validate_axis(
                    vds,
                    coss,
                    output_ref.vint_v,
                    ds_Qoss=output_ref.qoss_pc,
                    ds_Coer=output_ref.coer_pf,
                    ds_Cotr=output_ref.cotr_pf,
                    tol=0.25,
                    clip_ceiling=clip_ceiling,
                )
            except Exception as validation_exc:
                qoss_validation_error = str(validation_exc)
        except Exception as exc:
            qoss_validation_error = str(exc)

    diagnostics = trace_semantic_diagnostics(traces, plot)
    validation = trace_validation_summary(diagnostics)

    return {
        "crop": str(crop_path),
        "overlay": str(overlay_path.relative_to(out_dir)),
        "axis_debug_overlay": str(axis_debug_path.relative_to(out_dir)) if axis_debug_path is not None else None,
        "points": str(points_path.relative_to(out_dir)),
        "plot_box_px": [plot.x0, plot.y0, plot.x1, plot.y1],
        "extraction_method": extraction_method,
        "vector_error": vector_error,
        "axis_calibration": axis_calibration_to_json(axis_calibration) if axis_calibration is not None else None,
        "axis_text_order_calibration": axis_calibration_to_json(axis_text_order) if axis_text_order is not None else None,
        "axis_calibration_delta_vs_text_order": calibration_delta_to_json(axis_calibration, axis_text_order, plot),
        "axis_position_error": axis_position_error,
        "axis_grid_error": axis_grid_error,
        "axis_text_order_error": axis_text_order_error,
        "axis_error": axis_error,
        "axis_warning": axis_warning,
        "axis_calibration_trusted": axis_trusted,
        "output_charge_reference": output_charge_reference_to_json(output_ref),
        "qoss_metrics": qoss_metrics,
        "qoss_vendor_tail_validation": qoss_vendor_tail_validation,
        "qoss_validation_status": qoss_validation_status(metrics, qoss_validation_error, qoss_vendor_tail_validation),
        "qoss_validation_error": qoss_validation_error,
        "coss_top_decade_clip": coss_clip_diag,
        "trace_validation_status": validation["status"],
        "trace_validation_reasons": validation["reasons"],
        "diagnostics": diagnostics,
        "anchors": {
            name: {"value_pf": anchor.value_pf, "vds_v": anchor.vds_v}
            for name, anchor in anchors.items()
        },
        "traces": [
            {
                "name": trace.name,
                "area": trace.area,
                "bbox_local_px": list(trace.bbox),
                "points": len(trace.points),
            }
            for trace in traces
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("chart_index", type=Path, help="charts.json from find_charts.py")
    parser.add_argument("--out", type=Path, help="Output dir; defaults to chart index directory")
    parser.add_argument(
        "--datasheet-root",
        type=Path,
        help="Directory containing <part>.pdf.nop.csv anchor tables; defaults to each chart PDF's directory",
    )
    parser.add_argument(
        "--debug-axis-overlays",
        action="store_true",
        help="Write axis calibration overlays with selected ticks/gridlines and fit residuals.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    index_path = args.chart_index
    base_dir = index_path.parent
    out_dir = args.out or base_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    charts = json.loads(index_path.read_text())

    results: list[dict[str, object]] = []
    errors: list[dict[str, str]] = []
    for chart in charts:
        if chart.get("kind") != "capacitances":
            continue
        chart = dict(chart)
        if chart.get("pdf"):
            pdf_path = Path(str(chart["pdf"]))
            if not pdf_path.is_absolute():
                indexed_pdf = base_dir / pdf_path
                pdf_path = indexed_pdf if indexed_pdf.exists() else pdf_path
            chart["pdf"] = str(pdf_path.resolve())
        crop_rel = Path(chart["crop_png"])
        crop_path = base_dir / crop_rel
        rel_stem = crop_rel.with_suffix("")
        if args.datasheet_root is not None:
            datasheet_root = args.datasheet_root
        elif chart.get("pdf"):
            datasheet_root = Path(str(chart["pdf"])).parent
        else:
            datasheet_root = base_dir
        print(f"digitize {chart['part']} diagram {chart['diagram']}: {crop_rel}")
        try:
            result = process_chart(
                chart,
                crop_path,
                out_dir,
                rel_stem,
                datasheet_root,
                debug_axis_overlays=args.debug_axis_overlays,
            )
        except Exception as exc:
            print(f"  ERROR: {exc}")
            errors.append({"crop": str(crop_path), "error": str(exc)})
        else:
            result.update({"part": chart["part"], "page": chart["page"], "diagram": chart["diagram"]})
            results.append(result)
            for trace in result["traces"]:
                print(f"  {trace['name']}: {trace['points']} sampled columns")

    (out_dir / "capacitance_digitization.json").write_text(json.dumps(results, indent=2) + "\n")
    (out_dir / "capacitance_digitization_errors.json").write_text(json.dumps(errors, indent=2) + "\n")
    print(f"wrote {out_dir / 'capacitance_digitization.json'}")
    if errors:
        print(f"wrote {out_dir / 'capacitance_digitization_errors.json'} with {len(errors)} errors")


if __name__ == "__main__":
    main()
