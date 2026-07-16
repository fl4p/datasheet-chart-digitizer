import csv
import json

import pymupdf
from PIL import Image

from datasheet_chart_digitizer.transfer_closure import (
    audit_transfer_batch,
    audit_transfer_record,
    write_closure_report,
)


def _write_batch_record(tmp_path, *, shared_tail=False, wrong_temperature=False):
    source = tmp_path / "source.png"
    overlay = tmp_path / "overlay.png"
    points = tmp_path / "points.csv"
    Image.new("RGB", (100, 100), "white").save(source)
    Image.new("RGB", (100, 170), "white").save(overlay)
    with points.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["curve_id", "temperature_c", "Vgs_V", "Id_A"])
        for curve_id, temperature, offset in (
            ("curve_1", 25, 3.0),
            ("curve_2", 125, 4.0),
        ):
            for index in range(21):
                current = index * 5.0
                vgs = offset + 0.02 * current
                if shared_tail and curve_id == "curve_2" and current >= 60:
                    vgs = 3.0 + 0.02 * current
                writer.writerow([curve_id, temperature, vgs, current])
    record = {
        "manufacturer": "Example",
        "part": "EXAMPLE1",
        "source": source.name,
        "overlay": overlay.name,
        "points_csv": points.name,
        "plot_box_px": [10, 10, 90, 90],
        "axis": {
            "x": {"quantity": "Vgs", "unit": "V", "min": 0, "max": 10, "scale": "linear"},
            "y": {"quantity": "Id", "unit": "A", "min": 0, "max": 100, "scale": "linear"},
        },
        "expected_curves": 2,
        "allowed_source_gap_fraction": 0.05,
        "maximum_pairwise_collapse_fraction": 0.0,
        "identity_provenance": "raster-branch-tracking",
        "identity_source": {"kind": "delivered-raster", "image": source.name},
        "temperature_labels_c": [25, 125],
        "curve_identification": {
            "curve_1": {"temperature_c": 25},
            "curve_2": {"temperature_c": 25 if wrong_temperature else 125},
        },
        "status": "digitized-review-required",
        "diagnostics": [
            {
                "curve_id": "curve_1",
                "points": 21,
                "y_span_fraction": 1.0,
                "monotone_violation_fraction": 0.0,
                "maximum_source_gap_fraction": 0.01,
            },
            {
                "curve_id": "curve_2",
                "points": 21,
                "y_span_fraction": 1.0,
                "monotone_violation_fraction": 0.0,
                "maximum_source_gap_fraction": 0.01,
            },
        ],
    }
    return record


def test_closure_accepts_complete_independent_curves(tmp_path):
    record = _write_batch_record(tmp_path)

    closure = audit_transfer_record(
        tmp_path,
        record,
        overlay_text="DIGITIZED X AXIS Y AXIS VGS ID",
    )

    assert closure["decision"] == "accepted"
    assert all(gate["passed"] for gate in closure["gates"])


def test_closure_rejects_a_long_forced_shared_tail(tmp_path):
    record = _write_batch_record(tmp_path, shared_tail=True)

    closure = audit_transfer_record(
        tmp_path,
        record,
        overlay_text="DIGITIZED X AXIS Y AXIS VGS ID",
    )

    assert closure["decision"] == "rejected"
    independence = next(
        gate for gate in closure["gates"] if gate["name"] == "branch-independence"
    )
    assert not independence["passed"]
    assert "exactly shared run=0.406" in independence["detail"]


def test_closure_rejects_inconsistent_temperature_identity(tmp_path):
    record = _write_batch_record(tmp_path, wrong_temperature=True)

    closure = audit_transfer_record(
        tmp_path,
        record,
        overlay_text="DIGITIZED X AXIS Y AXIS VGS ID",
    )

    identity = next(
        gate
        for gate in closure["gates"]
        if gate["name"] == "temperature-identity-consistent"
    )
    assert not identity["passed"]


