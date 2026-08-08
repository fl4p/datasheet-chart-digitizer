"""End-to-end `calibrate_axes` wiring for arithmetic vs log capacitance Y axes.

These drive the public entry point with word streams shaped like the real
panels, so they fail if the log/linear decision is rewired, reordered, or the
disproof call site is removed -- unit tests on the private predicates do not.

The case that matters is HYG030N10NS1P: a 0..10000 pF LINEAR ladder whose 1000
and 10000 labels are decade-VALUED.  Reading only those two as a log axis
returned a TRUSTED calibration with `y_resid_dec = 8.9e-16` -- a two-point fit
cannot have any other residual -- and Ciss +20583 % against the spec table.
Degrading the ladder by a single tick used to bring that straight back, because
the disproof was keyed on the arithmetic side being flawless.
"""
from __future__ import annotations

import unittest

from datasheet_chart_digitizer.axis_calibration import calibrate_axes


X_ROW_BAND = (505.0, 527.0)
Y_LABEL_X_BAND = (40.0, 81.0)
PLOT_Y_BAND = (342.0, 512.0)


class _Page:
    """Minimal PyMuPDF page stand-in: words only, no span dictionary."""

    def __init__(self, words):
        self._words = words

    def get_text(self, kind):
        if kind == "words":
            return list(self._words)
        raise RuntimeError("no dictionary for this page")


def _word(text, cx, cy):
    return (cx - 8.0, cy - 4.0, cx + 8.0, cy + 4.0, text)


def _hyg_words(drop=()):
    """HYG030N10NS1P: x 0..30 V, y 1000..10000 pF evenly spaced, title unit."""
    words = [_word(str(v), 81.5 + (v / 5.0) * 33.4, 509.9) for v in range(0, 35, 5)]
    words.append(_word("C-Capacitance(pF)", 54.2, 427.0))
    for index, value in enumerate(range(1000, 11000, 1000)):
        if value in drop:
            continue
        words.append(_word(str(value), 72.3, 489.6 - 15.51 * index))
    return words


def _log_words(decades):
    words = [_word(str(v), 81.5 + (v / 5.0) * 33.4, 509.9) for v in range(0, 35, 5)]
    words.append(_word("C(pF)", 54.2, 427.0))
    for index, exponent in enumerate(decades):
        words.append(_word(str(10 ** exponent), 72.3, 489.6 - 35.0 * index))
    return words


def _calibrate(words):
    return calibrate_axes(_Page(words), X_ROW_BAND, Y_LABEL_X_BAND, PLOT_Y_BAND)


class ArithmeticLadderEndToEndTests(unittest.TestCase):
    def test_full_ladder_calibrates_linear_not_log(self) -> None:
        calibration = _calibrate(_hyg_words())
        self.assertFalse(calibration.y_log, "an arithmetic ladder is not a log axis")
        # y_decades carries (value_pf, pixel) pairs on the linear path.
        values = sorted(value for value, _pixel in calibration.y_decades)
        self.assertEqual(values[0], 1000.0)
        self.assertEqual(values[-1], 10000.0)
        self.assertAlmostEqual(calibration.mx * 81.5 + calibration.bx, 0.0, places=6)

    def test_one_missing_tick_does_not_resurrect_the_two_decade_log_fit(self) -> None:
        # The reviewer's reproduction: suppress exactly one Y word. The
        # arithmetic ladder can no longer prove itself, which must NOT hand the
        # axis back to the unfalsifiable 1000/10000 log reading.
        with self.assertRaises(RuntimeError) as caught:
            _calibrate(_hyg_words(drop=(5000,)))
        self.assertIn("Y decade labels", str(caught.exception))

    def test_every_single_tick_dropout_refuses_rather_than_reading_log(self) -> None:
        for dropped in range(1000, 11000, 1000):
            with self.subTest(dropped=dropped):
                try:
                    calibration = _calibrate(_hyg_words(drop=(dropped,)))
                except RuntimeError:
                    continue
                self.assertFalse(
                    calibration.y_log,
                    f"dropping {dropped} produced a log axis from an arithmetic ladder",
                )


class LogLadderEndToEndTests(unittest.TestCase):
    def test_three_decade_log_axis_still_calibrates(self) -> None:
        calibration = _calibrate(_log_words((2, 3, 4)))
        self.assertTrue(calibration.y_log)
        self.assertEqual(
            sorted(exponent for exponent, _pixel in calibration.y_decades),
            [2.0, 3.0, 4.0],
        )

    def test_two_decade_log_axis_is_admitted_when_nothing_contradicts_it(self) -> None:
        # Toshiba TPCC8105's OCR recovers exactly two Y labels and nothing
        # else. A two-point fit is unfalsifiable, but refusing here would
        # discard the only reading the page offers on evidence that does not
        # exist.
        calibration = _calibrate(_log_words((3, 4)))
        self.assertTrue(calibration.y_log)

    def test_two_decade_log_axis_is_refused_when_a_peer_contradicts_it(self) -> None:
        # The same two decades, plus one label that is nowhere near the log
        # fit: that peer is the evidence, and it says this is not a log axis.
        words = _log_words((3, 4))
        words.append(_word("5000", 72.3, 489.6 - 35.0 * 0.5))
        with self.assertRaises(RuntimeError):
            _calibrate(words)

    def test_stray_ocr_fragment_does_not_veto_a_real_log_axis(self) -> None:
        # IAUTN15S6N025ATMA1: OCR emits a spurious "2" on top of the 10^3
        # label. Immunity here must come from the decade evidence itself, not
        # from the arithmetic side happening to be imperfect.
        words = _log_words((1, 2, 3, 4, 5))
        words.append(_word("2", 72.3, 489.6 - 35.0 * 2 - 0.5))
        calibration = _calibrate(words)
        self.assertTrue(calibration.y_log)
        self.assertGreaterEqual(len(calibration.y_decades), 5)


if __name__ == "__main__":
    unittest.main()
