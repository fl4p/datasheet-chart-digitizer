"""Superjunction Coss crossing the flat Ciss through a short merge.

The tracker left both names on Ciss past the knee and Coss was served at the
Ciss value (STB36N60M6: 1990 pF against a 93 pF table Coss at 100 V).
"""

import os
from pathlib import Path

import cv2
import numpy as np
import pytest

from datasheet_chart_digitizer import capacitance_pair_tracking as cp
from datasheet_chart_digitizer.capacitance_types import PlotBox

PLOT = PlotBox(0, 0, 199, 199)


def _merge_case(upper_after, upper_before=lambda x: 10 + 0.5 * x):
    """Upper curve falls onto flat y=50 at x=80, then leaves along `upper_after`."""
    upper = [upper_before(x) if x <= 80 else upper_after(x) for x in range(200)]
    mask = np.zeros((200, 200), np.uint8)
    cv2.line(mask, (0, 50), (199, 50), 1, 1)
    pts = np.array([[x, round(y)] for x, y in enumerate(upper)], np.int32)
    cv2.polylines(mask, [pts], False, 1, 2)
    tracked_upper = [(x, round(upper[x])) for x in range(76)] + [(x, 50) for x in range(76, 200)]
    assigned = {
        "Ciss": tracked_upper,
        "Coss": [(x, 50) for x in range(200)],
        "Crss": [(x, 180) for x in range(200)],
    }
    return mask, assigned


def test_crossing_through_short_merge_follows_its_own_ink():
    mask, assigned = _merge_case(lambda x: min(150.0, 50 + 4 * (x - 80)))
    out = cp.repair_crossed_upper_merge(mask, assigned, PLOT)
    upper = dict(out["Ciss"])
    assert abs(upper[150] - 150) <= 1 and abs(upper[95] - 110) <= 2
    assert upper[40] == 30
    assert out["Coss"] == assigned["Coss"]


def test_bounce_back_above_peer_is_left_shared():
    mask, assigned = _merge_case(lambda x: max(20.0, 50 - 2 * (x - 86)) if x > 86 else 50.0)
    assert cp.repair_crossed_upper_merge(mask, assigned, PLOT) is assigned


def test_tangent_approach_is_left_shared():
    # Parallel to its peer until a kink just before the merge: no closing rate.
    mask, assigned = _merge_case(
        lambda x: min(150.0, 50 + 4 * (x - 80)),
        lambda x: 40.0 if x < 76 else 40 + 2.5 * (x - 76),
    )
    assert cp.repair_crossed_upper_merge(mask, assigned, PLOT) is assigned


LIB = Path(os.environ.get("DSDIG_DATASHEET_ROOT", ".")) / "datasheets" / "st"


@pytest.mark.parametrize(
    "part, table_coss_pf",
    [("STB36N60M6", 93.0), ("STP60N043DM9", 82.0),
     ("STWA65N60DM6", 125.0), ("SH63N65DM6AG", 161.0)],
)
def test_st_superjunction_coss_is_not_served_on_ciss(part, table_coss_pf, tmp_path):
    pdf = LIB / f"{part}.pdf"
    if not pdf.exists():
        pytest.skip("pwr-mosfet-lib datasheets not available")
    from datasheet_chart_digitizer import mosfet_capacitance as mc
    from datasheet_chart_digitizer.capacitance_assignment import TABLE_CHECK_MAX_LOG10_RATIO
    from datasheet_chart_digitizer.find_charts import asdict, process_pdf

    panels = [p for p in process_pdf(pdf, tmp_path, 220) if p.kind == "capacitances"]
    assert len(panels) == 1
    panel = panels[0]
    result = mc.process_chart(asdict(panel), tmp_path / panel.crop_png, tmp_path,
                              Path(panel.crop_png).with_suffix(""), pdf.parent)
    if result["status"] == "refused":
        return
    coss = next(r for r in result["table_check"]["anchors"] if r["name"] == "Coss")
    assert coss["table_pf"] == table_coss_pf
    assert abs(coss["log10_ratio"]) <= TABLE_CHECK_MAX_LOG10_RATIO
    assert result["diagnostics"]["Coss"]["repair"] == "crossed_upper_merge"
    assert "trace_repaired_crossed_merge" not in result["status_reasons"]
    assert result["status"] == "ok"


def test_repair_reaches_the_review_record_and_overlay(tmp_path):
    pdf = LIB / "STB36N60M6.pdf"
    if not pdf.exists():
        pytest.skip("pwr-mosfet-lib datasheets not available")
    import cv2
    from datasheet_chart_digitizer.annotate_pdf import _process_indexed_panel
    from datasheet_chart_digitizer.find_charts import process_pdf

    panel = next(p for p in process_pdf(pdf, tmp_path, 220) if p.kind == "capacitances")
    record = _process_indexed_panel(panel, tmp_path, pdf)
    assert record["repairs"] == {"Coss": "crossed_upper_merge"}

    from dataclasses import replace
    from datasheet_chart_digitizer.capacitance_overlay import draw_trace_overlay
    from datasheet_chart_digitizer.capacitance_types import PlotBox, Trace

    plot = PlotBox(10, 10, 190, 190)
    image = np.full((200, 200, 3), 255, np.uint8)
    trace = Trace("Coss", 150, (20, 100, 170, 100), [(20 + i, 100) for i in range(150)])
    plain = draw_trace_overlay(image, plot, [trace])
    marked = draw_trace_overlay(image, plot, [replace(trace, repair="crossed_upper_merge")])
    assert (cv2.absdiff(plain, marked) > 0).any()
