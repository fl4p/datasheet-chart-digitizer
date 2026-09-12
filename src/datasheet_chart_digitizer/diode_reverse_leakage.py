"""Digitize diode reverse-leakage families: Ir versus Vr at several junction temperatures.

Small-signal diode datasheets carry this panel almost universally (Vishay
"Typical Reverse Current vs. Reverse Voltage vs. Various Temperatures", and the
equivalents from Diodes Inc, Nexperia and onsemi). It is the only published
source for leakage at a bias and a temperature that are not the headline test
condition, and the headline is usually quoted near the breakdown voltage — so a
part whose ``IR`` spec reads "2 uA at VR = 25 V" may leak 50 nA or 10 uA at the
1 V that an actual clamp node applies, depending entirely on temperature.
Reading that off the curve by eye is how the number gets wrong by an order of
magnitude, which is exactly what this module exists to stop.

WHY THIS IS ITS OWN PLUGIN AND NOT A FLAG ON ``diode_forward_voltage``.
The two panels look alike — log current, linear voltage, one curve per
temperature — but their *physics gates* are opposites, and the gate is the part
that decides whether an extraction may be trusted:

  - Forward curves CROSS. A body diode has a zero-tempco crossover, so the
    forward plugin must tolerate, detect and report a crossing, and cannot use
    vertical order to identify temperature over the whole span.
  - Reverse-leakage curves DO NOT CROSS. Saturation current rises monotonically
    with temperature at every reverse bias, over the whole plotted range. So
    vertical order IS identity here, and — more usefully — a crossing is
    positive evidence that the extraction is wrong.

Folding these into one module would mean a flag that inverts the safety
property, which is the kind of switch that eventually gets passed the wrong way.

WHAT THIS MODULE REFUSES TO DO. It emits calibrated (Vr, Ir) points per
temperature and nothing else. It does not fit a saturation-current law and does
not extrapolate beyond the plotted temperatures: an Arrhenius fit through five
typical curves would produce a confident number at 85 C whether or not the
curves were assigned to the right temperatures, and a plausible fit must never
be allowed to validate a wrong panel. Interpolation BETWEEN plotted curves is
offered (``interpolate_leakage``) because it is bounded by two measured curves;
extrapolation outside them is refused.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict
from operator import attrgetter
from pathlib import Path

import cv2
import numpy as np

import pymupdf

from .capacitance_types import PlotBox
from .crop_transform import CropTransform
from .diode_forward_voltage import (
    PanelCalibration,
    _NUMERIC_RE,
    _axis_payload,
    _cluster,
    _dedupe_positions,
    _extract_vector_curve_series,
    _normalize_numeric_text,
    _page_labels,
    _panel_temperatures,
)
from .find_charts import ChartPanel, process_pdf
from .numeric_axis import fit_numeric_axis, tick_aligned_plot
from .overlay import draw_axis_ticks, draw_plot_frame

KIND = "reverse_leakage"

# A crossing of two reverse-leakage curves is physically impossible, but two
# curves that merely touch within a stroke width are a rendering artifact of a
# thick line on a log axis, not evidence of a bad extraction. The tolerance is
# in fractions of a decade so it does not depend on the panel's pixel scale.
_CROSSING_TOLERANCE_DECADES = 0.02

# Curves must be separated over most of their shared span for vertical order to
# carry identity. Two curves that overlap almost everywhere mean the extractor
# has split one curve in two, or merged two into one.
_MIN_SEPARATED_FRACTION = 0.80


def digitize_pdf(pdf: Path, out_dir: Path, dpi: int = 180) -> list[dict[str, object]]:
    """Digitize every reverse-leakage panel in *pdf* and write review artifacts."""
    panels = [panel for panel in process_pdf(pdf, out_dir, dpi) if panel.kind == KIND]
    results = [_digitize_panel(panel, out_dir) for panel in panels]
    _write_results(out_dir, results)
    return results


def digitize_panels_fail_closed(
    panels: list[ChartPanel], out_dir: Path
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Digitize owned panels independently and serialize explicit refusals.

    One panel's refusal must not abort unrelated chart families, so this
    mirrors the per-panel wrapper the other plugins expose to ``annotate``.
    """
    results: list[dict[str, object]] = []
    errors: list[dict[str, object]] = []
    for panel in panels:
        if panel.kind != KIND:
            continue
        try:
            results.append(_digitize_panel(panel, out_dir))
        except Exception as error:  # noqa: BLE001 - serialized, not swallowed
            errors.append(
                {
                    "kind": KIND,
                    "page": panel.page,
                    "diagram": panel.diagram,
                    "error": str(error),
                }
            )
    _write_results(out_dir, results)
    return results, errors


