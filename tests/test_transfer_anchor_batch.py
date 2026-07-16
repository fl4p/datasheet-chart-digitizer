"""Tests for the guarded cross-vendor saturation-temperature batch."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np

from datasheet_chart_digitizer.transfer_anchor_batch import (
    ANCHORS,
    AnchorEvidence,
    _ztc_guards,
    evaluate_part,
)
from datasheet_chart_digitizer.transfer_characteristics import TransferCurve


def _write_curves(path: Path, *, chart_has_crossing: bool = True) -> None:
    path.parent.mkdir(parents=True)
    vgs = np.linspace(1.0, 5.0, 250)
    cold = 10.0 * np.maximum(vgs - 2.0, 0.0) ** 2
    if chart_has_crossing:
        hot = 7.5 * np.maximum(vgs - 1.75, 0.0) ** 2
    else:
        hot = 7.5 * np.maximum(vgs - 2.15, 0.0) ** 2
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["curve_id", "temperature_c", "Vgs_V", "Id_A"])
        for curve_id, temp, currents in (("curve_1", 25, cold), ("curve_2", 125, hot)):
            for gate, current in zip(vgs, currents):
                writer.writerow([curve_id, temp, gate, current])


def _manifest() -> dict:
    return {
        "points_csv": "points/sample.csv",
        "overlay": "overlays/sample.png",
        "axis": {
            "x": {"quantity": "Vgs", "unit": "V", "min": 1, "max": 5, "scale": "linear"},
            "y": {"quantity": "Id", "unit": "A", "min": 0, "max": 90, "scale": "linear"},
        },
    }


def test_anchor_set_uses_gate_charge_currents_and_same_part_evidence():
    by_part = {anchor.part: anchor for anchor in ANCHORS}
    assert len(by_part) == 8
    assert by_part["AOD442"].id_gc_a == 20.0
    assert by_part["AOD442"].vpl_v == 3.7
    assert by_part["AOD442"].pdf_rel == "ao/AOD442.pdf"
    assert by_part["FBG10N30BC"].qgs_nc == 2.4
    assert by_part["IPT65R033G7XTMA1-HXY"].qgs_nc == 29.0
    assert by_part["PSMN0R9-30YLD"].gate_charge_page == 9
    assert by_part["PXN017-30QL"].id_gc_a == 6.8


def test_only_datasheet_explicit_partitions_can_clear_partition_guard(tmp_path: Path):
    _write_curves(tmp_path / "points/sample.csv")
    estimated = AnchorEvidence(
        "Vendor", "PART", "vendor/PART.pdf", 1, 2, "Fig. 1",
        20.0, 20.0, 5.0, 3.0, 2.0, None, "estimated-45pct-of-Qgs", "condition",
    )
    result = evaluate_part(estimated, _manifest(), tmp_path)
    assert result["status"] == "guard-refusal"
    assert "estimated-charge-partition" in result["guard_reasons"]
    assert result["eligible_for_attachment"] is False


def test_explicit_label_cannot_clear_missing_partition_value(tmp_path: Path):
    _write_curves(tmp_path / "points/sample.csv")
    mislabeled = AnchorEvidence(
        "Vendor", "PART", "vendor/PART.pdf", 1, 2, "Fig. 1",
        20.0, 20.0, 5.0, 3.0, 2.0, None, "datasheet-explicit", "condition",
    )
    result = evaluate_part(mislabeled, _manifest(), tmp_path)
    assert mislabeled.partition_is_explicit is False
    assert "estimated-charge-partition" in result["guard_reasons"]


def test_explicit_partition_still_needs_human_review(tmp_path: Path):
    _write_curves(tmp_path / "points/sample.csv")
    explicit = AnchorEvidence(
        "Vendor", "PART", "vendor/PART.pdf", 1, 2, "Fig. 1",
        10.0, 20.0, 5.0, 3.0, 3.0, 2.0, "datasheet-explicit", "condition",
    )
    result = evaluate_part(explicit, _manifest(), tmp_path)
    assert result["eligible_for_attachment"] is False
    assert result["status"] == "fit-review-required"
    assert result["guard_reasons"] == []


def test_records_complete_axis_contract_and_strict_json(tmp_path: Path):
    _write_curves(tmp_path / "points/sample.csv")
    result = evaluate_part(ANCHORS[0], _manifest(), tmp_path)
    assert result["transfer_axis"]["x"] == {
        "quantity": "Vgs", "unit": "V", "min": 1, "max": 5, "scale": "linear"
    }
    assert result["transfer_axis"]["y"]["unit"] == "A"
    json.loads(json.dumps(result, allow_nan=False))


def test_ztc_holdout_refuses_chart_crossing_when_model_has_none():
    points = [(float(v), float(i)) for v, i in zip(np.linspace(1, 5, 50), np.linspace(0, 100, 50))]
    curves = [TransferCurve(25.0, points), TransferCurve(125.0, points)]
    fit = {
        "ztc_model_a": None,
        "ztc_chart_a": 61.667,
        "anchor": {"id_gc_a": 20.0},
    }
    reasons = _ztc_guards(fit, curves)
    assert reasons == [
        "ztc-chart-crossing-inside-reliable-range-but-model-has-no-crossing"
    ]
