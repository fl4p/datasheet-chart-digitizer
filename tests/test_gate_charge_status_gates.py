"""Fail-closed status gates: table lift, OCR recalibration and dual-y identity."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pymupdf
import pytest

from datasheet_chart_digitizer import gate_charge as gc
from datasheet_chart_digitizer.gate_charge_branch import BRANCH_HOP_DIAGNOSTIC
from datasheet_chart_digitizer.gate_charge_spec_table import QgSpecRow
from datasheet_chart_digitizer.gate_charge_table_check import read_qg_at_vgs

X0, Y0, X1, Y1 = 150.0, 300.0, 400.0, 500.0
FALLBACK = "vpl_from_slope_fallback"


PLATEAU = [(0, 0), (6, 4.0), (12, 4.0), (22, 10.0)]


def _chart(path, right_gap=None, curve=PLATEAU, mask=None):
    """VGS 0..10 (labels 20 pt left of the frame), plateau at 4.00 V. With
    *right_gap*, a VDS 0..15 column that many pt right of the frame; *curve*
    (Qg nC, VGS V) points; *mask* ((q0, v0), (q1, v1)): an opaque in-plot
    label over that region."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((150, 280), "Figure 7. Gate Charge", fontsize=9)
    for i in range(6):
        x = X0 + i * (X1 - X0) / 5
        page.draw_line((x, Y0), (x, Y1), color=(0, 0, 0), width=1.0 if i in (0, 5) else 0.4)
        t = str(5 * i)
        page.insert_text((x - pymupdf.get_text_length(t, fontsize=8) / 2, Y1 + 11), t, fontsize=8)
    page.insert_text((215, Y1 + 24), "Qg, Total Gate Charge (nC)", fontsize=8)
    for j in range(6):
        y = Y1 - j * (Y1 - Y0) / 5
        page.draw_line((X0, y), (X1, y), color=(0, 0, 0), width=1.0 if j in (0, 5) else 0.4)
        t = str(2 * j)
        page.insert_text((X0 - 20 - pymupdf.get_text_length(t, fontsize=8), y + 3), t, fontsize=8)
        if right_gap is not None:
            page.insert_text((X1 + right_gap, y + 3), str(3 * j), fontsize=8)
    page.insert_text((X0 - 40, Y0 - 12), "VGS (V)", fontsize=8)
    if right_gap is not None:
        page.insert_text((X1 - 5, Y0 - 12), "VDS (V)", fontsize=8)
    page.insert_text((300, 330), "VDS = 40 V, ID = 20 A", fontsize=7)

    def p(q, v):
        return (X0 + q / 25 * (X1 - X0), Y1 - v / 10 * (Y1 - Y0))

    page.draw_polyline([p(q, v) for q, v in curve], color=(0, 0, 0), width=1.5)
    if mask is not None:
        box = pymupdf.Rect(p(mask[0][0], mask[1][1]), p(mask[1][0], mask[0][1]))
        page.draw_rect(box, color=None, fill=(1, 1, 1))
        page.insert_text((box.x0 + 1, box.y1 - 2), "VDD=10V", fontsize=7)
    doc.save(path)
    return path


def _only(pdf):
    results, errors = gc.digitize_gate_charge_fail_closed(pdf, dpi=220, finder_dpi=220)
    assert not errors and len(results) == 1
    return results[0]


def test_single_axis_control_is_ok(tmp_path):
    result = _only(_chart(tmp_path / "single.pdf"))
    assert result.status == "ok"
    assert result.vpl == pytest.approx(4.0, abs=0.1)
    assert FALLBACK not in result.diagnostics


def test_dual_y_nearer_unverified_column_is_not_ok(tmp_path):
    # the VDS column is nearer the frame and wins; read through it Vpl is 6.0 V
    result = _only(_chart(tmp_path / "dual.pdf", right_gap=3.0))
    assert result.status != "ok"
    assert result.to_manifest()["vpl"] is None


