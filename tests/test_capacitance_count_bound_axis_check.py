"""The grid tier binds text-order decades to major gridlines by COUNT.

A misread decade list (hidden white secondary-axis labels, "10 000" read as
10^0) was bound to the frame and served as a trusted axis, curves ~100x low
(Vishay SiSS5112DN before fd74a70). The tier now finds each consumed tick's
visible printed label and checks the served mapping there; an axis it cannot
check is not served.
"""

from __future__ import annotations

import math
import os
import tempfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from datasheet_chart_digitizer import axis_calibration, capacitance_axis
from datasheet_chart_digitizer.capacitance_types import AxisCalibration, PlotBox

ROOT = Path(os.environ.get("DSDIG_DATASHEET_ROOT", ".")) / "datasheets"
PLOT = PlotBox(100, 50, 500, 450)
RECT = SimpleNamespace(x0=100.0, y0=50.0, x1=500.0, y1=450.0)
IDENTITY = SimpleNamespace(to_px=lambda x, y: (x, y))  # page pt == crop px


class _Page:
    def __init__(self, words, hidden=()):
        self._words, self._hidden = words, hidden

    def get_text(self, kind):
        if kind == "words":
            return list(self._words)
        spans = [{"bbox": w[:4], "text": w[4], "color": 0xFFFFFF, "alpha": 255, "char_flags": 16}
                 for w in self._hidden]
        return {"blocks": [{"lines": [{"spans": spans}]}]} if kind == "dict" else {"blocks": []}


def _grid_image() -> np.ndarray:
    """Four log decades (100 px each) with 2..9 minors, six X rules 0..100 V."""
    image = np.full((500, 560, 3), 255, np.uint8)
    for d in range(4):
        for j in range(1, 10):
            y = int(round(450 - 100 * (d + math.log10(j))))
            cv2.line(image, (100, y), (500, y), (0, 0, 0) if j == 1 else (150, 150, 150), 1)
    cv2.line(image, (100, 50), (500, 50), (0, 0, 0), 1)
    for k in range(6):
        cv2.line(image, (100 + 80 * k, 50), (100 + 80 * k, 450), (0, 0, 0), 1)
    return image


def check_count_bound_axes(*args):
    from datasheet_chart_digitizer.capacitance_grid_anchor import check_count_bound_axes as check

    return check(*args)


def _y_label(value: str, y: float):
    return (70.0, y - 4.0, 70.0 + 4.0 * len(value), y + 4.0, value)


def _x_words():
    return [(100 + 80 * k - 4.0, 455.0, 100 + 80 * k + 4.0, 463.0, str(20 * k)) for k in range(6)]


def _calibration(decades, top_px=50.0, bottom_px=450.0) -> AxisCalibration:
    y_scale = (decades[0] - decades[-1]) / (bottom_px - top_px)
    return AxisCalibration(
        x_min_v=0.0, x_max_v=100.0, y_min_decade=decades[0], y_max_decade=decades[-1],
        source="grid_text", x_ticks_v=tuple(20.0 * k for k in range(6)), y_decades=tuple(decades),
        x_scale=0.25, x_offset=-25.0, y_scale=y_scale, y_offset=decades[0] - y_scale * bottom_px,
    )


def _ladder(first_decade: int):
    return [_y_label(str(10 ** (first_decade + d)), 450.0 - 100 * d) for d in range(5)]


def test_correct_count_binding_is_verified():
    page = _Page(_ladder(0) + _x_words())
    cal = check_count_bound_axes(_calibration((0.0, 1.0, 2.0, 3.0, 4.0)), _grid_image(), PLOT, page, IDENTITY, RECT)
    assert cal.y_grid_check["status"] == "verified"
    assert cal.x_grid_check["status"] == "verified"


