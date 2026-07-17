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


def test_span_relative_conflict_fires_on_logic_level_scale(tmp_path):
    """RJK0853 known-bad: a steep logic-level curve with the pivot offset right.

    Absolute cold RMS stays ~0.1 V (under the 0.35 V absolute bound), but on a
    part whose overdrive span is <1 V that is a screaming figure-vs-figure
    conflict; the span-relative guard must refuse it.
    """
    import json

    from datasheet_chart_digitizer.transfer_tempco_curve_anchor import evaluate_part

    rows = ["curve_id,temperature_c,Vgs_V,Id_A,collapsed"]
    # Steep curve: 44 A over ~0.5 V of gate swing (like the Renesas part).
    for i in np.linspace(0.5, 44, 60):
        rows.append(f"curve_1,25,{2.3 + 0.5*(i/44)**0.5:.5f},{i:.4f},0")
        rows.append(f"curve_2,75,{2.2 + 0.5*(i/44)**0.5:.5f},{i:.4f},0")
    csv_path = tmp_path / "points.csv"
    csv_path.write_text("\n".join(rows) + "\n")
    anchor = {
        "manufacturer": "T", "part": "SYNTH-LOGIC", "id_gc_a": 40.0,
        "vpl_v": 3.05, "vpl_id_a": 40.0, "vgs_drive_v": 4.5,
        "vds_cond_v": 25.0, "vpl_source": "synthetic", "status": "test",
    }
    manifest_entry = {"points_csv": csv_path.name, "overlay": "n/a"}
    result = evaluate_part(anchor, manifest_entry, tmp_path)
    assert any("span-relative" in g or "anchor-curve-conflict" in g
               for g in result["guard_reasons"]), json.dumps(result["guard_reasons"])