def test_table_agreement_never_lifts_a_failed_vgs_check(tmp_path):
    pdf = _chart(tmp_path / "single.pdf")
    g = _only(pdf)
    read, _how = read_qg_at_vgs(g.curve_px, g.x_ticks_px, g.y_ticks_px, 10.0)
    y_grid = dict(g.y_grid)
    y_grid["anchoring"] = {**(y_grid.get("anchoring") or {}), "binding": "ordinal_rules"}
    y_grid["grid_check"] = {**y_grid["grid_check"], "status": "failed",
                            "reason": "VGS: served calibration misses the '10' line by +40px"}
    bad = dataclasses.replace(
        g, status="low_confidence", y_pending_table=True, y_grid=y_grid,
        diagnostics=(*g.diagnostics, "y_axis_grid_check_failed"),
    )
    row = QgSpecRow(page=1, typ_nc=round(read, 1), typ_resolution_nc=0.1, vgs_v=10.0, vdrain_v=40.0,
                    vdrain_name="VDS", id_a=20.0, row_text="Qg Total gate charge VGS=10V VDS=40V",
                    condition_text="", y_pt=100.0)
    with pymupdf.open(pdf) as doc:
        out = gc._check_served_qg_axis(doc[0], bad, spec_rows=lambda: [row], split_rows=lambda: [])
    assert "qg_table_check_agrees" in out.diagnostics
    assert out.status == "low_confidence"
    assert out.y_grid["grid_check"]["status"] == "failed"
    assert not gc._y_pending_table(("y_axis_grid_check_failed",), y_grid)


def _recalibrate(monkeypatch, pdf, *, value_scale=1.0, extra=()):
    """Run the bounded-OCR recalibration path on a first pass carrying
    ``axis_assumed_0_10`` (+ *extra*), with label values scaled by *value_scale*."""
    real = gc._digitize_panel
    marker = object()

    def first_pass(pdf_, doc, panel, dpi, page_text):
        if page_text is marker:
            return None  # bounded OCR result untrusted: recalibrate instead
        r = real(pdf_, doc, panel, dpi, page_text)
        return dataclasses.replace(
            r, status="axis_assumed", y_pending_table=True,
            diagnostics=(*r.diagnostics, "axis_assumed_0_10", *extra),
        )

    def recalibrate(result, _page_text):
        ticks = tuple((value * value_scale, px) for value, px in result.y_ticks_px)
        return 0.0, ticks, "nC"

    monkeypatch.setattr(gc, "_digitize_panel", first_pass)
    monkeypatch.setattr(gc, "bounded_gate_axis_ocr", lambda *a, **k: marker)
    monkeypatch.setattr(gc, "recalibrate_gate_result", recalibrate)
    monkeypatch.setattr(gc, "_recover_missing_axis_labels", lambda pdf_, page, r, **k: r)
    return _only(pdf)


def test_recalibration_rechecks_the_vpl_value_gates(tmp_path, monkeypatch):
    out = _recalibrate(monkeypatch, _chart(tmp_path / "single.pdf"), value_scale=4.0)
    assert out.vpl == pytest.approx(16.0, abs=0.4)
    assert "vpl_outside_expected_range" in out.diagnostics
    assert out.status == "low_confidence"
    assert not out.y_pending_table


def test_recalibration_keeps_every_curve_gate(tmp_path, monkeypatch):
    out = _recalibrate(monkeypatch, _chart(tmp_path / "single.pdf"), extra=(BRANCH_HOP_DIAGNOSTIC,))
    assert out.vpl == pytest.approx(4.0, abs=0.1)
    assert out.status == "low_confidence"


def test_recalibration_keeps_the_slope_fallback(tmp_path, monkeypatch):
    result = _recalibrate(monkeypatch, _chart(tmp_path / "line.pdf", curve=[(0, 0), (20, 10.0)]))
    assert result.status != "ok", (result.vpl, result.diagnostics)
    assert FALLBACK in result.diagnostics


