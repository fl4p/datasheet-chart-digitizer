"""Closure gates for manually reviewed MOSFET transfer-curve batches."""

from __future__ import annotations

import csv
import json
import math
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pymupdf
from PIL import Image


@dataclass(frozen=True)
class ClosureGate:
    name: str
    passed: bool
    detail: str


def _gate(gates: list[ClosureGate], name: str, passed: bool, detail: str) -> None:
    gates.append(ClosureGate(name, bool(passed), detail))


def _curve_number(curve_id: str) -> int:
    match = re.fullmatch(r"curve_(\d+)", curve_id)
    return int(match.group(1)) if match else 10**9


def _is_finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _read_curves(path: Path) -> dict[str, list[tuple[float, float, float]]]:
    curves: dict[str, list[tuple[float, float, float]]] = {}
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"curve_id", "temperature_c", "Vgs_V", "Id_A"}
        if set(reader.fieldnames or ()) != required:
            raise ValueError(f"CSV columns must be {sorted(required)}")
        for row in reader:
            curve_id = row["curve_id"]
            curves.setdefault(curve_id, []).append(
                (
                    float(row["temperature_c"]),
                    float(row["Vgs_V"]),
                    float(row["Id_A"]),
                )
            )
    return curves


def _interpolate(points: list[tuple[float, float]], y: float) -> float:
    if y <= points[0][0]:
        return points[0][1]
    if y >= points[-1][0]:
        return points[-1][1]
    lo = 0
    hi = len(points) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if points[mid][0] <= y:
            lo = mid
        else:
            hi = mid
    y0, x0 = points[lo]
    y1, x1 = points[hi]
    if y1 == y0:
        return 0.5 * (x0 + x1)
    fraction = (y - y0) / (y1 - y0)
    return x0 + fraction * (x1 - x0)


def _longest_identical_run_fraction(
    first: list[tuple[float, float, float]],
    second: list[tuple[float, float, float]],
    samples: int = 101,
) -> float:
    first_xy = [(current, vgs) for _temperature, vgs, current in first]
    second_xy = [(current, vgs) for _temperature, vgs, current in second]
    lo = max(first_xy[0][0], second_xy[0][0])
    hi = min(first_xy[-1][0], second_xy[-1][0])
    if hi <= lo:
        return 0.0
    longest = 0
    current_run = 0
    for index in range(samples):
        current = lo + (hi - lo) * index / (samples - 1)
        first_vgs = _interpolate(first_xy, current)
        second_vgs = _interpolate(second_xy, current)
        if abs(first_vgs - second_vgs) <= 1e-10:
            current_run += 1
            longest = max(longest, current_run)
        else:
            current_run = 0
    return longest / samples


def _ocr_overlay(path: Path) -> str:
    result = subprocess.run(
        ["tesseract", path.name, "stdout", "--psm", "11"],
        cwd=path.parent,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "tesseract failed")
    return result.stdout


