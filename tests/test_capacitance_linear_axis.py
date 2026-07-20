"""Focused guards for arithmetic capacitance Y-axis calibration."""

from __future__ import annotations

import csv
import math
import unittest
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import numpy as np

from datasheet_chart_digitizer import capacitance_axis, find_charts
from datasheet_chart_digitizer.capacitance_overlay import _axis_debug_y_label
from datasheet_chart_digitizer import mosfet_capacitance as mc


CSD16342Q5A = Path(
    "/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/ti/CSD16342Q5A.pdf"
)
CSD86330Q3D = Path(
    "/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/ti/CSD86330Q3D.pdf"
)
TWO_N7002L = Path(
    "/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/ti/2N7002L.pdf"
)


class LinearCapacitanceAxisTests(unittest.TestCase):
    def test_linear_y_mapping_round_trips_pf(self) -> None:
        plot = mc.PlotBox(50, 20, 250, 220)
        calibration = mc.AxisCalibration(
            x_min_v=0.0,
            x_max_v=25.0,
            y_min_decade=math.log10(500.0),
            y_max_decade=math.log10(2500.0),
            source="position_text",
            x_ticks_v=(0.0, 5.0, 10.0, 15.0, 20.0, 25.0),
            y_decades=tuple(
                math.log10(value) for value in (500.0, 1000.0, 1500.0, 2000.0)
            ),
            y_log=False,
            y_ticks_pf=(500.0, 1000.0, 1500.0, 2000.0),
            y_resid_pf=0.0,
            x_resid_v=0.0,
            x_scale=0.125,
            x_offset=-6.25,
            y_scale=-10.0,
            y_offset=2500.0,
        )

        log_cap = mc.calibration_log_c_of_y(calibration, plot, 100.0)
        self.assertAlmostEqual(10.0**log_cap, 1500.0)
        self.assertAlmostEqual(
            mc.calibration_y_of_log_c(calibration, plot, math.log10(1500.0)),
            100.0,
        )
        self.assertIsNone(mc.reject_bad_position_calibration(calibration, plot))

    def test_default_log_y_mapping_is_unchanged(self) -> None:
        plot = mc.PlotBox(10, 20, 110, 220)
        calibration = mc.AxisCalibration(
            x_min_v=0.0,
            x_max_v=25.0,
            y_min_decade=1.0,
            y_max_decade=4.0,
            source="position_text",
            x_ticks_v=(0.0, 25.0),
            y_decades=(1.0, 2.0, 3.0, 4.0),
            y_scale=-0.015,
            y_offset=4.3,
        )

        expected = calibration.y_scale * 120.0 + calibration.y_offset
        self.assertTrue(calibration.y_log)
        self.assertAlmostEqual(
            mc.calibration_log_c_of_y(calibration, plot, 120.0), expected
        )
        self.assertAlmostEqual(
            mc.calibration_y_of_log_c(calibration, plot, expected), 120.0
        )
        self.assertEqual(_axis_debug_y_label(calibration, 2.0), "10^2")
        self.assertEqual(
            _axis_debug_y_label(calibration, math.log10(2.0)), "2pF"
        )

    def test_linear_grid_seating_refuses_label_without_nearby_gridline(self) -> None:
        plot = mc.PlotBox(10, 20, 110, 220)
        calibration = mc.AxisCalibration(
            x_min_v=0.0,
            x_max_v=25.0,
            y_min_decade=math.log10(500.0),
            y_max_decade=math.log10(2000.0),
            source="position_text",
            x_ticks_v=(0.0, 25.0),
            y_decades=tuple(math.log10(value) for value in (500.0, 1000.0, 1500.0, 2000.0)),
            y_log=False,
            y_ticks_pf=(500.0, 1000.0, 1500.0, 2000.0),
            y_tick_label_px=(180.0, 140.0, 100.0, 60.0),
            y_scale=-12.5,
            y_offset=2750.0,
        )
        image = np.full((240, 140), 255, dtype=np.uint8)
        with mock.patch.object(
            capacitance_axis,
            "_horizontal_gridline_candidates",
            return_value=[50.0, 90.0, 130.0, 170.0],
        ):
            with self.assertRaisesRegex(RuntimeError, "does not own exactly one"):
                capacitance_axis._seat_linear_y_ticks_on_grid(
                    calibration, image, plot
                )

    @unittest.skipUnless(CSD16342Q5A.exists(), "local TI pilot PDF unavailable")
    def test_csd16342q5a_linear_nf_axis_recovers_physical_curves(self) -> None:
        with TemporaryDirectory(prefix="csd16342-linear-cap-") as tmp:
            out = Path(tmp)
            panels = find_charts.process_pdf(CSD16342Q5A, out, dpi=220)
            caps = [panel for panel in panels if panel.kind == "capacitances"]
            self.assertEqual([(panel.page, panel.diagram) for panel in caps], [(4, 55)])
            panel = caps[0]
            result = mc.process_chart(
                asdict(panel),
                out / panel.crop_png,
                out,
                Path("CSD16342Q5A/p04_diagram_55"),
                CSD16342Q5A.parent,
            )

            axis = result["axis_calibration"]
            self.assertEqual(axis["source"], "position_text")
            self.assertFalse(axis["y_log"])
            self.assertEqual(axis["y_ticks_pf"], [500.0, 1000.0, 1500.0, 2000.0])
            self.assertEqual(axis["y_source"], "position_text_grid_seated")
            self.assertLess(axis["y_resid_pf"], 0.01)
            expected_grid_px = [325.0362, 245.6153, 166.1946, 86.7743]
            for value, observed, expected in zip(
                axis["y_ticks_pf"], axis["y_gridline_px"], expected_grid_px
            ):
                with self.subTest(value_pf=value):
                    self.assertAlmostEqual(observed, expected, delta=0.1)
                    served_pixel = (value - axis["y_offset"]) / axis["y_scale"]
                    self.assertAlmostEqual(served_pixel, expected, delta=1.0)
            self.assertLessEqual(axis["y_grid_residual_px"], 1.0)
            self.assertLessEqual(axis["y_label_to_grid_max_px"], 3.0)
            self.assertEqual(result["status"], "ok")
            self.assertTrue(result["physical_output_available"])
            self.assertEqual(result["trace_validation_status"], "pass")

            with (out / result["points"]).open() as handle:
                rows = list(csv.DictReader(handle))
        by_name: dict[str, list[dict[str, str]]] = {}
        for row in rows:
            by_name.setdefault(row["trace"], []).append(row)
        at_25v = {
            name: min(values, key=lambda row: abs(float(row["vds_V"]) - 25.0))
            for name, values in by_name.items()
        }
        self.assertTrue(1050.0 < float(at_25v["Ciss"]["cap_pF"]) < 1120.0)
        self.assertTrue(500.0 < float(at_25v["Coss"]["cap_pF"]) < 580.0)
        self.assertTrue(30.0 < float(at_25v["Crss"]["cap_pF"]) < 40.0)

    @unittest.skipUnless(TWO_N7002L.exists(), "local TI 2N7002L PDF unavailable")
    def test_2n7002l_dense_log_pf_axis_recovers_physical_curves(self) -> None:
        with TemporaryDirectory(prefix="2n7002l-dense-log-cap-") as tmp:
            out = Path(tmp)
            panels = find_charts.process_pdf(TWO_N7002L, out, dpi=220)
            panel = next(
                panel for panel in panels
                if (panel.page, panel.diagram, panel.kind) == (7, 58, "capacitances")
            )
            result = mc.process_chart(
                asdict(panel),
                out / panel.crop_png,
                out,
                Path("2N7002L/p07_diagram_58"),
                TWO_N7002L.parent,
            )

            axis = result["axis_calibration"]
            self.assertTrue(axis["x_log"])
            self.assertTrue(axis["y_log"])
            self.assertEqual(
                [round(10.0 ** exponent) for exponent in axis["y_decades"]],
                list(range(2, 11)),
            )
            self.assertLess(axis["x_resid_v"], 0.001)
            self.assertLess(axis["y_resid_dec"], 0.001)
            self.assertEqual(result["status"], "ok")
            self.assertTrue(result["physical_output_available"])
            self.assertEqual(result["trace_validation_status"], "pass")
            self.assertEqual(result["extraction_method"], "vector")
            self.assertEqual(
                result["vector_selection_method"],
                "exact_color_components_short_source_span",
            )

            with (out / result["points"]).open() as handle:
                rows = list(csv.DictReader(handle))

        physical = [row for row in rows if row["vds_V"] and row["cap_pF"]]
        self.assertEqual(len(physical), 1488)
        by_name = {
            name: [row for row in physical if row["trace"] == name]
            for name in ("Ciss", "Coss", "Crss")
        }
        self.assertEqual(
            {name: len(values) for name, values in by_name.items()},
            {"Ciss": 496, "Coss": 496, "Crss": 496},
        )
        ranges = {
            name: (
                min(float(row["cap_pF"]) for row in values),
                max(float(row["cap_pF"]) for row in values),
            )
            for name, values in by_name.items()
        }
        self.assertTrue(5.2 < ranges["Ciss"][0] < ranges["Ciss"][1] < 6.8)
        self.assertTrue(5.6 < ranges["Coss"][0] < ranges["Coss"][1] < 8.2)
        self.assertTrue(2.0 < ranges["Crss"][0] < ranges["Crss"][1] < 2.7)

    @unittest.skipUnless(CSD86330Q3D.exists(), "local TI power-block PDF unavailable")
    def test_csd86330q3d_subunit_nf_decades_recover_both_physical_panels(self) -> None:
        with TemporaryDirectory(prefix="csd86330-subunit-cap-") as tmp:
            out = Path(tmp)
            panels = find_charts.process_pdf(CSD86330Q3D, out, dpi=220)
            caps = [panel for panel in panels if panel.kind == "capacitances"]
            self.assertEqual(
                [(panel.page, panel.diagram) for panel in caps], [(8, 16), (8, 17)]
            )
            results = [
                mc.process_chart(
                    asdict(panel),
                    out / panel.crop_png,
                    out,
                    Path(f"CSD86330Q3D/p08_diagram_{panel.diagram}"),
                    CSD86330Q3D.parent,
                )
                for panel in caps
            ]

            for result, expected_decades in zip(results, ([0, 1, 2, 3, 4], [1, 2, 3, 4])):
                axis = result["axis_calibration"]
                self.assertEqual(axis["source"], "position_text")
                self.assertTrue(axis["y_log"])
                self.assertEqual(axis["y_decades"], expected_decades)
                self.assertLess(axis["x_resid_v"], 0.01)
                self.assertLess(axis["y_resid_dec"], 0.01)
                self.assertEqual(result["status"], "ok")
                self.assertTrue(result["physical_output_available"])
                self.assertEqual(result["trace_validation_status"], "pass")
                self.assertEqual([trace["points"] for trace in result["traces"]], [571] * 3)

            with (out / results[0]["points"]).open() as handle:
                control_rows = list(csv.DictReader(handle))
            with (out / results[1]["points"]).open() as handle:
                sync_rows = list(csv.DictReader(handle))

        def at_25v(rows, name):
            owned = [row for row in rows if row["trace"] == name]
            return float(min(owned, key=lambda row: abs(float(row["vds_V"]) - 25.0))["cap_pF"])

        self.assertTrue(700.0 < at_25v(control_rows, "Ciss") < 750.0)
        self.assertTrue(270.0 < at_25v(control_rows, "Coss") < 300.0)
        self.assertTrue(8.0 < at_25v(control_rows, "Crss") < 12.0)
        self.assertTrue(1250.0 < at_25v(sync_rows, "Ciss") < 1300.0)
        self.assertTrue(530.0 < at_25v(sync_rows, "Coss") < 570.0)
        self.assertTrue(18.0 < at_25v(sync_rows, "Crss") < 22.0)


if __name__ == "__main__":
    unittest.main()
