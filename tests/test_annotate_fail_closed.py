from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock
import unittest

from datasheet_chart_digitizer import diode_forward_voltage as diode
from datasheet_chart_digitizer import gate_charge as gate
from datasheet_chart_digitizer import rdson_current
from datasheet_chart_digitizer.find_charts import ChartPanel


def _panel(diagram: int) -> ChartPanel:
    return ChartPanel(
        pdf="fixture.pdf",
        part="FIXTURE",
        page=2,
        diagram=diagram,
        title="Source-Drain Diode Forward",
        kind="body_diode",
        bbox_pt=(10.0, 20.0, 110.0, 120.0),
        crop_box_pt=(8.0, 18.0, 112.0, 122.0),
        crop_png=f"crops/p{diagram}.png",
        text="VSD Source-Drain Voltage IS Source Current",
        formula="",
        mentions=[],
        text_source="pdftotext",
    )


class AnnotateFailClosedTests(unittest.TestCase):
    def test_body_diode_panel_refusal_does_not_abort_peer(self) -> None:
        first, second = _panel(8), _panel(9)
        accepted = {
            "status": "ok",
            "panel": second.__dict__,
            "overlay": "overlays/accepted.png",
        }
        with TemporaryDirectory() as tmp, mock.patch.object(
            diode,
            "_digitize_panel",
            side_effect=[RuntimeError("X axis: no trustworthy numeric tick run"), accepted],
        ):
            out = Path(tmp)
            results, errors = diode.digitize_panels_fail_closed(
                [first, second, replace(second, kind="capacitances")], out
            )

            self.assertEqual(results, [accepted])
            self.assertEqual(
                errors,
                [
                    {
                        "kind": "body_diode",
                        "page": 2,
                        "diagram": 8,
                        "error": "X axis: no trustworthy numeric tick run",
                    }
                ],
            )
            self.assertTrue((out / "diode_forward_voltage.json").is_file())

    def test_gate_panel_refusal_does_not_abort_peer(self) -> None:
        first = replace(
            _panel(8),
            kind="gate_charge",
            title="Gate Charge Characteristics",
        )
        second = replace(first, diagram=9, crop_png="crops/p9.png")
        accepted = gate.GateChargeResult(
            pdf="fixture.pdf",
            panel=second,
            vpl=4.2,
            status="ok",
            score=1.0,
            trace_source="vector",
            dpi=220,
            crop_box_pt=second.crop_box_pt,
            plot_box_px=(10, 10, 100, 100),
            curve_px=((10, 90), (100, 20)),
            vpl_y_px=50.0,
            y_tick_count=3,
        )
        with TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "fixture.pdf"
            pdf.touch()
            with mock.patch.object(
                gate, "_discover_gate_panels", return_value=([first, second], {})
            ), mock.patch.object(
                gate, "_digitize_panel", side_effect=[RuntimeError("bad axis"), accepted]
            ), mock.patch.object(gate.pymupdf, "open", return_value=mock.MagicMock()):
                results, errors = gate.digitize_gate_charge_fail_closed(pdf)

        self.assertEqual(results, [accepted])
        self.assertEqual(
            errors,
            [
                {
                    "kind": "gate_charge",
                    "page": 2,
                    "diagram": 8,
                    "error": "bad axis",
                }
            ],
        )

    def test_rdson_current_panel_refusal_does_not_abort_peer(self) -> None:
        first = replace(
            _panel(8),
            kind="rds_on",
            title="On-resistance vs. Drain Current",
        )
        second = replace(first, diagram=9, crop_png="crops/p9.png")
        accepted = {"status": "ok", "panel": second.__dict__}
        with TemporaryDirectory() as tmp, mock.patch.object(
            rdson_current, "process_pdf", return_value=[first, second]
        ), mock.patch.object(
            rdson_current, "_is_rdson_current_panel", return_value=True
        ), mock.patch.object(
            rdson_current, "calibrate_panel", return_value=mock.sentinel.calibration
        ), mock.patch.object(
            rdson_current,
            "_digitize_rds_panel",
            side_effect=[RuntimeError("bad RDS grid"), accepted],
        ):
            results, errors = rdson_current.digitize_pdf_fail_closed(
                Path("fixture.pdf"), Path(tmp)
            )

        self.assertEqual(results, [accepted])
        self.assertEqual(
            errors,
            [
                {
                    "kind": "rds_on_current",
                    "page": 2,
                    "diagram": 8,
                    "error": "bad RDS grid",
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