def test_recalibration_never_serves_an_unresolved_slope_fit(tmp_path, monkeypatch):
    # the slope fit's pixel with a non-finite first read: recalibration re-reads it
    real = gc._estimate_vpl_from_curve
    monkeypatch.setattr(gc, "_estimate_vpl_from_curve",
                        lambda *a: (float("nan"), real(*a)[1], True))
    result = _recalibrate(monkeypatch, _chart(tmp_path / "single.pdf"))
    assert result.status != "ok", (result.vpl, result.diagnostics)
    assert FALLBACK in result.diagnostics


def test_recalibration_control_is_ok(tmp_path, monkeypatch):
    out = _recalibrate(monkeypatch, _chart(tmp_path / "single.pdf"))
    assert out.vpl == pytest.approx(4.0, abs=0.1)
    assert out.status == "ok"


def _ordinal_x_pending(pdf, monkeypatch, x_status):
    """The chart with both axes bound in rule order: VGS check unverified, the
    Qg check forced to *x_status*, and a spec-table Qg row the curve agrees with."""
    g = _only(pdf)
    read, _how = read_qg_at_vgs(g.curve_px, g.x_ticks_px, g.y_ticks_px, 10.0)
    y_grid = dict(g.y_grid)
    y_grid["anchoring"] = {**(y_grid.get("anchoring") or {}), "binding": "ordinal_rules"}
    y_grid["grid_check"] = {**y_grid["grid_check"], "status": "unverified"}
    x_grid = dict(g.x_grid)
    x_grid["anchoring"] = {**(x_grid.get("anchoring") or {}), "binding": "ordinal_rules"}
    pending = dataclasses.replace(
        g, status="low_confidence", y_pending_table=True, y_grid=y_grid, x_grid=x_grid,
        diagnostics=(*g.diagnostics, "y_axis_grid_check_unverified"),
    )
    monkeypatch.setattr(gc, "check_gate_x_on_grid", lambda *a, **k: gc.GridCheck(
        x_status, f"Qg: forced {x_status}", 1.5, ()))
    row = QgSpecRow(page=1, typ_nc=round(read, 1), typ_resolution_nc=0.1, vgs_v=10.0, vdrain_v=40.0,
                    vdrain_name="VDS", id_a=20.0, row_text="Qg Total gate charge VGS=10V VDS=40V",
                    condition_text="", y_pt=100.0)
    return pending, row


@pytest.mark.parametrize("x_status, lifted", [("unverified", True), ("failed", False)])
def test_table_agreement_lifts_only_an_unverified_qg_check(tmp_path, monkeypatch, x_status, lifted):
    pdf = _chart(tmp_path / "single.pdf")
    pending, row = _ordinal_x_pending(pdf, monkeypatch, x_status)
    assert gc._y_pending_table(pending.diagnostics, pending.y_grid)
    with pymupdf.open(pdf) as doc:
        out = gc._check_served_qg_axis(doc[0], pending, spec_rows=lambda: [row], split_rows=lambda: [])
    assert "qg_table_check_agrees" in out.diagnostics
    assert out.x_grid["grid_check"]["status"] == ("verified" if lifted else "failed")
    assert out.y_grid["grid_check"]["status"] == ("verified" if lifted else "unverified")
    assert out.status == ("ok" if lifted else "low_confidence")
    assert (out.to_manifest()["vpl"] is not None) == lifted


