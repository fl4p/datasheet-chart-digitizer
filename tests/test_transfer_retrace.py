"""Tick-evidenced review calibration: each test constructs a defect the
transfer-review25 plot-box calibration let through (re-examined 2026-09-28)."""

from dataclasses import replace

import numpy as np
import pymupdf
import pytest

from datasheet_chart_digitizer.numeric_axis import NumericAxis, fit_axis_ticks, AxisTick
from datasheet_chart_digitizer.transfer_retrace import (
    assert_ticks_hit,
    bind_strokes_to_legend,
    calibrate_review_points,
    fit_review_axis,
    ink_label_centers,
    legend_temperature_colors,
    seat_labels_on_gridlines,
    vector_curve_strokes,
    vector_gridlines,
)


def _box_axis(frame_px, lo, hi):
    """The transfer-review25 calibration: frame corners ASSUMED to be min/max."""
    (p0, p1) = frame_px
    m = (hi - lo) / (p1 - p0)
    return NumericAxis("linear", m, lo - m * p0, (), 0.0, ())


# EPC7018GSH Figure 4 at 180 dpi: nine uniform 0.5 V grid intervals, labels
# 3.0..5.0 on gridlines, but "2.0" printed at the frame edge and "2.5" mid-gap.
EPC_GRID = [154.93 + 61.115 * k for k in range(10)]
EPC_LABELS = [
    ("2.0", 2.0, 155.07),
    ("2.5", 2.5, 246.71),
    ("3.0", 3.0, 460.59),
    ("3.5", 3.5, 521.69),
    ("4.0", 4.0, 582.79),
    ("4.5", 4.5, 641.09),
    ("5.0", 5.0, 703.41),
]


def test_contradicting_labels_are_reported_and_not_served():
    axis = fit_review_axis("x", EPC_LABELS, EPC_GRID, (EPC_GRID[0], EPC_GRID[-1]))
    assert [t.text for t in axis.inliers] == ["3.0", "3.5", "4.0", "4.5", "5.0"]
    reasons = {c.text: c.reason for c in axis.conflicts}
    assert reasons["2.0"].startswith("off-consensus")
    assert reasons["2.5"].startswith("unseated")
    # The uniform grid puts the frame edge at 0.5 V, but nothing printed
    # anchors the region below 3.0 V: it must not be served.
    assert axis.frame_values == pytest.approx((0.5, 5.0), abs=1e-3)
    assert axis.served_values == pytest.approx((3.0, 5.0), abs=1e-3)
    assert axis.unserved_reasons and "conflicting labels" in axis.unserved_reasons[0]
    rows = assert_ticks_hit(axis, 0.5)
    assert all(abs(r["error_px"]) <= 0.5 for r in rows if r["consumed"])


def test_the_shared_fitter_alone_refuses_the_contradicting_label_set():
    ticks = [AxisTick(t, v, p) for t, v, p in EPC_LABELS]
    with pytest.raises(RuntimeError, match="untrusted"):
        fit_axis_ticks(ticks, "x", model="linear")


def test_tick_guard_fires_on_the_old_plot_box_calibration():
    axis = fit_review_axis("x", EPC_LABELS, EPC_GRID, (EPC_GRID[0], EPC_GRID[-1]))
    old = replace(axis, axis=_box_axis(axis.frame_px, 2.0, 5.0))
    # The old map reads the printed 3.0 V gridline as ~3.67 V.
    assert old.value(EPC_GRID[5]) == pytest.approx(3.667, abs=0.01)
    with pytest.raises(RuntimeError, match="misses printed ticks"):
        assert_ticks_hit(old, 1.5)


def _dense_minor_axis(frame_right_lines=54, label_offset=-3.0):
    grid = [840.0 + 9.935 * k for k in range(frame_right_lines + 1)]
    labels = [(f"{0.5 * k:.1f}", 0.5 * k, grid[5 * k] + label_offset) for k in range(11)]
    return grid, labels


