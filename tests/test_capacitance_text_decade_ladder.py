"""Split "10 <exp>" Y tick labels must not absorb numbers from prose.

Calibrated against Infineon IAUTN12S5N018G figure 10, whose caption number and
condition line ("10 Typ. capacitances ... V GS = 0 V") paired into a decade 0.
The ladder then spanned 10^0..10^5 over a 10^1..10^5 axis, which displaced every
tick by a full decade and blocked the gridline fit.
"""

from __future__ import annotations

import unittest

from datasheet_chart_digitizer.capacitance_axis import (
    _parse_x_ticks_from_chart_text,
    _parse_y_decades_from_chart_text,
)


IAUTN12S5N018G = (
    "10 Typ. capacitances C = f(V ); V = 0 V; f = 1 MHz DS GS "
    "10 5 Ciss 10 4 Coss [pF] 10 3 C 10 2 Crss 10 1 0 50 100 V [V] DS"
)
IAUA170 = (
    "10 Typ. capacitances C = f(V ); V = 0 V; f = 1 MHz DS GS "
    "4 10 Ciss 3 10 Coss [pF] C 2 10 Crss 1 10 0 20 40 60 80 100 V [V] DS"
)


def _decades(text: str) -> list[float]:
    _ticks, x_start = _parse_x_ticks_from_chart_text(text)
    return sorted(set(_parse_y_decades_from_chart_text(text, x_start)))


class TextDecadeLadderTests(unittest.TestCase):
    def test_mantissa_first_ladder_excludes_prose_numbers(self) -> None:
        self.assertEqual([1.0, 2.0, 3.0, 4.0, 5.0], _decades(IAUTN12S5N018G))

    def test_exponent_first_ladder_excludes_prose_numbers(self) -> None:
        self.assertEqual([1.0, 2.0, 3.0, 4.0], _decades(IAUA170))

    def test_adjacent_split_labels_are_still_read(self) -> None:
        text = "Capacitance 10 4 Ciss 10 3 Coss 10 2 Crss 0 25 50 V [V]"
        self.assertEqual([2.0, 3.0, 4.0], _decades(text))

    def test_prose_separated_pair_is_not_a_label(self) -> None:
        # "10" and "0" are adjacent number TOKENS but words apart in the text.
        text = "10 Typ. capacitances V GS = 0 V 10 3 Ciss 10 2 Coss 0 50 V [V]"
        self.assertNotIn(0.0, _decades(text))
