from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import pymupdf
from PIL import Image, ImageDraw

from datasheet_chart_digitizer.annotate_pdf import (
    _draw_gate_tick_evidence,
    _draw_vpl_annotation,
    annotate_pdf,
)


class AnnotatePdfTests(unittest.TestCase):
    def test_gate_overlay_draws_consumed_tick_values_for_scale_review(self) -> None:
        result = SimpleNamespace(
            plot_box_px=(10, 10, 110, 70),
            x_ticks_px=((0.0, 10.0), (1.0, 110.0)),
            y_ticks_px=((5.0, 10.0), (0.0, 70.0)),
            x_tick_unit="nC",
            y_tick_unit="V",
        )

        overlay = _draw_gate_tick_evidence(
            Image.new("RGB", (120, 80), "white"), result
        )

        self.assertEqual(overlay.getpixel((110, 70)), (196, 0, 196))
        colors = dict((color, count) for count, color in overlay.getcolors(120 * 80))
        self.assertGreater(
            colors.get((196, 0, 196), 0), 20
        )

    def test_vpl_guide_is_drawn_only_for_served_gate_results(self) -> None:
        served = SimpleNamespace(
            status="ok",
            vpl=1.54,
            vpl_y_px=40.0,
            plot_box_px=(10, 10, 110, 70),
        )
        refused = SimpleNamespace(
            status="axis_assumed",
            vpl=1.54,
            vpl_y_px=40.0,
            plot_box_px=(10, 10, 110, 70),
        )
        served_image = Image.new("RGB", (120, 80), "white")
        refused_image = Image.new("RGB", (120, 80), "white")

        _draw_vpl_annotation(ImageDraw.Draw(served_image), served)
        _draw_vpl_annotation(ImageDraw.Draw(refused_image), refused)

        self.assertEqual(served_image.getpixel((20, 40)), (196, 0, 196))
        self.assertEqual(refused_image.getpixel((20, 40)), (255, 255, 255))

    def test_csd13385_embeds_exactly_the_five_supported_charts(self) -> None:
        pdf = Path(
            "/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/ti/CSD13385F5.pdf"
        )
        if not pdf.exists():
            self.skipTest(f"missing local corpus fixture: {pdf}")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = annotate_pdf(
                pdf,
                root / "annotated.pdf",
                work_dir=root / "artifacts",
                dpi=220,
                include_review_required=True,
            )

        self.assertEqual(manifest["detected_panels"], 5)
        self.assertEqual(manifest["errors"], [])
        self.assertRegex(manifest["source_pdf_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(manifest["extractor_git_commit"], r"^[0-9a-f]{40}$")
        self.assertRegex(manifest["extractor_source_sha256"], r"^[0-9a-f]{64}$")
        self.assertTrue(manifest["extractor_git_dirty"])
        self.assertGreater(len(manifest["extractor_source_files"]), 10)
        self.assertEqual(
            [
                (row["page"], row["diagram"], row["kind"])
                for row in manifest["overlays"]
            ],
            [
                (4, 52, "transfer"),
                (4, 54, "gate_charge"),
                (4, 55, "capacitances"),
                (5, 58, "rds_on_temperature"),
                (5, 59, "body_diode"),
            ],
        )
        self.assertTrue(all(row["embedded"] for row in manifest["overlays"]))

    def test_csd13201_embeds_both_detected_gate_charge_panels(self) -> None:
        pdf = Path(
            "/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/ti/CSD13201W10.pdf"
        )
        if not pdf.exists():
            self.skipTest(f"missing local corpus fixture: {pdf}")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = annotate_pdf(
                pdf,
                root / "annotated.pdf",
                work_dir=root / "artifacts",
                dpi=220,
                include_review_required=True,
            )

        gate_panels = [
            (row["page"], row["diagram"])
            for row in manifest["overlays"]
            if row["kind"] == "gate_charge"
        ]
        self.assertEqual(gate_panels, [(1, 952), (5, 4)])
        gate_rows = [row for row in manifest["overlays"] if row["kind"] == "gate_charge"]
        self.assertTrue(all(row["vpl_v"] is not None for row in gate_rows))
        self.assertTrue(all(row["vpl_y_px"] is not None for row in gate_rows))
        self.assertEqual(manifest["detected_panels"], 6)
        self.assertEqual(len(manifest["overlays"]), 6)
        self.assertEqual(manifest["errors"], [])
        self.assertTrue(all(row["embedded"] for row in manifest["overlays"]))

    def test_spd03_writes_all_seven_detected_overlays(self) -> None:
        pdf = Path(
            "/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/hxy/"
            "SPD03N50C3ATMA1-HXY.pdf"
        )
        if not pdf.exists():
            self.skipTest(f"missing local corpus fixture: {pdf}")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "annotated.pdf"
            manifest = annotate_pdf(pdf, output, work_dir=root / "artifacts", dpi=220)
            self.assertEqual(manifest["detected_panels"], 7)
            self.assertEqual(manifest["errors"], [])
            self.assertEqual(
                [(row["page"], row["diagram"], row["kind"]) for row in manifest["overlays"]],
                [
                    (3, 2, "transfer"),
                    (3, 3, "rds_on_current"),
                    (3, 4, "body_diode"),
                    (3, 5, "gate_charge"),
                    (3, 6, "capacitances"),
                    (4, 7, "breakdown_voltage"),
                    (4, 8, "rds_on_temperature"),
                ],
            )
            self.assertTrue(output.exists())
            by_kind = {row["kind"]: row for row in manifest["overlays"]}
            self.assertFalse(by_kind["transfer"]["embedded"])
            self.assertEqual(
                by_kind["transfer"]["embedding_reason"],
                "status_not_embeddable:overlay-review-required",
            )
            self.assertTrue(
                all(
                    row["embedded"]
                    for kind, row in by_kind.items()
                    if kind != "transfer"
                )
            )
            diode = by_kind["body_diode"]
            crop = diode["crop_box_pt"]
            target_width = round((crop[2] - crop[0]) * 220 / 72)
            target_height = round((crop[3] - crop[1]) * 220 / 72)
            self.assertLessEqual(
                abs(diode["overlay_size_px"][0] - target_width), 2
            )
            self.assertEqual(
                diode["embedded_overlay_size_px"],
                [diode["overlay_size_px"][0], target_height],
            )
            self.assertEqual(diode["placed_rect_pt"], diode["crop_box_pt"])
            with pymupdf.open(pdf) as source, pymupdf.open(output) as document:
                self.assertEqual(document.page_count, source.page_count)

            repeat = root / "annotated-repeat.pdf"
            repeat_manifest = annotate_pdf(
                pdf, repeat, work_dir=root / "repeat-artifacts", dpi=220
            )
            self.assertEqual(
                hashlib.sha256(output.read_bytes()).hexdigest(),
                hashlib.sha256(repeat.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                [row["embedded"] for row in manifest["overlays"]],
                [row["embedded"] for row in repeat_manifest["overlays"]],
            )

    def test_st_rds_refusal_preserves_peer_overlays_and_manifest(self) -> None:
        pdf = Path(
            "/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/st/STL260N4F7.pdf"
        )
        if not pdf.exists():
            self.skipTest(f"missing local corpus fixture: {pdf}")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "annotated.pdf"
            manifest = annotate_pdf(
                pdf,
                output,
                work_dir=root / "artifacts",
                dpi=220,
                include_review_required=True,
            )

            self.assertTrue(output.is_file())
            self.assertTrue((root / "artifacts" / "annotated_pdf_manifest.json").is_file())
            self.assertEqual(manifest["detected_panels"], 7)
            gate = [
                row for row in manifest["overlays"] if row["kind"] == "gate_charge"
            ]
            self.assertEqual(len(gate), 1)
            self.assertEqual(gate[0]["status"], "ok")
            self.assertTrue(gate[0]["embedded"])
            self.assertIn(
                {
                    "kind": "rds_on_temperature",
                    "page": 6,
                    "diagram": 8,
                    "error": "panel: no direction-evidenced local RDS grid",
                },
                manifest["errors"],
            )


if __name__ == "__main__":
    unittest.main()