def _digitize_panel(panel: ChartPanel, out_dir: Path) -> dict[str, object]:
    """Digitize one already-owned reverse-leakage panel, or refuse."""
    crop_path = out_dir / panel.crop_png
    calibration = _calibrate(panel, crop_path)
    _require_reverse_leakage_axes(calibration)

    temperatures = _panel_temperatures(panel)
    if len(temperatures) < 2:
        raise ValueError(
            f"panel names {len(temperatures)} temperature(s); a leakage family needs "
            "at least two for vertical order to mean anything"
        )

    extracted = _extract_vector_curve_series(
        panel,
        crop_path,
        calibration.plot,
        curve_spans_x=True,
        expected_curve_count=len(temperatures),
    )
    curves_px = [curve.points_px for curve in extracted]
    if len(curves_px) != len(temperatures):
        raise ValueError(
            f"extracted {len(curves_px)} curves but the panel names "
            f"{len(temperatures)} temperatures; refusing to guess the pairing"
        )

    current_scale, current_unit = _current_unit_scale(panel)
    assigned = _assign_by_monotone_order(
        curves_px, calibration, temperatures, current_scale
    )
    diagnostics = _verify_no_crossing(assigned)
    diagnostics.append(f"current_axis_unit_read_from_panel_as_{current_unit}")

    overlay = _draw_overlay(crop_path, calibration, assigned, panel)
    overlay_path = (
        out_dir / "overlays" / panel.part / f"p{panel.page:02d}_d{panel.diagram}.png"
    )
    overlay_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(overlay_path), overlay)

    return {
        "status": "ok",
        "diagnostics": diagnostics,
        "point_columns": ["vr_v", "ir_a"],
        "panel": asdict(panel),
        "plot_box_px": asdict(calibration.plot),
        "hint_source": calibration.hint_source,
        "x_axis": _axis_payload(calibration.x_axis),
        "y_axis": _axis_payload(calibration.y_axis),
        "curves": assigned,
        "overlay": str(overlay_path.relative_to(out_dir)),
    }


def _write_results(out_dir: Path, results: list[dict[str, object]]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "diode_reverse_leakage.json").write_text(
        json.dumps(results, indent=2) + "\n"
    )


_SI_PREFIXES = {
    "": 1.0, "p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "μ": 1e-6, "m": 1e-3,
}


def _current_unit_scale(panel: ChartPanel) -> tuple[float, str]:
    """Read the current axis's SI prefix from the panel's own axis title.

    NEVER defaulted. Vishay plots this family in uA, Nexperia in nA or uA and
    onsemi sometimes in mA, and the axis numbers are identical decades in every
    case -- so assuming a prefix silently rescales the answer by a thousand or a
    million while every other check still passes. That is precisely the failure
    this plugin exists to prevent, so a panel whose current unit cannot be read
    is refused rather than guessed.
    """
    text = panel.text.replace("μ", "u").replace("µ", "u")
    match = re.search(
        r"current[^()]{0,40}\(\s*([pnum]?)\s*A\s*\)", text, re.IGNORECASE
    )
    if match is None:
        match = re.search(r"\(\s*([pnum]?)A\s*\)", text)
    if match is None:
        raise ValueError(
            "cannot read the current axis unit from the panel text; refusing "
            "rather than assuming a prefix (uA vs nA is a 1000x error that no "
            "other check would catch)"
        )
    prefix = match.group(1)
    return _SI_PREFIXES[prefix], f"{prefix}A"


