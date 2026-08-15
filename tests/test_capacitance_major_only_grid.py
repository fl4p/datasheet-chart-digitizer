"""Plot-box detection on charts carrying only MAJOR vertical gridlines.

Regression cover for EPC 'Figure 5b: Typical Capacitance (Log Scale)' (EPC2361), whose only
verticals are the majors at 0/25/50/75/100 -- and whose 75 V line is interrupted by the legend
box, leaving four detectable runs. The old gate was a blanket "6 or more verticals", so it was
rejected outright with `could not find plot grid verticals; found 4`.

The replacement is structural, so these tests pin BOTH directions: a real major-only grid is
accepted (including with one line occluded), and non-grid junk at the same count is still
rejected. A gate relaxed into a hole would pass the first half alone.

Geometry here mirrors the real crop: 689 px wide with frames at 57 and 657, i.e. comfortably
inside the 4%/96% dead margins. Those margins are NOT part of this fix -- see
`test_outer_figure_border_beyond_the_right_margin_stays_excluded`.
"""

from __future__ import annotations

import numpy as np
import pytest

from datasheet_chart_digitizer.capacitance_traces import (
    _MIN_GRID_VERTICALS,
    find_plot_box,
)

# These charts are exactly the sparse case: the DEFAULT floor of 6 still refuses them, and
# `find_closed_frame_plot_box` only retries at this lower floor after closure recovery has
# declined. Passing it explicitly keeps that contract visible in the tests.
_SPARSE = dict(min_verticals=_MIN_GRID_VERTICALS)

_MAJORS = (57, 207, 357, 507, 657)


def _chart(width=689, height=522, xs=_MAJORS, drop: set[int] | None = None,
           legend_gap: tuple[int, int] | None = None, top=40, bottom=480):
    """White canvas with dark full-height verticals at `xs` (in px)."""
    img = np.full((height, width), 255, dtype=np.uint8)
    for i, x in enumerate(xs):
        if drop and i in drop:
            continue
        img[top:bottom, x:x + 2] = 30
        if legend_gap and i == len(xs) // 2:
            img[legend_gap[0]:legend_gap[1], x:x + 2] = 255
    return img


def test_major_only_grid_is_accepted():
    """5 verticals and nothing else -- rejected by the old count floor of 6."""
    img = _chart()
    box = find_plot_box(img, **_SPARSE)

    assert box.x0 == pytest.approx(58, abs=2)
    assert box.x1 == pytest.approx(658, abs=2)
    assert box.y1 - box.y0 > 0.45 * img.shape[0]


def test_a_gridline_occluded_by_a_legend_still_yields_the_box():
    """The legend splits the 75 V line into two short runs, so only 4 survive the height
    filter and the gaps become 1,1,2 multiples of the base spacing. Requiring EQUAL gaps
    would reject this; requiring integer multiples does not."""
    img = _chart(legend_gap=(150, 430))
    box = find_plot_box(img, **_SPARSE)

    assert box.x0 == pytest.approx(58, abs=2)
    assert box.x1 == pytest.approx(658, abs=2)


def test_non_uniform_verticals_at_the_same_count_are_still_rejected():
    """Calibration against a known-bad: 5 tall marks that are NOT a grid must not be
    mistaken for one, or the relaxation would be a hole rather than a narrower gate.

    Asserted on the OUTCOME (refusal) rather than one message: several structural rules can
    catch a given non-grid, and pinning whichever fires first would make the test brittle to
    reordering without testing anything more."""
    img = _chart(xs=(57, 96, 300, 322, 657))

    with pytest.raises(RuntimeError, match="could not find plot grid verticals"):
        find_plot_box(img, **_SPARSE)


def test_wide_spacing_that_is_not_an_integer_multiple_is_rejected():
    """Exercises the ratio rule itself: spacings well above the base floor and spanning the
    panel, but 150/150/236 is not a multiple pattern."""
    img = _chart(xs=(57, 207, 357, 593))

    with pytest.raises(RuntimeError, match="not a consistent grid"):
        find_plot_box(img, **_SPARSE)


def test_too_few_verticals_is_refused():
    img = _chart(xs=(57, 657))

    with pytest.raises(RuntimeError, match="could not find plot grid verticals"):
        find_plot_box(img, **_SPARSE)


def test_dense_minor_gridlines_still_take_the_confident_path():
    """The Infineon Diagram 11 case: many verticals, accepted on count without the spacing
    test, so unevenly-spaced LOG minor gridlines are not newly rejected."""
    xs = tuple(57 + int(600 * f) for f in (0.0, 0.08, 0.19, 0.3, 0.44, 0.6, 0.78, 1.0))
    img = _chart(xs=xs)
    box = find_plot_box(img, **_SPARSE)

    assert box.x0 == pytest.approx(58, abs=2)
    assert box.x1 == pytest.approx(658, abs=2)


def test_outer_figure_border_beyond_the_right_margin_stays_excluded():
    """Infineon panels carry a full-height OUTER FIGURE BORDER near the crop edge
    (ISC0802NLSATMA1: a rule at x=753 of a 759 px crop, against a true plot right edge of
    712). It must not be mistaken for a gridline: admitting it moved that plot box 42 px.

    This is the known-bad that an earlier revision of the fix actually regressed on, so it is
    pinned rather than merely reasoned about."""
    xs = (112, 262, 412, 562, 712)
    img = _chart(width=759, height=853, xs=xs, top=78, bottom=736)
    img[10:840, 753:755] = 30          # the outer figure border, past 0.96 * 759 = 728.6

    box = find_plot_box(img, **_SPARSE)
    assert box.x1 == pytest.approx(713, abs=2), "outer figure border was admitted as a gridline"
