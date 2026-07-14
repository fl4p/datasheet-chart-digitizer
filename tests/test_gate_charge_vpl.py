import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from datasheet_chart_digitizer import cli
from datasheet_chart_digitizer import gate_charge as gate
from datasheet_chart_digitizer import gate_charge_estimation as estimation
from datasheet_chart_digitizer import gate_charge_vpl as vpl
from datasheet_chart_digitizer import gate_charge_trace as trace
from datasheet_chart_digitizer import find_charts


def _sample_panel() -> find_charts.ChartPanel:
    return find_charts.ChartPanel(
        pdf="sample.pdf",
        part="sample",
        page=3,
        diagram=7,
        title="Gate charge",
        kind="gate_charge",
        bbox_pt=(10.0, 20.0, 110.0, 120.0),
        crop_box_pt=(8.0, 18.0, 112.0, 122.0),
        crop_png="crops/sample.png",
        text="Gate charge",
        formula="",
        mentions=[],
    )


class GateChargeVplTests(unittest.TestCase):
    def test_samples_from_chart_extraction_strips_datasheet_prefix(self) -> None:
        text = '''
        "datasheets/infineon/Foo.pdf": {"ref": 3.25, "comment": "first"},
        "datasheets/ao/Bar.pdf": {"comment": "no ref"}
        '''
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "chart-extraction.md"
            path.write_text(text)

            samples = vpl._samples_from_chart_extraction(path, start=1, count=2)

        self.assertEqual(samples[0], ("infineon/Foo.pdf", 3.25, "first"))
        self.assertEqual(samples[1], ("ao/Bar.pdf", None, "no ref"))

    def test_sample_pdf_path_resolves_relative_to_datasheets(self) -> None:
        root = Path("/tmp/pwr-mosfet-lib")

        self.assertEqual(
            vpl._sample_pdf_path(root, "infineon/Foo.pdf"),
            root / "datasheets" / "infineon/Foo.pdf",
        )
        self.assertEqual(
            vpl._sample_pdf_path(root, "/tmp/Foo.pdf"),
            Path("/tmp/Foo.pdf"),
        )

    def test_context_filter_accepts_gate_charge_and_rejects_diode(self) -> None:
        self.assertFalse(estimation._reject_non_gate_context("Figure 8 Gate Charge Characteristics"))
        self.assertTrue(estimation._reject_non_gate_context("Figure 6 Source-Drain Diode Forward"))

    def test_numeric_tick_parser_rejects_axis_words(self) -> None:
        self.assertIsNone(estimation._parse_numeric_label("Gate-to-Source"))
        self.assertIsNone(estimation._parse_numeric_label("Voltage"))
        self.assertEqual(estimation._parse_numeric_label("1O"), 10.0)

    def test_y_tick_column_uses_longest_linear_run(self) -> None:
        candidates = [
            (12.0, 10.0),
            (10.0, 20.0),
            (8.0, 30.0),
            (6.0, 40.0),
            (4.0, 50.0),
            (2.0, 60.0),
            (0.0, 70.0),
            (8.0, 150.0),
        ]

        ticks = estimation._normalize_y_tick_candidates(candidates)

        self.assertEqual([value for value, _y in ticks], [12.0, 10.0, 8.0, 6.0, 4.0, 2.0, 0.0])

    def test_result_manifest_retains_status_and_panel_provenance(self) -> None:
        panel = _sample_panel()
        result = gate.GateChargeResult(
            pdf="sample.pdf",
            panel=panel,
            vpl=4.2,
            status="axis_assumed",
            score=3.5,
            trace_source="raster",
            dpi=220,
            crop_box_pt=(8.0, 18.0, 112.0, 122.0),
            plot_box_px=(10, 20, 100, 120),
            curve_px=((10, 100), (90, 30)),
            vpl_y_px=62.0,
            y_tick_count=0,
            diagnostics=("axis_assumed_0_10",),
        )

        manifest = result.to_manifest()

        self.assertEqual(manifest["status"], "axis_assumed")
        self.assertEqual(manifest["diagnostics"], ("axis_assumed_0_10",))
        self.assertEqual(manifest["panel"]["page"], 3)
        self.assertEqual(manifest["panel"]["kind"], "gate_charge")

        detached = gate._detach_transient_panel_artifacts(panel)
        self.assertEqual(detached.crop_png, "")
        self.assertEqual(detached.crop_box_pt, panel.crop_box_pt)

        higher_score = replace(result, score=9.0)
        unresolved = replace(result, vpl=None, status="unresolved", score=99.0)
        ordered = sorted([unresolved, result, higher_score], key=gate._result_sort_key)
        self.assertEqual(ordered, [higher_score, result, unresolved])

    def test_digitize_detaches_temporary_finder_crop_path(self) -> None:
        with TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "sample.pdf"
            pdf.touch()
            document = mock.MagicMock()
            with mock.patch.object(gate, "process_pdf", return_value=[_sample_panel()]), mock.patch.object(
                gate.pymupdf, "open", return_value=document
            ), mock.patch.object(gate, "_digitize_panel", return_value=None) as digitize_panel:
                results = gate.digitize_gate_charge(pdf)

        self.assertEqual(results, [])
        passed_panel = digitize_panel.call_args.args[2]
        self.assertEqual(passed_panel.crop_png, "")
        self.assertEqual(passed_panel.crop_box_pt, _sample_panel().crop_box_pt)

    def test_trace_component_tracks_monotone_gate_curve(self) -> None:
        import numpy as np

        mask = np.zeros((80, 100), dtype=np.uint8)
        for x in range(5, 95):
            y = int(round(70 - 0.55 * x))
            mask[max(0, y - 1) : min(mask.shape[0], y + 2), x] = 255

        points = trace._trace_component(mask)

        self.assertGreater(len(points), 70)
        self.assertLess(points[-1][1], points[0][1])

    def test_main_has_no_dslib_runtime_requirement(self) -> None:
        with TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "sample.pdf"
            pdf.touch()
            argv = ["dsdig digitize-vpl", str(pdf), "--out", str(Path(tmp) / "out")]
            with mock.patch("sys.argv", argv), mock.patch.object(
                vpl, "digitize_gate_charge", return_value=[]
            ):
                result = vpl.main()

        self.assertEqual(result, 1)

    def test_cli_propagates_vpl_failure_exit_code(self) -> None:
        argv = ["dsdig", "digitize-vpl", "missing.pdf"]
        with mock.patch("sys.argv", argv), mock.patch.object(vpl, "main", return_value=7):
            with self.assertRaises(SystemExit) as raised:
                cli.main()

        self.assertEqual(raised.exception.code, 7)


if __name__ == "__main__":
    unittest.main()