@pytest.mark.parametrize("x_status, lifted", [("unverified", True), ("failed", False)])
def test_qgs_qgd_agreement_lifts_only_an_unverified_qg_check(tmp_path, monkeypatch, x_status, lifted):
    # the plateau runs 6..12 nC: Qgs 6, Qgd 6; table Qg 6 % high (contradicts, within the 10 % band)
    from datasheet_chart_digitizer.gate_charge_spec_table import ChargeSplitRow

    pdf = _chart(tmp_path / "single.pdf")
    pending, row = _ordinal_x_pending(pdf, monkeypatch, x_status)
    row = dataclasses.replace(row, typ_nc=round(row.typ_nc * 1.06, 1))
    splits = [ChargeSplitRow("qgs", 1, 6.0, 0.1, "Qgs Gate to Source Charge 6.0 nC", 110.0, True),
              ChargeSplitRow("qgd", 1, 6.0, 0.1, "Qgd Gate to Drain Charge 6.0 nC", 120.0, True)]
    with pymupdf.open(pdf) as doc:
        out = gc._check_served_qg_axis(doc[0], pending, spec_rows=lambda: [row], split_rows=lambda: splits)
    assert "qg_table_check_contradicts" in out.diagnostics
    if lifted:
        assert out.qgs_qgd_check["status"] == "agrees", out.qgs_qgd_check
    assert out.x_grid["grid_check"]["status"] == ("verified" if lifted else "failed")
    assert out.status == ("ok" if lifted else "low_confidence")


# known-bad on b228b0a: both served "ok", at 2.36 V (truth 4.00 V) and 8.0 V (no plateau)
@pytest.mark.parametrize("kw", [
    {"mask": ((5.5, 3.3), (12.5, 4.7))},  # a label over the whole plateau
    {"curve": [(0, 0), (20, 10.0)]},  # no plateau at all
], ids=["plateau_under_label", "no_plateau"])
def test_vpl_from_the_slope_fit_is_not_ok(tmp_path, kw):
    result = _only(_chart(tmp_path / "c.pdf", **kw))
    assert result.status == "low_confidence", (result.vpl, result.diagnostics)
    assert result.to_manifest()["vpl"] is None
    assert FALLBACK in result.diagnostics


def test_growing_plateau_occlusion_never_serves_a_wrong_vpl(tmp_path):
    # monotone: whatever the label hides, "ok" always carries the 4.00 V plateau
    served = []
    for k, q1 in enumerate((7.0, 8.5, 10.0, 11.5, 12.5, 14.0)):
        result = _only(_chart(tmp_path / f"m{k}.pdf", mask=((5.5, 3.3), (q1, 4.7))))
        if result.status == "ok":
            assert result.vpl == pytest.approx(4.0, abs=0.1), (q1, result.vpl)
            served.append(q1)
        else:
            assert result.to_manifest()["vpl"] is None
    assert served and served[0] == 7.0, served  # enough plateau left: read on it
    assert result.status != "ok"  # far tail: the whole plateau hidden


def test_table_agreement_never_lifts_a_slope_fit_vpl(tmp_path, monkeypatch):
    pdf = _chart(tmp_path / "line.pdf", curve=[(0, 0), (20, 10.0)])
    pending, row = _ordinal_x_pending(pdf, monkeypatch, "unverified")
    pending = dataclasses.replace(pending, y_pending_table=gc._y_pending_table(pending.diagnostics, pending.y_grid))
    with pymupdf.open(pdf) as doc:
        out = gc._check_served_qg_axis(doc[0], pending, spec_rows=lambda: [row], split_rows=lambda: [])
    assert out.x_grid["grid_check"]["status"] == "verified"  # the axes are lifted ...
    assert out.status == "low_confidence", (out.vpl, out.diagnostics)  # ... the Vpl is not
    assert out.to_manifest()["vpl"] is None
    assert FALLBACK in out.diagnostics


SYN1_0153 = Path("/Users/fab/dev/pv/ee/datasheet-chart-digitizer/out/synth-charts/set1/pdf/page_0103.pdf")


@pytest.mark.skipif(not SYN1_0153.exists(), reason="synthetic set1 not present")
def test_syn1_0153_label_beside_a_short_plateau_is_not_ok():
    # truth Vpl 3.146 V; the slope fit reads 1.76 V (b228b0a served it "ok")
    result = _only(SYN1_0153)
    assert result.status == "low_confidence", (result.vpl, result.diagnostics)
    assert FALLBACK in result.diagnostics
