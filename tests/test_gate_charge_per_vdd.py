"""Per-VDD gate-charge curves: separation, label binding, plateau, contract.

Unit tests draw synthetic vector pages (pt = px at scale 1, crop origin 0,0);
end-to-end tests use fetlib corpus PDFs and skip when one is missing.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from datasheet_chart_digitizer.gate_charge import digitize_gate_charge
from datasheet_chart_digitizer.gate_charge_per_vdd import serialize

DS = Path("/Users/fab/dev/pv/pwr-mosfet-lib/datasheets")
PSMN5R3 = DS / "nxp/PSMN5R3-25MLD.pdf"
IPB180 = DS / "infineon/IPB180N04S401ATMA1.pdf"
ISC040 = DS / "infineon/ISC040N10NM8.pdf"
FDB86102 = DS / "onsemi/FDB86102LZ.pdf"
FDP2710 = DS / "onsemi/FDP2710.pdf"
FDMS4D5 = DS / "onsemi/FDMS4D5N08LC.pdf"
SIJ482 = DS / "vishay/SIJ482DP-T1-GE3.pdf"
NTMFS3D5 = DS / "onsemi/NTMFS3D5N08XT1G.pdf"


class ContractTests(unittest.TestCase):
    PAYLOAD = {
        "method": "vector_paths", "curve_count": 2, "binding": "partly_bound", "physics": "not_evaluable",
        "labels": [], "plateau": {"shared": True, "vpl": 3.0, "vpl_y_px": 250, "status": "ok"},
        "shared_curve_px": [[0, 400]], "legacy_curve": {"index": 0, "rule": "unchanged"}, "diagnostics": [],
        "curves": [
            {"index": 0, "vdd_v": 10.0, "status": "ok", "curve_px": [[0, 400]], "vpl": 3.0, "vpl_y_px": 250, "plateau_qg": [1, 2]},
            {"index": 1, "vdd_v": None, "status": "unbound", "curve_px": [[0, 400]], "vpl": 3.0, "vpl_y_px": 250, "plateau_qg": [1, 3]},
        ],
    }

    def test_unbound_curve_points_are_withheld(self) -> None:
        out = serialize(self.PAYLOAD, True)
        self.assertEqual(out["curves"][0]["curve_px"], [[0, 400]])
        self.assertEqual(out["curves"][1]["curve_px"], [])
        self.assertIsNone(out["curves"][1]["vpl"])
        self.assertEqual(out["plateau"]["vpl"], 3.0)

    def test_nothing_is_served_when_the_chart_is_not_ok(self) -> None:
        payload = {**self.PAYLOAD, "curves": [dict(c, status="withheld") for c in self.PAYLOAD["curves"]]}
        out = serialize(payload, False)
        self.assertIsNone(out["plateau"]["vpl"])
        self.assertEqual(out["shared_curve_px"], [])
        self.assertTrue(all(c["curve_px"] == [] and c["vpl"] is None for c in out["curves"]))


def _result(pdf, page, diagram):
    if not pdf.exists():
        raise unittest.SkipTest(f"missing local corpus fixture: {pdf}")
    found = [r for r in digitize_gate_charge(pdf, dpi=220, finder_dpi=220) if (r.panel.page, r.panel.diagram) == (page, diagram)]
    assert len(found) == 1, found
    return found[0]


def _bound(result):
    return {c["vdd_v"]: c["index"] for c in result.per_vdd["curves"] if c["vdd_v"] is not None}


class PerVddEndToEndTests(unittest.TestCase):
    def test_psmn5r3_three_vds_curves_by_leader_and_vpl_ok(self) -> None:
        r = _result(PSMN5R3, 8, 12)
        self.assertEqual(r.status, "ok")
        self.assertEqual(_bound(r), {5.0: 0, 12.0: 1, 20.0: 2})
        manifest = r.to_manifest()["per_vdd"]
        self.assertEqual(manifest["plateau"]["status"], "ok")
        self.assertAlmostEqual(manifest["plateau"]["vpl"], 2.99, delta=0.03)
        self.assertTrue(all(c["status"] == "ok" and len(c["curve_px"]) > 100 for c in manifest["curves"]))

    def test_ipb180_two_vdd_by_label_side(self) -> None:
        r = _result(IPB180, 7, 15)
        self.assertEqual(r.status, "ok")
        self.assertEqual(_bound(r), {8.0: 0, 32.0: 1})
        self.assertEqual(r.per_vdd["method"], "vector_pen_strokes")

    def test_isc040_legend_by_dash_style_and_plateau_not_shared(self) -> None:
        r = _result(ISC040, 10, 15)
        self.assertEqual(_bound(r), {20.0: 0, 50.0: 1, 80.0: 2})
        self.assertEqual({c["binding_rule"] for c in r.per_vdd["curves"]}, {"legend"})
        self.assertFalse(r.per_vdd["plateau"]["shared"])
        vpls = [c["vpl"] for c in r.per_vdd["curves"]]
        self.assertEqual(vpls, sorted(vpls, reverse=True))  # higher VDD, lower plateau here

    def test_fdb86102_arrow_crossing_a_curve(self) -> None:
        r = _result(FDB86102, 5, 7)
        self.assertEqual(_bound(r), {25.0: 0, 50.0: 1, 75.0: 2})
        rules = {c["vdd_v"]: c["binding_rule"] for c in r.per_vdd["curves"]}
        self.assertEqual(rules[50.0], "leader")

    def test_fdp2710_switching_legacy_kept_but_flagged(self) -> None:
        r = _result(FDP2710, 4, 6)
        self.assertEqual(r.per_vdd["legacy_curve"]["rule"], "legacy_curve_switches_source_curves")
        self.assertFalse(r.per_vdd["plateau"]["shared"])
        self.assertEqual(_bound(r), {50.0: 0, 125.0: 1, 200.0: 2})

    def test_fdms4d5_rank_contradiction_refuses(self) -> None:
        r = _result(FDMS4D5, 4, 7)
        self.assertEqual(r.per_vdd["binding"], "refused")
        self.assertEqual(r.per_vdd["physics"], "contradicted")
        manifest = r.to_manifest()["per_vdd"]
        self.assertTrue(all(c["curve_px"] == [] for c in manifest["curves"]))
        self.assertEqual(manifest["plateau"]["status"], "ok")  # identity-free: still served

    def test_legacy_on_coincident_strokes_is_not_replaced(self) -> None:
        # 40 V and 48 V strokes coincide: the legacy trace lies on a real
        # stroke, so it stays (it is not a blend or a stroke switch)
        r = _result(NTMFS3D5, 5, 8)
        self.assertEqual(r.per_vdd["legacy_curve"]["rule"], "unchanged")
        self.assertIn("legacy_curve_on_coincident_source_curves", r.per_vdd["diagnostics"])

    def test_sij482_arrowhead_tip_not_shaft_end(self) -> None:
        r = _result(SIJ482, 3, 904)
        self.assertEqual(_bound(r), {30.0: 0, 40.0: 1, 50.0: 2})


class GraphWalkTests(unittest.TestCase):
    """The vector graph must be a DAG, or _paths never reaches a sink.

    2026-10-04: synth gc7 page_0006 merged two short pieces into the same pair
    of nodes in opposite raw order; the walk grew to ~20 GB per worker and 8
    workers panicked the host. Before the fix, the case below reached 3.9 GB
    in 8 s and was still growing.
    """

    STYLE = ("solid", (0, 0, 0), 1.0)

    def test_pieces_merging_against_their_raw_order_do_not_make_a_cycle(self) -> None:
        import pymupdf

        from datasheet_chart_digitizer import gate_charge_separation as S

        # A=(0,0) is created first; (0.6,0.35) lies outside A's 0.6 pt tolerance
        # and merges into B=(1,0), (0.6,0) merges into A: raw order runs B -> A.
        pieces = [((-5.0, 0.0), (0.0, 0.0), 1.0, 0, self.STYLE, True),
                  ((0.0, 0.0), (1.0, 0.0), 1.0, 0, self.STYLE, True),
                  ((0.6, 0.35), (0.6, 0.0), 1.0, 0, self.STYLE, True)]
        self.assertEqual(S._graph_curves(pieces, pymupdf.Rect(0, 0, 100, 100)), ([], None))

    def test_paths_refuses_a_cyclic_graph_instead_of_walking_forever(self) -> None:
        from datasheet_chart_digitizer import gate_charge_separation as S

        with self.assertRaises(ValueError):
            S._paths({0: {1}, 1: {2}, 2: {1}}, {1: 2, 2: 1}, 3)

    def test_paths_rebuilds_every_path_from_parent_pointers(self) -> None:
        from datasheet_chart_digitizer import gate_charge_separation as S

        edges = {0: {1, 2}, 1: {3}, 2: {3}, 3: {4, 5}}
        self.assertEqual(S._paths(edges, {1: 1, 2: 1, 3: 2, 4: 1, 5: 1}, 6),
                         [[0, 1, 3, 4], [0, 1, 3, 5], [0, 2, 3, 4], [0, 2, 3, 5]])


if __name__ == "__main__":
    unittest.main()