def test_closure_rejects_missing_collapse_evidence(tmp_path):
    record = _write_batch_record(tmp_path)
    record.pop("maximum_pairwise_collapse_fraction")

    closure = audit_transfer_record(
        tmp_path,
        record,
        overlay_text="DIGITIZED X AXIS Y AXIS VGS ID",
    )

    independence = next(
        gate for gate in closure["gates"] if gate["name"] == "branch-independence"
    )
    assert closure["decision"] == "rejected"
    assert not independence["passed"]
    assert "near-collapse=nan" in independence["detail"]


def test_closure_rejects_missing_plot_calibration_box(tmp_path):
    record = _write_batch_record(tmp_path)
    record.pop("plot_box_px")

    closure = audit_transfer_record(
        tmp_path,
        record,
        overlay_text="DIGITIZED X AXIS Y AXIS VGS ID",
    )

    plot_gate = next(
        gate for gate in closure["gates"] if gate["name"] == "plot-calibration-box"
    )
    assert closure["decision"] == "rejected"
    assert not plot_gate["passed"]


def test_closure_rejects_nonexistent_vector_pdf_page(tmp_path):
    record = _write_batch_record(tmp_path)
    pdf = tmp_path / "source.pdf"
    with pymupdf.open() as document:
        document.new_page()
        document.save(pdf)
    record["identity_provenance"] = "independent-pdf-vector-paths"
    record["identity_source"] = {
        "kind": "pdf-vector-paths",
        "pdf": str(pdf),
        "page": 2,
    }

    closure = audit_transfer_record(
        tmp_path,
        record,
        overlay_text="DIGITIZED X AXIS Y AXIS VGS ID",
    )

    provenance = next(
        gate for gate in closure["gates"] if gate["name"] == "identity-provenance"
    )
    assert closure["decision"] == "rejected"
    assert not provenance["passed"]
    assert "page=2/1" in provenance["detail"]


def test_closure_rejects_nonfinite_axis_bound(tmp_path):
    record = _write_batch_record(tmp_path)
    record["axis"]["x"]["max"] = float("inf")

    closure = audit_transfer_record(
        tmp_path,
        record,
        overlay_text="DIGITIZED X AXIS Y AXIS VGS ID",
    )

    axis_gate = next(
        gate for gate in closure["gates"] if gate["name"] == "axis-metadata-complete"
    )
    assert closure["decision"] == "rejected"
    assert not axis_gate["passed"]


def test_closure_rejects_duplicate_diagnostic_curve_id(tmp_path):
    record = _write_batch_record(tmp_path)
    record["diagnostics"][1]["curve_id"] = "curve_1"

    closure = audit_transfer_record(
        tmp_path,
        record,
        overlay_text="DIGITIZED X AXIS Y AXIS VGS ID",
    )

    quality_gate = next(
        gate for gate in closure["gates"] if gate["name"] == "trace-quality"
    )
    assert closure["decision"] == "rejected"
    assert not quality_gate["passed"]


def test_closure_rejects_boolean_plot_coordinate(tmp_path):
    record = _write_batch_record(tmp_path)
    record["plot_box_px"][0] = True

    closure = audit_transfer_record(
        tmp_path,
        record,
        overlay_text="DIGITIZED X AXIS Y AXIS VGS ID",
    )

    plot_gate = next(
        gate for gate in closure["gates"] if gate["name"] == "plot-calibration-box"
    )
    assert closure["decision"] == "rejected"
    assert not plot_gate["passed"]


def test_batch_writes_separate_curated_manifest_and_visual_report(tmp_path):
    record = _write_batch_record(tmp_path)
    (tmp_path / "manifest.json").write_text(json.dumps([record]))

    closure, promoted = audit_transfer_batch(tmp_path, use_ocr=False)
    write_closure_report(tmp_path, closure, promoted)

    assert promoted[0]["digitization_status"] == "digitized-review-required"
    assert promoted[0]["status"] == "curated"
    assert json.loads((tmp_path / "closure25.json").read_text())["accepted"] == 1
    report = (tmp_path / "closure25.md").read_text()
    assert "![EXAMPLE1](overlay.png)" in report
    assert "Vgs 0…10 V; Id 0…100 A linear" in report
