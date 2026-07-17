import numpy as np
import pytest

from datasheet_chart_digitizer.transfer_characteristics import TransferCurve
from datasheet_chart_digitizer.transfer_tempco_curve_anchor import (
    drive_gate,
    fit_vth_from_cold_curve,
)


def _law_curve(tj_c, vth, k, p=2.0, vmax=10.0, n=200):
    vgs = np.linspace(vth + 0.05, vmax, n)
    ids = k * (vgs - vth) ** p
    return TransferCurve(tj_c, list(zip(vgs.tolist(), ids.tolist())))


def test_vth_identified_from_synthetic_law_with_exact_pivot():
    vth_true, id_pl = 4.0, 50.0
    vpl = 5.5
    k_true = id_pl / (vpl - vth_true) ** 2
    cold = _law_curve(25.0, vth_true, k_true)
    fit = fit_vth_from_cold_curve(cold, vpl, id_pl)
    assert fit["vth_eff_v"] == pytest.approx(vth_true, abs=0.02)
    # The pivot is exact by construction for the returned pair.
    assert fit["k_a_per_vp"] * (vpl - fit["vth_eff_v"]) ** 2 == pytest.approx(id_pl)
    assert fit["cold_fit_rms_v"] < 0.01


def test_anchor_curve_conflict_yields_large_residual():
    # Curve obeys Vth=4 but the claimed pivot says the law passes (5.5V, 5A):
    # an order of magnitude below the real current there. No Vth can fix it.
    vth_true, vpl = 4.0, 5.5
    k_true = 50.0 / (vpl - vth_true) ** 2
    cold = _law_curve(25.0, vth_true, k_true, vmax=6.0)
    fit = fit_vth_from_cold_curve(cold, vpl, id_pl_a=5.0)
    assert fit["cold_fit_rms_v"] > 0.3, "conflicting pivot must not fit quietly"


def test_drive_gate_refuses_non_si_and_unresolved_drives():
    assert drive_gate({"vgs_drive_v": "-4/+18"}) is not None
    assert drive_gate({"vgs_drive_v": None}) is not None
    assert drive_gate({"vgs_drive_v": 10}) is None
    assert drive_gate({"vgs_drive_v": 4.5}) is None  # logic-level Si allowed
    assert drive_gate({"vgs_drive_v": 5, "note": "GaN-on-Si (EPC)"}) is not None


def test_collapsed_points_are_excluded(tmp_path):
    from datasheet_chart_digitizer.transfer_tempco_curve_anchor import load_flagged_curves

    csv_path = tmp_path / "points.csv"
    rows = ["curve_id,temperature_c,Vgs_V,Id_A,collapsed"]
    for vgs in np.linspace(4.1, 9.9, 30):
        rows.append(f"curve_1,25,{vgs:.4f},{20*(vgs-4)**2:.4f},0")
        flag = 1 if vgs > 8.0 else 0
        rows.append(f"curve_2,175,{vgs - 0.8:.4f},{18*(vgs-4)**2:.4f},{flag}")
    csv_path.write_text("\n".join(rows) + "\n")
    curves, stats = load_flagged_curves(csv_path)
    assert stats["excluded_collapsed_hot"] > 0
    hot = curves[1]
    assert max(v for v, _i in hot.points) <= 8.0 - 0.8 + 1e-9


def _write_synth_csv(tmp_path, vth25=2.3, k25=176.0, dvth=-0.002, dlnk=-0.002,
                     hot_t=75.0, imax=44.0, offset_pivot=False):
    dt = hot_t - 25.0
    vth_hot = vth25 + dvth * dt
    k_hot = k25 * np.exp(dlnk * dt)
    rows = ["curve_id,temperature_c,Vgs_V,Id_A,collapsed"]
    for i in np.linspace(0.5, imax, 60):
        rows.append(f"curve_1,25,{vth25 + (i/k25)**0.5:.5f},{i:.4f},0")
        rows.append(f"curve_2,{hot_t:g},{vth_hot + (i/k_hot)**0.5:.5f},{i:.4f},0")
    csv_path = tmp_path / "points.csv"
    csv_path.write_text("\n".join(rows) + "\n")
    return csv_path


