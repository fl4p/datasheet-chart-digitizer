"""C axis labels printed with digit-group spaces ("10 000").

Vishay SiSS5112DN / SIR582DP: the word stream split the top label "10 000" into
"10" + "000", a false duplicate of the real "10" that refused the position fit;
the text-order fallback then read "10 000" as 10^0, and the grid tier bound
1..10 000 pF onto one decade (curves served ~100x low, unverified).
"""

from __future__ import annotations

import csv
import os
import tempfile
import unittest
from pathlib import Path

import pytest

from datasheet_chart_digitizer import axis_calibration, capacitance_axis
from datasheet_chart_digitizer.capacitance_types import AxisCalibration

ROOT = Path(os.environ.get("DSDIG_DATASHEET_ROOT", ".")) / "datasheets"


class _WordsPage:
    def __init__(self, words):
        self._words = words

    def get_text(self, kind):
        return list(self._words) if kind == "words" else {"blocks": []}


def _label(x1: float, cy: float, text: str):
    # right-aligned 7 pt Arial digits, 3.9 pt each
    return (x1 - 3.9 * len(text), cy - 3.5, x1, cy + 3.5, text)


def _ladder_words():
    words = [_label(359.0, 460.0 - 33.3 * k, str(10 ** k)) for k in range(4)]
    # "10 000": "10" then a space, then "000"
    words += [(337.7, 323.1, 345.4, 330.1, "10"), (347.5, 323.1, 359.1, 330.1, "000")]
    words += [_label(367.0 + 29.9 * k, 468.0, str(20 * k)) for k in range(6)]
    return words


_BANDS = dict(
    x_row_band=(462.0, 486.0), y_label_x_band=(320.0, 361.0), plot_y_band=(318.0, 468.0)
)


class DigitGroupLabelTests(unittest.TestCase):
    def test_join_digit_groups(self) -> None:
        joined = axis_calibration.join_digit_groups(
            [(0, 0, 7.7, 7, "10"), (9.8, 0, 21.4, 7, "000"), (40, 0, 51.6, 7, "200")]
        )
        self.assertEqual([w[4] for w in joined], ["10000", "200"])
        self.assertEqual(joined[0][:4], (0, 0, 21.4, 7))
        # tick labels a tick pitch apart are not one number
        apart = [(0, 0, 11.6, 7, "100"), (30, 0, 41.6, 7, "200")]
        self.assertEqual(axis_calibration.join_digit_groups(apart), apart)

    def test_split_top_label_reads_as_ten_thousand(self) -> None:
        cal = axis_calibration.calibrate_axes(_WordsPage(_ladder_words()), **_BANDS)
        self.assertEqual([e for e, _ in sorted(cal.y_decades)], [0.0, 1.0, 2.0, 3.0, 4.0])

    def test_dense_x_tick_row_is_not_joined(self) -> None:
        # IMT65R026M2H: "0 50 100 ... 650" with 2.8 pt between 4.4 pt digits
        words = [w for w in _ladder_words() if w[1] < 462.0]
        words += [(363.0 + 16.0 * k - 4.4 * len(str(50 * k)), 466.0, 363.0 + 16.0 * k, 473.0, str(50 * k))
                  for k in range(8)]
        cal = axis_calibration.calibrate_axes(_WordsPage(words), **_BANDS)
        self.assertEqual([v for v, _ in cal.x_ticks], [50.0 * k for k in range(8)])

    def test_text_order_does_not_read_a_digit_group_as_an_exponent(self) -> None:
        text = (
            "10 000 10000 C iss 1000 (pF) 1000 C Capacitance oss line 100 2nd 100 - C C "
            "10 rss 10 1 0 20 40 60 80 100 V - Drain-to-Source Voltage (V) DS Capacitance"
        )
        _x, start = capacitance_axis._parse_x_ticks_from_chart_text(text)
        self.assertNotIn(0.0, capacitance_axis._parse_y_decades_from_chart_text(text, start))
        with self.assertRaises(RuntimeError):
            capacitance_axis.infer_text_order_axis_calibration({"text": text})
        # a real split power label is still read
        self.assertEqual(
            capacitance_axis._parse_y_decades_from_chart_text("10 4 10 3 10 2", -1), [4.0, 3.0, 2.0]
        )


def _table(*ratios):
    return {"anchors": [
        {"log10_ratio": r, "verdict": "not_evaluable" if r is None else
         ("consistent" if abs(r) <= 0.0969 else "inconsistent")} for r in ratios
    ]}


def _cal(grid_status):
    return AxisCalibration(
        x_min_v=0.0, x_max_v=100.0, y_min_decade=0.0, y_max_decade=1.0, source="grid_text", x_ticks_v=(0.0, 100.0), y_decades=(0.0, 1.0),
        y_grid_check=None if grid_status is None else {"status": grid_status, "reason": ""},
    )


class TableScaleGuardTests(unittest.TestCase):
    def test_guard(self) -> None:
        from datasheet_chart_digitizer.mosfet_capacitance import table_contradicts_axis_scale as g

        # the pre-fix SiSS5112DN reading
        self.assertTrue(g(_table(-2.17, -1.87, -0.65), _cal(None)))
        self.assertTrue(g(_table(-2.17, -1.87, -0.65), _cal("unverified")))
        self.assertTrue(g(_table(1.2, 3.0, None), _cal(None)))
        # a grid-verified axis is not overruled by the table
        self.assertFalse(g(_table(-2.17, -1.87, -0.65), _cal("verified")))
        # one anchor agrees, or the sides differ, or under a decade, or a lone anchor
        self.assertFalse(g(_table(-2.17, -1.87, -0.05), _cal(None)))
        self.assertFalse(g(_table(-2.17, 1.87, -0.65), _cal(None)))
        self.assertFalse(g(_table(-0.9, -0.5, -0.3), _cal(None)))
        self.assertFalse(g(_table(-2.5, None, None), _cal(None)))
        self.assertFalse(g(_table(-2.17, -1.87), None))


@pytest.mark.parametrize("part,vds,table", [
    ("SiSS5112DN", 50.0, {"Ciss": 790.0, "Coss": 310.0, "Crss": 7.3}),
    ("SIR582DP-T1-RE3", 40.0, {"Ciss": 3360.0, "Coss": 1020.0, "Crss": 9.5}),
])
def test_vishay_digit_group_axis(part, vds, table):
    pdf = ROOT / "vishay" / f"{part}.pdf"
    if not pdf.exists():
        pytest.skip("pwr-mosfet-lib datasheets not available")
    from datasheet_chart_digitizer import mosfet_capacitance as mc
    from datasheet_chart_digitizer.find_charts import asdict, process_pdf

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        panels = [p for p in process_pdf(pdf, out, 220) if p.kind == "capacitances"]
        assert len(panels) == 1
        panel = panels[0]
        result = mc.process_chart(asdict(panel), out / panel.crop_png, out,
                                  Path(panel.crop_png).with_suffix(""), pdf.parent)
        cal = result["axis_calibration"]
        assert cal["y_decades"] == [0.0, 1.0, 2.0, 3.0, 4.0]
        assert cal["y_grid_check"]["status"] == "verified"
        assert result["physical_output_available"]
        rows = list(csv.DictReader(open(out / result["points"])))
    served = {}
    for row in rows:
        if abs(float(row["vds_V"]) - vds) < 0.5:
            served.setdefault(row["trace"], float(row["cap_pF"]))
    for name, value in table.items():
        assert 0.8 * value <= served[name] <= 1.25 * value, (name, served[name], value)
