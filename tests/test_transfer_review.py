import importlib.util
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

from datasheet_chart_digitizer.capacitance_types import PlotBox
from datasheet_chart_digitizer.transfer_review import (
    TransferAxis,
    calibrate_pixels,
    exchange_two_trace_identities_below,
    extract_transfer_traces,
    maximum_pairwise_collapse_fraction,
    review_trace_from_pixels,
)


def test_calibrate_linear_and_log_axes():
    plot = PlotBox(10, 20, 110, 220)
    linear = TransferAxis(2.0, 7.0, 0.0, 100.0)
    assert calibrate_pixels([(10, 220), (110, 20)], plot, linear) == [
        (2.0, 0.0),
        (7.0, 100.0),
    ]
    log = TransferAxis(0.0, 5.0, 0.001, 100.0, "log10")
    points = calibrate_pixels([(10, 220), (110, 20)], plot, log)
    assert np.allclose(points, [(0.0, 0.001), (5.0, 100.0)])


def test_extracts_two_monotone_transfer_curves_from_grid():
    rgb = np.full((260, 360, 3), 255, np.uint8)
    plot = PlotBox(30, 20, 330, 230)
    for x in np.linspace(plot.x0, plot.x1, 7).astype(int):
        cv2.line(rgb, (x, plot.y0), (x, plot.y1), (190, 190, 190), 1)
    for y in np.linspace(plot.y0, plot.y1, 8).astype(int):
        cv2.line(rgb, (plot.x0, y), (plot.x1, y), (190, 190, 190), 1)
    for offset in (0, 25):
        points = []
        for y in range(plot.y1, plot.y0 - 1, -1):
            current = (plot.y1 - y) / plot.height
            x = int(plot.x0 + 75 + offset + 150 * np.sqrt(current))
            points.append((x, y))
        cv2.polylines(rgb, [np.array(points)], False, (0, 0, 0), 3)

    traces = extract_transfer_traces(
        cv2.cvtColor(rgb, cv2.COLOR_BGR2RGB),
        plot,
        TransferAxis(0.0, 6.0, 0.0, 100.0),
        2,
    )
    assert len(traces) == 2
    assert all(trace.y_span_fraction > 0.9 for trace in traces)
    assert all(trace.monotone_violation_fraction < 0.05 for trace in traces)


def test_seeded_duplicate_branches_are_refused():
    rgb = np.full((220, 300, 3), 255, np.uint8)
    plot = PlotBox(20, 10, 280, 210)
    points = np.array([(80 + int(120 * np.sqrt((210 - y) / 200)), y) for y in range(210, 9, -1)])
    cv2.polylines(rgb, [points], False, (0, 0, 0), 3)
    with pytest.raises(RuntimeError, match="collapsed two requested branches"):
        extract_transfer_traces(
            cv2.cvtColor(rgb, cv2.COLOR_BGR2RGB),
            plot,
            TransferAxis(0, 5, 0, 100),
            2,
            seed_pixels=[(140, 160), (140, 160)],
        )


def test_manual_callout_mask_preserves_seeded_curves_and_log_calibration():
    rgb = np.full((260, 360, 3), 255, np.uint8)
    plot = PlotBox(30, 20, 330, 230)
    curves = []
    for offset in (0, 28):
        points = np.array(
            [
                (plot.x0 + 65 + offset + int(155 * np.sqrt((plot.y1 - y) / plot.height)), y)
                for y in range(plot.y1, plot.y0 - 1, -1)
            ]
        )
        curves.append(points)
        cv2.polylines(rgb, [points], False, (0, 0, 0), 3)
    # A long annotation leader is more attractive to a naive monotone tracker.
    cv2.line(rgb, (45, 145), (250, 145), (0, 0, 0), 2)
    cv2.line(rgb, (45, 120), (45, 145), (0, 0, 0), 2)
    traces = extract_transfer_traces(
        cv2.cvtColor(rgb, cv2.COLOR_BGR2RGB),
        plot,
        TransferAxis(0, 5, 0.001, 100, "log10"),
        2,
        erase_boxes=[(40, 115, 255, 150)],
        seed_pixels=[tuple(curves[0][80]), tuple(curves[1][80])],
    )
    assert len(traces) == 2
    assert all(trace.y_span_fraction > 0.8 for trace in traces)
    assert all(trace.points[0][1] < 0.01 for trace in traces)
    assert all(trace.points[-1][1] > 10 for trace in traces)