def test_frame_ending_between_ticks_is_not_read_as_a_tick():
    # CRSS052N08N: 0.1 V minor grid, 54 intervals -> the frame ends at 5.4 V.
    grid, labels = _dense_minor_axis()
    axis = fit_review_axis("x", labels, grid, (grid[0], grid[-1]))
    assert axis.frame_values[1] == pytest.approx(5.4, abs=0.01)
    assert axis.served_values[1] == pytest.approx(5.4, abs=0.01)
    assert_ticks_hit(axis, 0.5)
    old = replace(axis, axis=_box_axis(axis.frame_px, 0.0, 5.5))
    with pytest.raises(RuntimeError, match="misses printed ticks"):
        assert_ticks_hit(old, 1.5)


def test_frame_far_beyond_the_last_label_is_not_served():
    grid = [100.0 + 50.0 * k for k in range(9)]
    labels = [(str(v), float(v), grid[k]) for k, v in enumerate((0, 10, 20, 30, 40, 50))]
    near = fit_review_axis("y", labels[:6], grid[:7], (grid[0], grid[6]))
    assert near.served_values[1] == pytest.approx(60.0)
    far = fit_review_axis("y", labels, grid, (grid[0], grid[-1]))
    assert far.served_values[1] == pytest.approx(50.0)
    assert "label intervals past" in far.unserved_reasons[0]
    # far tail: the verdict must not flip back as the gap grows
    farther = fit_review_axis("y", labels, grid + [600.0, 650.0], (grid[0], 650.0))
    assert farther.served_values[1] == pytest.approx(50.0)


@pytest.mark.parametrize("scale", [1.0, 2.0, 4.0])
def test_scattered_label_offsets_fail_closed(scale):
    # IPD65R380E6ATMA1-HXY: patched y labels sit +14..-9 px off a 47.6 px grid.
    grid = [1350.5 + 47.625 * k for k in range(9)]
    offsets = [x * scale for x in (14.0, 3.5, -9.0, 3.5, -5.5)]
    offsets = [max(-18.0, min(16.0, o)) for o in offsets]
    labels = [
        (str(v), float(v), grid[2 * k] + off)
        for k, (v, off) in enumerate(zip((20, 15, 10, 5, 0), offsets))
    ]
    with pytest.raises(RuntimeError):
        fit_review_axis("y", labels, grid, (grid[0], grid[-1]))


def test_missing_gridline_leaves_its_label_unseated():
    grid = [218.5 + 61.3 * k for k in range(9) if k != 6]
    labels = [("0", 0.0, 219.5), ("4", 4.0, 342.5), ("8", 8.0, 465.0), ("12", 12.0, 586.0)]
    axis = fit_review_axis("x", labels, grid, (grid[0], grid[-1]))
    assert [c.text for c in axis.conflicts] == ["12"]
    assert axis.served_values[1] == pytest.approx(8.0, abs=0.05)


def test_two_labels_on_one_gridline_are_both_conflicts():
    grid = [0.0, 50.0, 100.0, 150.0, 200.0]
    seated, conflicts = seat_labels_on_gridlines(
        [("0", 0.0, 0.0), ("1", 1.0, 49.0), ("2", 2.0, 51.0)], grid, max_offset_fraction=0.35
    )
    assert [t.text for t in seated] == ["0"]
    assert sorted(c.text for c in conflicts) == ["1", "2"]


def test_too_few_agreeing_labels_refuse():
    grid = [0.0, 50.0, 100.0, 150.0]
    with pytest.raises(RuntimeError, match="agree"):
        fit_review_axis("x", [("0", 0.0, 0.0), ("1", 1.0, 50.0)], grid, (0.0, 150.0))


def test_points_outside_the_served_span_are_flagged():
    x = fit_review_axis("x", EPC_LABELS, EPC_GRID, (EPC_GRID[0], EPC_GRID[-1]))
    grid, labels = _dense_minor_axis(50, 0.0)
    y = fit_review_axis("y", labels, grid, (grid[0], grid[-1]))
    points = calibrate_review_points([(EPC_GRID[5], grid[10]), (EPC_GRID[2], grid[10])], x, y)
    assert points[0][2] is True and points[0][0] == pytest.approx(3.0, abs=1e-3)
    assert points[1][2] is False


