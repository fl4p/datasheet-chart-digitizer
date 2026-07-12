import csv
import json
import math
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from datasheet_chart_digitizer.coss_export import (
    build_adaptive_coss_model,
    build_qoss_table,
    discover_export_jobs,
    evaluate_coss_model,
    load_coss_points_csv,
    spice_qoss_table,
)


class CossExportTests(unittest.TestCase):
    def test_adaptive_log_space_knots_bound_relative_error(self) -> None:
        vds = np.linspace(0.0, 80.0, 400)
        coss = 800.0 / np.sqrt(1.0 + vds)

        model = build_adaptive_coss_model(vds, coss, max_rel_error=0.02)
        fitted = evaluate_coss_model(model, vds)

        self.assertLessEqual(np.max(np.abs(fitted / coss - 1.0)), 0.0200001)
        self.assertLess(len(model.vds), 20)

    def test_qoss_table_is_monotone_and_matches_integral(self) -> None:
        vds = np.linspace(0.0, 80.0, 500)
        coss = 800.0 / np.sqrt(1.0 + vds)
        model = build_adaptive_coss_model(vds, coss, max_rel_error=0.005)

        table = build_qoss_table(model, v_max=40.0, samples=512)

        self.assertTrue(np.all(np.diff(np.asarray(table.qoss_c)) >= 0.0))
        analytic_q_pc = 1600.0 * (math.sqrt(41.0) - 1.0)
        self.assertLess(abs(table.qoss_pc - analytic_q_pc) / analytic_q_pc, 0.01)
        self.assertGreater(table.co_tr_pf, 0.0)
        self.assertGreater(table.co_er_pf, 0.0)

    def test_spice_export_uses_charge_table_current_source(self) -> None:
        vds = np.linspace(0.0, 10.0, 50)
        coss = 1000.0 / np.sqrt(1.0 + vds)
        model = build_adaptive_coss_model(vds, coss, max_rel_error=0.02)
        table = build_qoss_table(model, v_max=10.0, samples=8)

        text = spice_qoss_table("PART-1", table, drain="D", source="S")

        self.assertIn(".func qoss_PART_1(v) table(max(v,0),", text)
        self.assertIn("B_COSS_PART_1 D S I = ddt(qoss_PART_1(V(D,S)))", text)
        self.assertIn("QSPICE-oriented", text)
        self.assertIn("LTspice can over-count switching loss", text)
        self.assertIn(", ", text)

    def test_cli_exports_json_csv_and_spice(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            points = root / "points.csv"
            _write_points(points)
            out = root / "out"

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "datasheet_chart_digitizer.coss_export",
                    str(points),
                    "--out",
                    str(out),
                    "--name",
                    "TST",
                    "--max-rel-error",
                    "0.02",
                    "--table-points",
                    "32",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue((out / "TST.coss_model.json").exists())
            self.assertTrue((out / "TST.qoss_table.csv").exists())
            self.assertTrue((out / "TST.qoss_table.cir").exists())
            payload = json.loads((out / "TST.coss_model.json").read_text())
            self.assertLessEqual(payload["model"]["achieved_max_rel_error"], 0.020001)
            loaded_v, loaded_c = load_coss_points_csv(points)
            self.assertEqual(len(loaded_v), len(loaded_c))
            self.assertGreater(len(loaded_v), 10)

    def test_manifest_discovery_uses_relative_points_paths(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            points = root / "points" / "crops" / "PART1" / "p01_diagram_02.points.csv"
            _write_points(points)
            manifest = root / "capacitance_digitization.json"
            manifest.write_text(
                json.dumps(
                    [
                        {
                            "part": "PART1",
                            "diagram": "diagram_02",
                            "points": "points/crops/PART1/p01_diagram_02.points.csv",
                        }
                    ]
                )
                + "\n"
            )

            jobs = discover_export_jobs(manifest)

            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0].points_csv, points)
            self.assertEqual(jobs[0].name, "PART1_diagram_02")

    def test_cli_batch_exports_manifest_inputs(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = []
            for part, diagram in (("PART1", "diagram_01"), ("PART2", "diagram_02")):
                points = root / "points" / "crops" / part / f"p01_{diagram}.points.csv"
                _write_points(points)
                rows.append({"part": part, "diagram": diagram, "points": str(points.relative_to(root))})
            manifest = root / "capacitance_digitization.json"
            manifest.write_text(json.dumps(rows) + "\n")
            out = root / "batch"

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "datasheet_chart_digitizer.coss_export",
                    str(manifest),
                    "--out",
                    str(out),
                    "--max-rel-error",
                    "0.02",
                    "--table-points",
                    "16",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            export_manifest = out / "coss_export_manifest.json"
            self.assertTrue(export_manifest.exists())
            payload = json.loads(export_manifest.read_text())
            self.assertEqual([row["name"] for row in payload], ["PART1_diagram_01", "PART2_diagram_02"])
            for row in payload:
                self.assertTrue(Path(row["model_json"]).exists())
                self.assertTrue(Path(row["qoss_csv"]).exists())
                self.assertTrue(Path(row["spice_cir"]).exists())

def _write_points(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["trace", "x_px", "y_px", "x_norm", "y_norm_log_axis", "vds_V", "cap_pF"])
        for idx, v in enumerate(np.linspace(0.0, 20.0, 80)):
            writer.writerow(["Coss", idx, idx, "", "", v, 500.0 / math.sqrt(1.0 + v)])


if __name__ == "__main__":
    unittest.main()