def test_too_few_decades_bound_to_the_frame_fail():
    # "10 000" read as 10^0: decades 0, 1 bound to the frame rules, 100x low
    page = _Page(_ladder(0) + _x_words())
    cal = check_count_bound_axes(_calibration((0.0, 1.0)), _grid_image(), PLOT, page, IDENTITY, RECT)
    assert cal.y_grid_check["status"] == "failed"


def test_hidden_labels_are_not_evidence():
    # white text (Excel secondary axes) on the same rules: never a located label
    hidden = _ladder(0)
    page = _Page(hidden + _x_words(), hidden)
    cal = check_count_bound_axes(_calibration((0.0, 1.0, 2.0, 3.0, 4.0)), _grid_image(), PLOT, page, IDENTITY, RECT)
    assert cal.y_grid_check["status"] == "unverified"
    assert cal.x_grid_check["status"] == "verified"


def test_decade_count_contradicted_by_the_minor_rules():
    # labels 1 and 10 on the frame rules read as one decade over four decades of minors
    page = _Page([_y_label("1", 450.0), _y_label("10", 50.0)] + _x_words())
    cal = check_count_bound_axes(_calibration((0.0, 1.0)), _grid_image(), PLOT, page, IDENTITY, RECT)
    assert cal.y_grid_check["status"] != "verified"


def test_x_mapping_off_the_rules_fails():
    page = _Page(_ladder(0) + _x_words())
    cal = replace(_calibration((0.0, 1.0, 2.0, 3.0, 4.0)), x_scale=0.2, x_offset=-20.0)
    cal = check_count_bound_axes(cal, _grid_image(), PLOT, page, IDENTITY, RECT)
    assert cal.x_grid_check["status"] == "failed"


def _run_capacitance(pdf: Path) -> list[dict]:
    from datasheet_chart_digitizer import mosfet_capacitance as mc
    from datasheet_chart_digitizer.find_charts import asdict, process_pdf

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        return [
            mc.process_chart(asdict(p), out / p.crop_png, out, Path(p.crop_png).with_suffix(""), pdf.parent)
            for p in process_pdf(pdf, out, 220) if p.kind == "capacitances"
        ]


def _pdf(vendor: str, part: str) -> Path:
    pdf = ROOT / vendor / f"{part}.pdf"
    if not pdf.exists():
        pytest.skip("pwr-mosfet-lib datasheets not available")
    return pdf


def test_grid_tier_chart_with_superscript_labels_is_verified():
    # AGM15T06T: "10" + a superscript the position tier does not read; the grid
    # tier's decades 0..4 are right and its labels sit on their rules
    (result,) = _run_capacitance(_pdf("agmsemi", "AGM15T06T"))
    cal = result["axis_calibration"]
    assert cal["source"] == "grid_text"
    assert cal["y_grid_check"]["status"] == "verified"
    assert cal["x_grid_check"]["status"] == "verified"
    assert result["axis_calibration_trusted"] and result["physical_output_available"]


@pytest.mark.parametrize("unchecked", [False, True])
def test_misread_decades_without_table_anchors_are_not_served(monkeypatch, unchecked):
    # SiSS92DN has no spec-table anchors, so nothing else catches a wrong scale.
    # Pre-fd74a70 reading: the split "10 000" refuses the position tier and the
    # text order yields decades 0, 1, which the grid tier bound to the frame.
    pdf = _pdf("vishay", "SiSS92DN")
    monkeypatch.setattr(axis_calibration, "join_digit_groups", lambda words: [tuple(w) for w in words])
    monkeypatch.setattr(capacitance_axis, "_parse_y_decades_from_chart_text", lambda text, start: [0.0, 1.0])
    if unchecked:
        # a count-bound axis that carries no check at all is not served either
        monkeypatch.setattr(capacitance_axis, "check_count_bound_axes", lambda cal, *a: cal, raising=False)
    (result,) = _run_capacitance(pdf)
    assert all(row["verdict"] == "not_evaluable" for row in result["table_check"]["anchors"])
    assert not result["axis_calibration_trusted"]
    assert not result["physical_output_available"]
    assert result["status"] != "ok"