def _calibrate(panel: ChartPanel, crop_path: Path) -> PanelCalibration:
    """Calibrate from LABEL GEOMETRY rather than from the shared plot hint.

    ``diode_forward_voltage.calibrate_panel`` selects tick ladders from bands
    measured relative to ``_plot_hint``'s detected frame. That is right when the
    frame is right, and on this chart class it frequently is not: a leakage
    panel is a dense minor-grid field, and the shared regular-grid detector
    locks onto the densest coherent sub-block instead of the outer frame. On the
    Vishay BAT54W-G panel it returned a box whose bottom edge sat on the 0.1
    decade rather than 0.01, and whose left edge sat 125 px inside the y-label
    gutter -- so the x ladder fell 183 px outside the selector's search band and
    BOTH axes were reported as "no trustworthy numeric tick run".

    Rather than widen the shared detector's tolerances -- a data-dependent
    change to machinery six other chart families depend on, which this project
    requires be validated against the full corpus -- this class calibrates from
    what is unambiguous in its own panel: the printed tick labels.

    The two ladders are found by their typography, not their position:

      - The Y ladder is RIGHT-ALIGNED. Decade labels are set flush against the
        axis, so their right edges share an x within a couple of pixels. This
        is what separates them from the in-plot temperature annotations
        ("125 C", "100 C", "75 C" ...), which are centred and which would
        otherwise look like a plausible descending ladder -- the exact wrong
        ladder to fit, since it would calibrate current against temperature.
      - The X ladder shares a common baseline ``cy`` and lies BELOW the Y
        ladder's vertical span.

    Both are then required to fit an axis model whose residual is small, so a
    coincidental alignment that does not actually form a scale still fails.
    """
    image = cv2.imread(str(crop_path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise RuntimeError(f"could not read crop: {crop_path}")
    transform = CropTransform.for_chart(asdict(panel), image.shape)
    with pymupdf.open(panel.pdf) as doc:
        labels = _page_labels(doc[panel.page - 1], transform)

    height, width = image.shape[:2]
    numeric = [
        label
        for label in labels
        if _NUMERIC_RE.fullmatch(_normalize_numeric_text(label.text))
        and 0 <= label.cx <= width
        and 0 <= label.cy <= height
    ]

    y_axis = _fit_ladder(
        numeric,
        align_attr="x1",
        position_attr="cy",
        align_tolerance=4.0,
        min_span=0.35 * height,
        name="Y axis",
    )
    y_positions = [tick.pixel for tick in y_axis.ticks]
    below = [label for label in numeric if label.cy > max(y_positions) - 1.0]
    x_axis = _fit_ladder(
        below,
        align_attr="cy",
        position_attr="cx",
        align_tolerance=6.0,
        min_span=0.35 * width,
        name="X axis",
    )

    hint = PlotBox(
        int(min(tick.pixel for tick in x_axis.ticks)),
        int(min(y_positions)),
        int(max(tick.pixel for tick in x_axis.ticks)),
        int(max(y_positions)),
    )
    plot = tick_aligned_plot(x_axis, y_axis, hint)
    return PanelCalibration(plot, x_axis, y_axis, hint, "label_geometry")


def _fit_ladder(
    labels: list,
    *,
    align_attr: str,
    position_attr: str,
    align_tolerance: float,
    min_span: float,
    name: str,
):
    """Fit the best axis from label groups sharing an alignment coordinate."""
    best = None
    for group in _cluster(labels, attrgetter(align_attr), align_tolerance):
        deduped = _dedupe_positions(group, lambda label: float(getattr(label, position_attr)))
        if len(deduped) < 3:
            continue
        positions = [float(getattr(label, position_attr)) for label in deduped]
        if max(positions) - min(positions) < min_span:
            continue
        try:
            axis = fit_numeric_axis(
                [
                    (label.text, float(getattr(label, position_attr)))
                    for label in deduped
                ],
                name,
            )
        except RuntimeError:
            continue
        score = (len(deduped), -axis.residual_px)
        if best is None or score > best[0]:
            best = (score, axis)
    if best is None:
        raise RuntimeError(
            f"{name}: no right-aligned numeric tick ladder in the panel crop"
        )
    return best[1]


def _require_reverse_leakage_axes(calibration: PanelCalibration) -> None:
    """Refuse a panel whose axes are not linear volts against log amps.

    Leakage spans decades, so the current axis is logarithmic on every real
    example of this chart. A linear current axis means the finder handed over a
    different panel, and the monotone-order gate below would then be reasoning
    about the wrong picture.
    """
    if calibration.y_axis.model != "log10":
        raise ValueError(
            "reverse-leakage current axis is not logarithmic; refusing the panel "
            "rather than reading a family off a linear axis"
        )
    if calibration.x_axis.model == "log10":
        raise ValueError("reverse-voltage axis is logarithmic; unsupported panel form")


def _assign_by_monotone_order(
    curves_px: list[list[tuple[int, int]]],
    calibration: PanelCalibration,
    temperatures: list[float],
    current_scale: float,
) -> list[dict[str, object]]:
    """Pair curves to temperatures by vertical order: hotter leaks more.

    This is the one identification rule this chart class permits, and it is
    sound here precisely because the curves do not cross (see the module
    docstring). It is applied over the curves' SHARED x-span — comparing a
    curve's mean height against another's over a range where only one of them
    exists would rank them by where they start, not by how much they leak.
    """
    samples = [
        _calibrated_points(points, calibration, current_scale)
        for points in curves_px
    ]
    shared_lo = max(min(x for x, _ in curve) for curve in samples)
    shared_hi = min(max(x for x, _ in curve) for curve in samples)
    if not shared_hi > shared_lo:
        raise ValueError(
            "extracted curves share no common reverse-voltage span; vertical "
            "order cannot identify temperature"
        )

    grid = np.linspace(shared_lo, shared_hi, 32)
    resampled = [_resample_log(curve, grid) for curve in samples]
    order = sorted(range(len(samples)), key=lambda i: float(np.mean(resampled[i])))

    assigned: list[dict[str, object]] = []
    for rank, curve_index in enumerate(order):
        assigned.append(
            {
                "temperature_c": temperatures[rank],
                "points": [
                    [round(x, 6), round(y, 12)] for x, y in samples[curve_index]
                ],
                "points_px": curves_px[curve_index],
                "shared_span_v": [round(shared_lo, 6), round(shared_hi, 6)],
                "log10_ir_on_shared_grid": [
                    round(value, 6) for value in resampled[curve_index]
                ],
            }
        )
    return assigned


def _calibrated_points(
    points_px: list[tuple[int, int]],
    calibration: PanelCalibration,
    current_scale: float,
) -> list[tuple[float, float]]:
    """Map pixels to (volts, AMPS) -- the axis prints a prefixed unit."""
    return [
        (calibration.x_axis.value(x), calibration.y_axis.value(y) * current_scale)
        for x, y in points_px
    ]


def _resample_log(curve: list[tuple[float, float]], grid) -> list[float]:
    """Resample log10(Ir) onto a shared voltage grid.

    Interpolating in log space rather than linear is not cosmetic: on a decade
    axis a linear mean is dominated entirely by the high-voltage end, which
    would rank curves by their endpoint instead of their body.
    """
    xs = [x for x, _ in curve]
    ys = [math.log10(y) if y > 0 else float("nan") for _, y in curve]
    return list(np.interp(grid, xs, ys))


def _verify_no_crossing(assigned: list[dict[str, object]]) -> list[str]:
    """Assert the physics: a hotter curve is above a cooler one everywhere.

    A violation is not a warning to be reported alongside the data — it means
    the temperature assignment is wrong, or two curves were merged, so the
    numbers would be confidently mislabelled. Fail closed.
    """
    diagnostics = ["temperature_identity_from_noncrossing_vertical_order"]
    for lower, upper in zip(assigned, assigned[1:]):
        cool = np.asarray(lower["log10_ir_on_shared_grid"], dtype=float)
        hot = np.asarray(upper["log10_ir_on_shared_grid"], dtype=float)
        margin = hot - cool
        if np.nanmin(margin) < -_CROSSING_TOLERANCE_DECADES:
            raise ValueError(
                f"curves assigned {lower['temperature_c']:g}C and "
                f"{upper['temperature_c']:g}C cross by "
                f"{-float(np.nanmin(margin)):.3f} decades; reverse leakage is "
                "monotonic in temperature, so the extraction or the assignment "
                "is wrong"
            )
        separated = float(np.mean(margin > _CROSSING_TOLERANCE_DECADES))
        if separated < _MIN_SEPARATED_FRACTION:
            raise ValueError(
                f"curves assigned {lower['temperature_c']:g}C and "
                f"{upper['temperature_c']:g}C are separated over only "
                f"{separated:.0%} of their shared span; they are probably one "
                "curve split in two"
            )
    diagnostics.append(f"noncrossing_verified_over_{len(assigned)}_curves")
    return diagnostics


def interpolate_leakage(
    result: dict[str, object], reverse_voltage_v: float, temperature_c: float
) -> float:
    """Interpolate Ir at a bias and temperature BOUNDED BY plotted curves.

    Interpolation is in log10(Ir), linearly in temperature, which is the right
    space: saturation current is exponential in temperature, so a linear-in-Ir
    interpolation between 75 C and 100 C would underestimate 85 C badly.

    Refuses to extrapolate. A caller asking for 150 C from a family plotted to
    125 C wants a number this chart cannot supply, and returning one would be
    indistinguishable from a measured value once it is written into a budget.
    """
    curves = result["curves"]
    temps = [float(curve["temperature_c"]) for curve in curves]
    if not min(temps) <= temperature_c <= max(temps):
        raise ValueError(
            f"{temperature_c:g}C is outside the plotted {min(temps):g}..{max(temps):g}C; "
            "this chart cannot answer that without extrapolation"
        )

    def at(curve: dict[str, object]) -> float:
        xs = [point[0] for point in curve["points"]]
        ys = [math.log10(point[1]) for point in curve["points"]]
        if not min(xs) <= reverse_voltage_v <= max(xs):
            raise ValueError(
                f"{reverse_voltage_v:g} V is outside this curve's plotted "
                f"{min(xs):g}..{max(xs):g} V"
            )
        return float(np.interp(reverse_voltage_v, xs, ys))

    order = sorted(range(len(curves)), key=lambda i: temps[i])
    for lower, upper in zip(order, order[1:]):
        if temps[lower] <= temperature_c <= temps[upper]:
            span = temps[upper] - temps[lower]
            weight = 0.0 if span == 0 else (temperature_c - temps[lower]) / span
            log_ir = at(curves[lower]) * (1 - weight) + at(curves[upper]) * weight
            return 10.0**log_ir
    raise ValueError("no bracketing pair of curves")  # pragma: no cover - guarded above


def _draw_overlay(
    crop_path: Path,
    calibration: PanelCalibration,
    curves: list[dict[str, object]],
    panel: ChartPanel,
):
    image = cv2.imread(str(crop_path), cv2.IMREAD_COLOR)
    assert image is not None
    plot = calibration.plot
    header_y = 0
    if plot.y0 <= 32:
        source_height = image.shape[0]
        canvas = np.full((source_height + 33, image.shape[1], 3), 255, dtype=np.uint8)
        canvas[:source_height] = image
        image = canvas
        header_y = source_height
    cv2.rectangle(
        image, (0, header_y), (image.shape[1] - 1, header_y + 32), (220, 255, 255), -1
    )
    cv2.putText(
        image,
        f"SELECTED p{panel.page} FIGURE/DIAGRAM {panel.diagram}: {panel.title}",
        (5, header_y + 12),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.34,
        (0, 0, 0),
        1,
    )
    cv2.putText(
        image,
        "AXES: IR (A, log) versus VR (V)",
        (5, header_y + 27),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.38,
        (0, 0, 0),
        1,
    )
    draw_plot_frame(image, plot, (0, 180, 0))
    colors = (
        (255, 120, 0),
        (0, 170, 0),
        (0, 190, 220),
        (0, 90, 255),
        (0, 0, 230),
        (180, 0, 180),
    )
    for index, curve in enumerate(curves):
        color = colors[index % len(colors)]
        for x, y in curve["points_px"]:
            cv2.circle(image, (int(x), int(y)), 1, color, -1)
        cv2.putText(
            image,
            f'{curve["temperature_c"]:g}C',
            (plot.x1 - 60, plot.y0 + 32 + 14 * index),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.4,
            color,
            1,
        )
    draw_axis_ticks(
        image,
        plot,
        x_ticks=[(t.pixel, t.value) for t in calibration.x_axis.ticks],
        y_ticks=[(t.pixel, t.value) for t in calibration.y_axis.ticks],
        color=(255, 0, 0),
        marker_size=8,
        font_scale=0.32,
        unit_x=" V",
        unit_y=" A",
        line_aa=True,
        halo=True,
    )
    return image


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Digitize diode reverse-leakage Ir(Vr, Tj) charts"
    )
    parser.add_argument("pdfs", nargs="*", type=Path, help="Datasheet PDFs to scan")
    parser.add_argument("--out", type=Path, default=Path("out/datasheet_charts"))
    parser.add_argument("--dpi", type=int, default=180)
    args = parser.parse_args(argv)

    for pdf in args.pdfs:
        results = digitize_pdf(pdf, args.out, args.dpi)
        print(f"scan {pdf}")
        print(f"  digitized {len(results)} reverse-leakage panels")
        for result in results:
            print(f"  overlay: {args.out / result['overlay']}")
