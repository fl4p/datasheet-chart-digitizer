"""Census tools (tools/census): the rules that turn runs into tables."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "census"))

import chart_medium_census as medium  # noqa: E402
import refusal_census  # noqa: E402
import refusal_report  # noqa: E402


def test_medium_rule_keeps_filled_outline_charts_apart_from_empty_crops():
    assert medium.classify_medium(9.0, 0.0, 0) == "vector"
    assert medium.classify_medium(0.0, 0.95, 0) == "raster"
    assert medium.classify_medium(9.0, 0.95, 0) == "both"
    assert medium.classify_medium(0.0, 0.0, 700) == "vector_filled"
    # a table's cell fills and a bare frame are not a chart medium
    assert medium.classify_medium(0.0, 0.0, 21) == "none"
    assert medium.classify_medium(2.8, 0.1, 0) == "none"


def test_generated_pdf_copies_are_not_canonical(tmp_path):
    vendor = tmp_path / "acme"
    vendor.mkdir()
    for name in ("A.pdf", "A.pdf.gs.pdf", "A.pdf.nop.csv", "B.PDF"):
        (vendor / name).write_bytes(b"")
    (tmp_path / "_samples").mkdir()
    (tmp_path / "_samples" / "C.pdf").write_bytes(b"")
    found = medium.canonical_pdfs(tmp_path)
    assert {p.name for p in found["acme"]} == {"A.pdf", "B.PDF"}
    assert "_samples" not in found


def test_a_failed_class_run_stays_a_row():
    raw = {
        "results": [{"class": "capacitance", "page": 4, "diagram": 7,
                     "panel": {"kind": "capacitances", "text_source": "pdftotext", "title": "C", "crop_box_pt": [0, 0, 1, 1]},
                     "result": {"status": "unverified", "status_reasons": ["axis_calibration_untrusted"],
                                "extraction_method": "raster", "diagnostics": {"nested": 1}}}],
        "errors": [{"class": "transfer", "page": 3, "diagram": 5, "error": "X axis (VGS): no monotone full-span tick-label row"}],
    }
    rows = refusal_census.compact(raw)
    assert [r["outcome"] for r in rows] == ["result", "error"]
    assert rows[0]["reasons"] == {"status_reasons": ["axis_calibration_untrusted"]}
    assert rows[0]["medium"] == {"extraction_method": "raster"}
    assert refusal_report.outcome_of(rows[1]) == "refused"


def test_stage_comes_from_the_reason_text_not_the_field_name():
    chart = {"outcome": "result", "status": "refused",
             "reasons": {"binding_error": "trace: no full-span vector curves", "diagnostics": ["no_full_span_vector_curve"]}}
    assert refusal_report.stage_of(chart)[0] == "curve tracing"
    legend = {"outcome": "result", "status": "refused", "reasons": {"binding_error": "legend: no local VGS labels"}}
    assert refusal_report.stage_of(legend)[0] == "label / curve identity"


def test_unmatched_and_missing_reasons_are_unclassified_not_a_pass():
    odd = {"outcome": "result", "status": "refused", "reasons": {"diagnostics": ["rdson_span_below_threshold"]}}
    assert refusal_report.stage_of(odd)[0] == "unclassified"
    empty = {"outcome": "result", "status": None, "reasons": {}}
    assert refusal_report.outcome_of(empty) == "refused"
    assert refusal_report.stage_of(empty) == ("unclassified", "(no reason given)")
    assert refusal_report.outcome_of({"outcome": "result", "status": "unverified", "reasons": {}}) == "served, unverified"
    assert refusal_report.outcome_of({"outcome": "result", "status": "ok", "reasons": {}}) == "pass"