def test_grouped_tracking_rejects_an_available_single_callout_leader():
    rgb = np.full((240, 340, 3), 255, np.uint8)
    plot = PlotBox(20, 15, 320, 220)
    curves = []
    for offset in (0, 30):
        points = np.array(
            [
                (plot.x0 + 75 + offset + int(135 * np.sqrt((plot.y1 - y) / plot.height)), y)
                for y in range(plot.y1, plot.y0 - 1, -1)
            ]
        )
        curves.append(points)
        cv2.polylines(rgb, [points], False, (0, 0, 0), 3)
    cv2.line(rgb, (35, 130), (235, 130), (0, 0, 0), 2)
    seed_index = 90
    traces = extract_transfer_traces(
        cv2.cvtColor(rgb, cv2.COLOR_BGR2RGB),
        plot,
        TransferAxis(0, 5, 0, 100),
        2,
        seed_pixels=[tuple(curve[seed_index]) for curve in curves],
        grouped_seeded=True,
    )
    assert len(traces) == 2
    for trace, expected in zip(traces, curves):
        at_leader = min(trace.pixels, key=lambda point: abs(point[1] - 130))
        expected_x = int(expected[np.argmin(abs(expected[:, 1] - 130)), 0])
        assert abs(at_leader[0] - expected_x) < 6


def test_crossing_exchange_preserves_physical_curve_identity():
    plot = PlotBox(0, 0, 100, 100)
    axis = TransferAxis(0, 10, 0, 100)
    left = review_trace_from_pixels([(30, 20), (35, 50), (40, 90)], plot, axis)
    right = review_trace_from_pixels([(70, 20), (65, 50), (60, 90)], plot, axis)

    first, second = exchange_two_trace_identities_below(
        [left, right], 60, plot, axis
    )

    first_x = {y: x for x, y in first.pixels}
    second_x = {y: x for x, y in second.pixels}
    assert first_x[20] == pytest.approx(30, abs=1)
    assert first_x[50] == pytest.approx(35, abs=1)
    assert first_x[90] == pytest.approx(60, abs=1)
    assert second_x[20] == pytest.approx(70, abs=1)
    assert second_x[50] == pytest.approx(65, abs=1)
    assert second_x[90] == pytest.approx(40, abs=1)


def test_partial_branch_collapse_is_measured_across_common_rows():
    plot = PlotBox(0, 0, 100, 100)
    axis = TransferAxis(0, 10, 0, 100)
    first = review_trace_from_pixels(
        [(30, 10), (40, 20), (50, 30), (60, 40)], plot, axis
    )
    second = review_trace_from_pixels(
        [(35, 10), (45, 20), (50, 30), (60, 40)], plot, axis
    )

    collapse = maximum_pairwise_collapse_fraction([first, second])
    assert 0.35 < collapse < 0.5


def test_review_trace_interpolates_accepted_source_gaps_and_records_them():
    plot = PlotBox(0, 0, 100, 100)
    axis = TransferAxis(0, 10, 0, 100)

    trace = review_trace_from_pixels([(20, 10), (30, 20)], plot, axis)

    assert len(trace.pixels) == 11
    assert trace.pixels[5] == (25, 15)
    assert trace.maximum_source_gap_fraction == pytest.approx(0.1)


def test_stk295_vector_paths_and_temperature_identity_match_source():
    script = Path(__file__).parents[1] / "scripts" / "generate_transfer_review25.py"
    spec = importlib.util.spec_from_file_location("generate_transfer_review25", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)

    assert module.TEMPERATURE_BY_CURVE["STK295N10F8AG"] == [25, 175, 55]

    traces = module._stk_vector_traces(
        PlotBox(115, 178, 491, 553), TransferAxis(0, 7, 0, 800)
    )
    maximum_currents = [trace.points[-1][1] for trace in traces]
    assert 490 < maximum_currents[0] < 530
    assert maximum_currents[1] > 690
    assert maximum_currents[2] > 700

    def vgs_at(trace, current):
        return min(trace.points, key=lambda point: abs(point[1] - current))[0]

    assert vgs_at(traces[1], 600) < vgs_at(traces[2], 600)
    assert vgs_at(traces[1], 700) > vgs_at(traces[2], 700)