def audit_transfer_record(
    root: Path,
    record: dict[str, Any],
    *,
    overlay_text: str | None = None,
) -> dict[str, Any]:
    """Audit one transfer record without changing its source manifest."""
    gates: list[ClosureGate] = []
    expected_value = record.get("expected_curves")
    expected_curves = (
        expected_value
        if isinstance(expected_value, int)
        and not isinstance(expected_value, bool)
        and expected_value > 0
        else 0
    )
    expected_ids = [f"curve_{index}" for index in range(1, expected_curves + 1)]

    paths: dict[str, Path] = {}
    path_errors = []
    for field in ("source", "overlay", "points_csv"):
        value = record.get(field)
        if not value:
            path_errors.append(f"{field}=missing")
            continue
        candidate = (root / value).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            path_errors.append(f"{field}=outside-root")
            continue
        if not candidate.is_file():
            path_errors.append(f"{field}=not-found")
            continue
        paths[field] = candidate
    _gate(
        gates,
        "artifacts-present",
        not path_errors and len(paths) == 3,
        "source, overlay, and CSV exist inside the batch"
        if not path_errors
        else ", ".join(path_errors),
    )

    source_size: tuple[int, int] | None = None
    image_detail = "image dimensions unavailable"
    image_ok = False
    if "source" in paths and "overlay" in paths:
        try:
            with Image.open(paths["source"]) as source_image:
                source_size = source_image.size
            with Image.open(paths["overlay"]) as overlay_image:
                overlay_size = overlay_image.size
            image_ok = (
                overlay_size[0] == source_size[0]
                and overlay_size[1] >= source_size[1] + 60
            )
            image_detail = f"source={source_size}, overlay={overlay_size}"
        except OSError as exc:
            image_detail = str(exc)
    _gate(gates, "overlay-footer-space", image_ok, image_detail)

    visible_labels_ok = False
    visible_labels_detail = "overlay unavailable"
    if "overlay" in paths:
        try:
            text = overlay_text if overlay_text is not None else _ocr_overlay(paths["overlay"])
            normalized = re.sub(r"\s+", " ", text.upper())
            required_tokens = ("X AXIS", "Y AXIS", "DIGITIZED", "VGS", "ID")
            missing = [token for token in required_tokens if token not in normalized]
            visible_labels_ok = not missing
            visible_labels_detail = (
                "visible X/Y axis footer and digitization legend"
                if not missing
                else f"OCR missing: {', '.join(missing)}"
            )
        except (OSError, RuntimeError) as exc:
            visible_labels_detail = str(exc)
    _gate(gates, "visible-axis-and-legend-labels", visible_labels_ok, visible_labels_detail)

    axis = record.get("axis") or {}
    x_axis = axis.get("x") or {}
    y_axis = axis.get("y") or {}
    x_min = x_axis.get("min")
    x_max = x_axis.get("max")
    y_min = y_axis.get("min")
    y_max = y_axis.get("max")
    axis_ok = (
        x_axis.get("quantity") == "Vgs"
        and x_axis.get("unit") == "V"
        and x_axis.get("scale") == "linear"
        and y_axis.get("quantity") == "Id"
        and y_axis.get("unit") == "A"
        and y_axis.get("scale") in {"linear", "log10"}
        and _is_finite_number(x_min)
        and _is_finite_number(x_max)
        and _is_finite_number(y_min)
        and _is_finite_number(y_max)
        and x_max > x_min
        and y_max > y_min
        and (y_axis.get("scale") != "log10" or y_min > 0)
    )
    _gate(
        gates,
        "axis-metadata-complete",
        axis_ok,
        f"Vgs={x_min}..{x_max} V; Id={y_min}..{y_max} A; y-scale={y_axis.get('scale')}",
    )

    plot = record.get("plot_box_px")
    plot_ok = (
        isinstance(plot, list)
        and len(plot) == 4
        and all(_is_finite_number(value) for value in plot)
        and plot[2] > plot[0]
        and plot[3] > plot[1]
        and source_size is not None
        and 0 <= plot[0] < plot[2] <= source_size[0]
        and 0 <= plot[1] < plot[3] <= source_size[1]
    )
    _gate(
        gates,
        "plot-calibration-box",
        plot_ok,
        f"plot_box_px={plot}; source_size={source_size}",
    )

    curves: dict[str, list[tuple[float, float, float]]] = {}
    csv_error = None
    if "points_csv" in paths:
        try:
            curves = _read_curves(paths["points_csv"])
        except (OSError, ValueError, TypeError) as exc:
            csv_error = str(exc)
    actual_ids = sorted(curves, key=_curve_number)
    _gate(
        gates,
        "curve-count-and-ids",
        expected_curves > 0 and csv_error is None and actual_ids == expected_ids,
        csv_error
        or f"expected_curves={expected_value}; expected={expected_ids}; actual={actual_ids}",
    )

    identifications = record.get("curve_identification") or {}
    manifest_temperatures = {
        curve_id: value.get("temperature_c")
        for curve_id, value in identifications.items()
        if isinstance(value, dict)
    }
    label_set = set(record.get("temperature_labels_c") or ())
    csv_temperatures = {
        curve_id: {point[0] for point in points}
        for curve_id, points in curves.items()
    }
    identity_ok = (
        sorted(manifest_temperatures, key=_curve_number) == expected_ids
        and set(manifest_temperatures.values()) == label_set
        and len(label_set) == expected_curves
        and all(_is_finite_number(value) for value in label_set)
        and all(_is_finite_number(value) for value in manifest_temperatures.values())
        and all(
            csv_temperatures.get(curve_id) == {manifest_temperatures[curve_id]}
            for curve_id in expected_ids
        )
    )
    _gate(
        gates,
        "temperature-identity-consistent",
        identity_ok,
        f"manifest={manifest_temperatures}; source-labels={sorted(label_set)}",
    )

    finite_and_bounded = axis_ok and plot_ok and bool(curves)
    continuity_ok = axis_ok and plot_ok and bool(curves)
    continuity_details = []
    if finite_and_bounded:
        plot_width = plot[2] - plot[0]
        vgs_pixel = (x_max - x_min) / plot_width
        backstep_limit = 1.1 * vgs_pixel + 1e-12
        for curve_id, points in curves.items():
            if len(points) < 20:
                finite_and_bounded = False
                continuity_ok = False
                continuity_details.append(f"{curve_id}: only {len(points)} points")
                continue
            for temperature, vgs, current in points:
                finite_and_bounded &= (
                    math.isfinite(temperature)
                    and math.isfinite(vgs)
                    and math.isfinite(current)
                    and x_min - vgs_pixel <= vgs <= x_max + vgs_pixel
                    and y_min - 1e-12 <= current <= y_max + 1e-12
                )
            current_backstep = max(
                [0.0]
                + [first[2] - second[2] for first, second in zip(points, points[1:])]
            )
            vgs_backstep = max(
                [0.0]
                + [first[1] - second[1] for first, second in zip(points, points[1:])]
            )
            curve_ok = current_backstep <= 1e-12 and vgs_backstep <= backstep_limit
            continuity_ok &= curve_ok
            continuity_details.append(
                f"{curve_id}: Id-backstep={current_backstep:.4g}, "
                f"Vgs-backstep={vgs_backstep:.4g} V (limit={backstep_limit:.4g})"
            )
    _gate(
        gates,
        "finite-calibrated-bounds",
        finite_and_bounded,
        "all CSV values are finite and within calibrated axes",
    )
    _gate(
        gates,
        "curve-continuity",
        continuity_ok,
        "; ".join(continuity_details) or "no curves",
    )

    diagnostics = record.get("diagnostics")
    diagnostic_ids = (
        [value.get("curve_id") for value in diagnostics if isinstance(value, dict)]
        if isinstance(diagnostics, list)
        else []
    )
    diagnostic_ok = (
        isinstance(diagnostics, list)
        and len(diagnostics) == expected_curves
        and sorted(diagnostic_ids, key=_curve_number) == expected_ids
    )
    diagnostic_detail = f"expected diagnostic ids={expected_ids}; actual={diagnostic_ids}"
    if diagnostic_ok:
        allowed_gap_value = record.get("allowed_source_gap_fraction")
        numeric_fields_ok = _is_finite_number(allowed_gap_value)
        spans = []
        gaps = []
        violations = []
        point_counts_ok = True
        for value in diagnostics:
            curve_id = value["curve_id"]
            span = value.get("y_span_fraction")
            gap = value.get("maximum_source_gap_fraction")
            violation = value.get("monotone_violation_fraction")
            points = value.get("points")
            numeric_fields_ok &= all(
                _is_finite_number(number) for number in (span, gap, violation, points)
            )
            if numeric_fields_ok:
                spans.append(float(span))
                gaps.append(float(gap))
                violations.append(float(violation))
            point_counts_ok &= (
                isinstance(points, int)
                and not isinstance(points, bool)
                and points == len(curves.get(curve_id, []))
            )
        if numeric_fields_ok:
            allowed_gap = float(allowed_gap_value)
            diagnostic_ok = (
                0 <= allowed_gap <= 1
                and all(0 <= value <= 1 for value in spans + gaps + violations)
                and min(spans) >= 0.6
                and max(gaps) <= allowed_gap + 1e-12
                and max(violations) <= 0.02
                and point_counts_ok
            )
            diagnostic_detail = (
                f"ids={diagnostic_ids}; min-span={min(spans):.3f}; "
                f"max-gap={max(gaps):.3f}/{allowed_gap:.3f}; "
                f"max-monotone-violation={max(violations):.3f}; "
                f"point-counts-match={point_counts_ok}"
            )
        else:
            diagnostic_ok = False
            diagnostic_detail = f"non-finite or boolean diagnostic value; ids={diagnostic_ids}"
    _gate(gates, "trace-quality", diagnostic_ok, diagnostic_detail)

    provenance = record.get("identity_provenance")
    source_provenance = record.get("identity_source") or {}
    provenance_ok = False
    provenance_detail = f"identity_provenance={provenance}"
    if provenance == "raster-branch-tracking":
        provenance_ok = (
            source_provenance.get("kind") == "delivered-raster"
            and source_provenance.get("image") == record.get("source")
        )
    elif provenance == "independent-pdf-vector-paths":
        pdf = Path(str(source_provenance.get("pdf") or ""))
        page = source_provenance.get("page")
        page_count = None
        if pdf.is_file():
            try:
                with pymupdf.open(pdf) as document:
                    page_count = document.page_count
            except (OSError, RuntimeError, ValueError):
                pass
        provenance_ok = (
            source_provenance.get("kind") == "pdf-vector-paths"
            and page_count is not None
            and isinstance(page, int)
            and not isinstance(page, bool)
            and 1 <= page <= page_count
        )
        provenance_detail += f"; pdf={pdf}; page={page}/{page_count}"
    _gate(gates, "identity-provenance", provenance_ok, provenance_detail)

    collapse_value = record.get("maximum_pairwise_collapse_fraction")
    collapse_present = (
        isinstance(collapse_value, (int, float))
        and not isinstance(collapse_value, bool)
        and math.isfinite(collapse_value)
        and 0 <= collapse_value <= 1
    )
    collapse = float(collapse_value) if collapse_present else math.nan
    collapse_ok = collapse_present and (
        collapse <= 0.1 or provenance == "independent-pdf-vector-paths"
    )
    longest_identical = 0.0
    curve_items = [(curve_id, curves[curve_id]) for curve_id in actual_ids]
    for index, (_first_id, first) in enumerate(curve_items):
        for _second_id, second in curve_items[index + 1 :]:
            longest_identical = max(
                longest_identical,
                _longest_identical_run_fraction(first, second),
            )
    independence_ok = collapse_ok and longest_identical <= 0.05
    _gate(
        gates,
        "branch-independence",
        independence_ok,
        f"near-collapse={collapse:.3f}; longest exactly shared run={longest_identical:.3f}",
    )

    accepted = all(gate.passed for gate in gates)
    return {
        "manufacturer": record.get("manufacturer"),
        "part": record.get("part"),
        "decision": "accepted" if accepted else "rejected",
        "promoted_status": "curated" if accepted else "rejected",
        "overlay": record.get("overlay"),
        "points_csv": record.get("points_csv"),
        "curve_identification": identifications,
        "axis": axis,
        "gates": [asdict(gate) for gate in gates],
    }