def test_ink_label_centres_follow_glyph_groups():
    gray = np.full((40, 300), 255, np.uint8)
    for x0 in (20, 120, 220):
        gray[10:25, x0 : x0 + 6] = 0
        gray[10:25, x0 + 9 : x0 + 15] = 0  # a second glyph, 3 px apart
    centres = ink_label_centers(gray, (0, 0, 300, 40), "x", min_gap_px=8)
    assert centres == pytest.approx([27.5, 127.5, 227.5])


def _vector_chart(decoy_swatch=False):
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=300)
    frame = pymupdf.Rect(50, 50, 250, 250)
    shape = page.new_shape()
    shape.draw_rect(frame)
    for k in range(1, 4):
        x = 50 + 50 * k
        shape.draw_line((x, 50), (x, 250))
        shape.draw_line((50, x), (250, x))
    shape.finish(color=(0.8, 0.8, 0.8), width=0.5)
    # a neighbouring panel whose gridlines share this chart's rows
    for y in (100, 150, 200):
        shape.draw_line((270, y), (390, y))
    shape.finish(color=(0.8, 0.8, 0.8), width=0.5)
    shape.commit()
    colors = {25: (0.0, 0.44, 0.73), 125: (0.93, 0.13, 0.14)}
    # curves drawn in the OPPOSITE order of the legend rows; the 125 C curve
    # runs past the frame top (a PDF clip path hides that part in print)
    curves = {
        125: [(50 + 10 * k, 250 - 0.08 * (10 * k) ** 1.6) for k in range(21)],
        25: [(50 + 10 * k, 250 - 0.05 * (10 * k) ** 1.6) for k in range(20)],
    }
    for temperature in (125, 25):
        shape = page.new_shape()
        shape.draw_polyline(curves[temperature])
        shape.finish(color=colors[temperature], width=2.0, closePath=False)
        shape.commit()
    for row, temperature in enumerate((25, 125)):
        y = 70 + 12 * row
        shape = page.new_shape()
        shape.draw_line((60, y), (72, y))
        shape.finish(color=colors[temperature], width=2.0, closePath=False)
        shape.commit()
        if decoy_swatch and temperature == 25:
            shape = page.new_shape()
            shape.draw_line((62, y + 0.5), (72, y + 0.5))
            shape.finish(color=(0.2, 0.6, 0.2), width=2.0, closePath=False)
            shape.commit()
        page.insert_text((76, y + 3), f"{temperature}°C", fontsize=8)
    return doc, page, frame


def test_vector_strokes_clip_to_the_frame_and_bind_by_legend_colour():
    doc, page, frame = _vector_chart()
    xs, ys = vector_gridlines(page, frame)
    assert xs == pytest.approx([50, 100, 150, 200, 250])
    assert ys == pytest.approx([50, 100, 150, 200, 250])
    strokes = vector_curve_strokes(page, frame)
    legend = legend_temperature_colors(page, frame)
    bound = bind_strokes_to_legend(strokes, legend)
    assert set(bound) == {25.0, 125.0}
    hot = bound[125.0].points_pt
    # the 125 C curve leaves through the frame top: it ends ON the frame, not above it
    assert hot[-1][1] == pytest.approx(frame.y0, abs=0.35)
    assert min(y for _x, y in hot) >= frame.y0 - 0.35
    assert bound[25.0].color == pytest.approx((0.0, 0.44, 0.73), abs=0.01)
    doc.close()


def test_two_swatch_colours_for_one_legend_label_refuse():
    doc, page, frame = _vector_chart(decoy_swatch=True)
    with pytest.raises(RuntimeError, match="swatch"):
        legend_temperature_colors(page, frame)
    doc.close()
