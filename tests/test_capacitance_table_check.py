"""Chart-vs-table check of served C(V) curves at their spec-table anchors.

Unevaluable must never read as consistent: a missing, rejected or unsampled
anchor, or an untrusted axis, gives `not_evaluable`, never `consistent`.
"""

import math
import os
from pathlib import Path

import pytest

from datasheet_chart_digitizer import capacitance_assignment as ca
from datasheet_chart_digitizer.capacitance_axis import calibration_y_of_log_c
from datasheet_chart_digitizer.capacitance_types import (
    AxisCalibration,
    CapAnchor,
    PlotBox,
    Trace,
)

PLOT = PlotBox(0, 0, 200, 200)
LOG = AxisCalibration(
    x_min_v=0.0, x_max_v=100.0, y_min_decade=0.0, y_max_decade=4.0,
    source="test", x_ticks_v=(), y_decades=(),
)


def _flat(name: str, pf: float) -> Trace:
    y = int(round(calibration_y_of_log_c(LOG, PLOT, math.log10(pf))))
    return Trace(name=name, area=1, bbox=(0, 0, 200, 1),
                 points=[(x, y) for x in range(0, 201)])


TRACES = [_flat("Ciss", 315.0), _flat("Coss", 40.0), _flat("Crss", 2.4)]


def _verdicts(check):
    return {row["name"]: row["verdict"] for row in check["anchors"]}


def test_tolerance_is_25_percent_symmetric_in_log():
    assert ca.TABLE_CHECK_MAX_LOG10_RATIO == pytest.approx(math.log10(1.25))


def test_chart_table_disagreement_is_inconsistent():
    # AGM15N10D-G: chart Ciss ~315 pF (100 kHz) vs table 520 pF (1 MHz).
    anchors = {"Ciss": CapAnchor("Ciss", 520.0, 50.0),
               "Coss": CapAnchor("Coss", 40.0, 50.0)}
    check = ca.capacitance_table_check(TRACES, PLOT, LOG, anchors, {})
    assert check["verdict"] == "inconsistent"
    assert _verdicts(check) == {"Ciss": "inconsistent", "Coss": "consistent",
                                "Crss": "not_evaluable"}
    ciss = check["anchors"][0]
    assert ciss["table_pf"] == 520.0 and ciss["vds_v"] == 50.0
    assert ciss["log10_ratio"] == pytest.approx(math.log10(315 / 520), abs=0.02)


def test_agreement_is_consistent():
    anchors = {"Coss": CapAnchor("Coss", 42.0, 50.0)}
    check = ca.capacitance_table_check(TRACES, PLOT, LOG, anchors, {})
    assert check["verdict"] == "consistent"


def test_rejected_anchors_are_not_evaluable_not_consistent():
    reason = "value_is_a_test_condition_number"
    rejected = {"Ciss": reason, "Coss": reason}
    check = ca.capacitance_table_check(TRACES, PLOT, LOG, {}, rejected)
    assert check["verdict"] == "not_evaluable"
    assert check["anchors"][0]["reason"] == reason
    assert check["anchors"][2]["reason"] == "no_table_anchor"


def test_untrusted_axis_and_unresolved_trace_are_not_evaluable():
    anchors = {"Coss": CapAnchor("Coss", 40.0, 50.0)}
    assert ca.capacitance_table_check(TRACES, PLOT, None, anchors, {})["verdict"] == "not_evaluable"
    check = ca.capacitance_table_check(TRACES, PLOT, LOG, anchors, {}, {"Coss"})
    assert check["verdict"] == "not_evaluable"
    assert check["anchors"][1]["reason"] == "anchor_below_axis_resolution"


def test_anchor_outside_the_trace_is_not_evaluable():
    short = [Trace(name="Coss", area=1, bbox=(0, 0, 50, 1),
                   points=[(x, 150) for x in range(0, 50)])]
    anchors = {"Coss": CapAnchor("Coss", 40.0, 90.0)}
    check = ca.capacitance_table_check(short, PLOT, LOG, anchors, {})
    assert check["verdict"] == "not_evaluable"
    assert check["anchors"][1]["reason"] == "anchor_outside_trace_span"


LIB = Path(os.environ.get("DSDIG_DATASHEET_ROOT", ".")) / "datasheets"


def test_agm15n10d_g_chart_is_inconsistent_with_its_table(tmp_path):
    """End to end: the table now anchors Ciss at 520 pF; the chart (100 kHz)
    shows ~315 pF. Recorded as a reason, not a status change."""
    pdf = LIB / "agmsemi" / "AGM15N10D-G.pdf"
    if not pdf.exists():
        pytest.skip("pwr-mosfet-lib datasheets not available")
    from datasheet_chart_digitizer.annotate_pdf import annotate_pdf

    manifest = annotate_pdf(pdf, tmp_path / "out.pdf", work_dir=tmp_path / "work")
    records = [r for r in manifest["overlays"] if r["kind"] == "capacitances"]
    assert len(records) == 1
    check = records[0]["table_check"]
    assert check["verdict"] == "inconsistent"
    ciss = next(row for row in check["anchors"] if row["name"] == "Ciss")
    assert (ciss["table_pf"], ciss["vds_v"], ciss["verdict"]) == (520.0, 50.0, "inconsistent")
    assert "table_check_inconsistent" in records[0]["diagnostics"]
