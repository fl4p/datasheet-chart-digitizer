"""The sparse-grid acceptance must refuse junk, measured by fuzz rather than argued.

An adversarial review demonstrated that a naive "gaps are integer multiples of min(gaps)" test
degrades into a near-no-op: `base` has no floor, so one near-duplicate contour (anti-aliasing,
a legend border beside a real line) collapses it to a few px, after which almost any gap is
within tolerance of some multiple. Measured acceptance of NON-grids was ~5% for random 4-mark
sets and ~2.3% for sets containing a close pair -- each a confidently wrong PlotBox, not a
refusal.

These tests pin the outcome, not the implementation: random non-grids must be refused at a rate
indistinguishable from zero, while the real geometry this path exists for is still admitted.
"""

from __future__ import annotations

import random

import numpy as np
import pytest

from datasheet_chart_digitizer.capacitance_traces import _MIN_GRID_VERTICALS, find_plot_box

WIDTH, HEIGHT = 689, 522
_REAL_MAJORS = (57, 207, 357, 657)      # EPC2361 log panel, 75 V line lost to the legend


def _chart(xs, width=WIDTH, height=HEIGHT, top=40, bottom=480):
    img = np.full((height, width), 255, dtype=np.uint8)
    for x in xs:
        img[top:bottom, x:x + 2] = 30
    return img


def _accepts(xs):
    try:
        find_plot_box(_chart(xs), min_verticals=_MIN_GRID_VERTICALS)
        return True
    except RuntimeError:
        return False


def test_the_real_sparse_grid_is_still_admitted():
    """Guard against 'fixed' by refusing everything -- the fuzz below is meaningless if the
    genuine case no longer passes."""
    assert _accepts(_REAL_MAJORS)


def test_a_double_detected_line_does_not_collapse_the_base_spacing():
    """The specific trigger: two contours 4 px apart. They are one gridline, and must be
    merged rather than used to define a 4 px 'base' that legitimises everything else."""
    assert not _accepts((188, 192, 260, 640))


def test_close_pair_plus_unrelated_marks_is_refused():
    rng = random.Random(20260815)
    accepted = 0
    trials = 2000
    for _ in range(trials):
        a = rng.randint(60, 500)
        pair = (a, a + rng.randint(3, 6))
        others = sorted(rng.sample(range(60, WIDTH - 40), 2))
        xs = sorted({*pair, *others})
        if len(xs) < _MIN_GRID_VERTICALS:
            continue
        accepted += _accepts(xs)
    assert accepted == 0, f"{accepted}/{trials} close-pair non-grids accepted"


def test_random_four_mark_sets_are_refused():
    """The review measured ~5% acceptance here even with no near-duplicates present.

    A random draw can legitimately BE a grid, so an accept is only counted as a false positive
    when the set does not actually satisfy the structural contract. Counting every accept
    would make this test fail for the one reason that is not a defect."""
    rng = random.Random(4242)
    false_positives, trials, evaluated = [], 4000, 0
    for _ in range(trials):
        xs = sorted(rng.sample(range(60, WIDTH - 40), 4))
        gaps = np.diff(np.array(xs))
        if gaps.min() < 5:
            continue
        evaluated += 1
        if not _accepts(xs):
            continue
        ratios = gaps / gaps.min()
        genuinely_a_grid = (
            np.max(np.abs(ratios - np.round(ratios))) <= 0.12
            and gaps.min() >= 0.08 * WIDTH
            and np.sum(np.round(ratios)) <= 12
            and (xs[-1] - xs[0]) >= 0.5 * WIDTH
        )
        if not genuinely_a_grid:
            false_positives.append(xs)
    assert evaluated > 3000, "fuzz filtered out too many cases to be meaningful"
    assert not false_positives, (
        f"{len(false_positives)}/{evaluated} random non-grids accepted, "
        f"e.g. {false_positives[:3]}")


@pytest.mark.parametrize(
    "xs, why",
    [
        ((57, 62, 67, 72), "clustered marks spanning almost none of the panel"),
        ((57, 87, 117, 147), "real grid spacing but confined to one corner"),
        ((57, 107, 157, 657), "a 10x jump implies invisible cells, not a grid"),
    ],
)
def test_structurally_implausible_sets_are_refused(xs, why):
    assert not _accepts(xs), why