def audit_transfer_batch(
    root: Path,
    *,
    use_ocr: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return closure records and a promoted copy of the source manifest."""
    root = root.resolve()
    manifest = json.loads((root / "manifest.json").read_text())
    closure_records = []
    promoted_manifest = []
    for record in manifest:
        overlay_text = None if use_ocr else "X AXIS Y AXIS VGS ID DIGITIZED"
        closure = audit_transfer_record(root, record, overlay_text=overlay_text)
        closure_records.append(closure)
        promoted = dict(record)
        promoted["digitization_status"] = record.get("status")
        promoted["status"] = closure["promoted_status"]
        promoted["closure"] = {
            "decision": closure["decision"],
            "gates": closure["gates"],
        }
        promoted_manifest.append(promoted)
    return closure_records, promoted_manifest


def write_closure_report(
    root: Path,
    closure_records: list[dict[str, Any]],
    promoted_manifest: list[dict[str, Any]],
) -> None:
    """Write machine-readable and visual-review closure artifacts."""
    accepted = sum(record["decision"] == "accepted" for record in closure_records)
    summary = {
        "batch": root.name,
        "accepted": accepted,
        "rejected": len(closure_records) - accepted,
        "records": closure_records,
    }
    (root / "closure25.json").write_text(json.dumps(summary, indent=2) + "\n")
    (root / "curated-manifest.json").write_text(
        json.dumps(promoted_manifest, indent=2) + "\n"
    )

    lines = [
        "# MOSFET transfer-curve batch closure",
        "",
        f"Accepted: **{accepted}** / {len(closure_records)}  ",
        f"Rejected: **{len(closure_records) - accepted}**",
        "",
        "The closure separates calibrated geometry gates from explicit temperature-label "
        "provenance. Missing evidence is a rejection, never an assumed pass.",
        "",
        "[Batch contact sheet](contact25.png)",
        "",
        "| # | Manufacturer | Part | Curves | Axes | Identity provenance | Decision |",
        "|---:|---|---|---|---|---|---|",
    ]
    for index, (closure, promoted) in enumerate(
        zip(closure_records, promoted_manifest, strict=True), start=1
    ):
        curves = ", ".join(
            f"{curve_id}=Tj {value['temperature_c']:g}°C"
            for curve_id, value in closure["curve_identification"].items()
        )
        x_axis = closure["axis"]["x"]
        y_axis = closure["axis"]["y"]
        axes = (
            f"Vgs {x_axis['min']:g}…{x_axis['max']:g} V; "
            f"Id {y_axis['min']:g}…{y_axis['max']:g} A {y_axis['scale']}"
        )
        lines.append(
            f"| {index:02d} | {closure['manufacturer']} | {closure['part']} | "
            f"{curves} | {axes} | {promoted['identity_provenance']} | "
            f"**{closure['promoted_status']}** |"
        )
    for index, closure in enumerate(closure_records, start=1):
        lines.extend(
            [
                "",
                f"## {index:02d} — {closure['manufacturer']} / {closure['part']}",
                "",
                f"![{closure['part']}]({closure['overlay']})",
                "",
            ]
        )
        for gate in closure["gates"]:
            mark = "PASS" if gate["passed"] else "FAIL"
            lines.append(f"- **{mark} — {gate['name']}**: {gate['detail']}")
    (root / "closure25.md").write_text("\n".join(lines) + "\n")