def test_ratio_mode_recovers_tempco_despite_offset_pivot(tmp_path):
    """RJK0853-geometry known-bad turned informative: the pivot sits 0.25 V
    right of the curve. Ratio mode must (a) recover the true temperature
    transform from the curves, (b) record the pivot discrepancy with the
    absolute-anchor badge withheld, and (c) NOT refuse the transform."""
    from datasheet_chart_digitizer.transfer_tempco_curve_anchor import evaluate_part

    csv_path = _write_synth_csv(tmp_path, dvth=-0.002)
    anchor = {
        "manufacturer": "T", "part": "SYNTH-LOGIC", "id_gc_a": 40.0,
        "vpl_v": 3.05, "vpl_id_a": 40.0, "vgs_drive_v": 4.5,
        "vds_cond_v": 25.0, "vpl_source": "synthetic", "status": "test",
    }
    result = evaluate_part(anchor, {"points_csv": csv_path.name, "overlay": "n/a"}, tmp_path)
    assert result["status"] == "fit-review-required", result["guard_reasons"]
    fit = result["fit"]
    assert fit["mode"] == "temperature-transform"
    assert fit["d_vth_eff_v_per_k"] == pytest.approx(-0.002, abs=3e-4)
    pivot = fit["pivot_cross_check"]
    assert not pivot["absolute_anchor_eligible"]
    # true curve reaches 40 A at ~2.777 V; the claimed chart pivot is 3.05 V
    assert pivot["discrepancy_v"] == pytest.approx(3.05 - (2.3 + (40 / 176.0) ** 0.5), abs=0.03)


def test_ratio_mode_badges_consistent_pivot(tmp_path):
    from datasheet_chart_digitizer.transfer_tempco_curve_anchor import evaluate_part

    csv_path = _write_synth_csv(tmp_path)
    true_vpl = 2.3 + (40 / 176.0) ** 0.5
    anchor = {
        "manufacturer": "T", "part": "SYNTH-OK", "id_gc_a": 40.0,
        "vpl_v": round(true_vpl, 3), "vpl_id_a": 40.0, "vgs_drive_v": 10,
        "vds_cond_v": 25.0, "vpl_source": "synthetic", "status": "test",
    }
    result = evaluate_part(anchor, {"points_csv": csv_path.name, "overlay": "n/a"}, tmp_path)
    assert result["status"] == "fit-review-required", result["guard_reasons"]
    assert result["fit"]["pivot_cross_check"]["absolute_anchor_eligible"]


def test_matched_shift_table_matches_synthetic_law(tmp_path):
    from datasheet_chart_digitizer.transfer_tempco_curve_anchor import evaluate_part

    dvth, dlnk, hot_t, k25 = -0.002, -0.002, 75.0, 176.0
    csv_path = _write_synth_csv(tmp_path, dvth=dvth, dlnk=dlnk, hot_t=hot_t, k25=k25)
    anchor = {
        "manufacturer": "T", "part": "SYNTH-TBL", "id_gc_a": 40.0,
        "vpl_v": 2.8, "vpl_id_a": 40.0, "vgs_drive_v": 10,
        "vds_cond_v": 25.0, "vpl_source": "synthetic", "status": "test",
    }
    result = evaluate_part(anchor, {"points_csv": csv_path.name, "overlay": "n/a"}, tmp_path)
    table = result["fit"]["matched_shift_table"]
    assert table["tj_hot_c"] == hot_t
    ids = np.array(table["id_a"])
    deltas = np.array(table["delta_vgs_v"])
    dt = hot_t - 25.0
    k_hot = k25 * np.exp(dlnk * dt)
    expected = dvth * dt + np.sqrt(ids) * (1 / np.sqrt(k_hot) - 1 / np.sqrt(k25))
    assert np.allclose(deltas, expected, atol=5e-3)
