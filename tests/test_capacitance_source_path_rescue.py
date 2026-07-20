from pathlib import Path
from tempfile import TemporaryDirectory
import csv
import unittest

import pymupdf

from datasheet_chart_digitizer import find_charts
from datasheet_chart_digitizer import capacitance_vector as cv
from datasheet_chart_digitizer import mosfet_capacitance as mc


NVMFS5C460NL = Path(
    "/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/onsemi/NVMFS5C460NLWFT3G.pdf"
)
IPD50N10S3L = Path(
    "/Users/fab/dev/pv/pwr-mosfet-lib/datasheets/infineon/IPD50N10S3L-16.pdf"
)


class SourceDrawingPredicateTests(unittest.TestCase):
    @staticmethod
    def _candidate(index: int) -> tuple[float, list[tuple[int, int]]]:
        return (float(index), [(0, index), (100, index + 1)])

    def test_accepts_exactly_three_unambiguous_source_drawings(self) -> None:
        expected = [self._candidate(index) for index in range(3)]

        actual = cv._select_exact_source_drawing_rescue(
            [[candidate] for candidate in expected]
        )

        self.assertEqual(actual, expected)

    def test_ambiguous_drawing_contributes_none_and_total_refuses(self) -> None:
        first = self._candidate(1)
        second = self._candidate(2)
        ambiguous = [self._candidate(3), self._candidate(4)]

        actual = cv._select_exact_source_drawing_rescue(
            [[first], [second], ambiguous]
        )

        self.assertEqual(actual, [])

    def test_four_unambiguous_source_drawings_refuse(self) -> None:
        actual = cv._select_exact_source_drawing_rescue(
            [[self._candidate(index)] for index in range(4)]
        )

        self.assertEqual(actual, [])

    def test_short_span_color_path_requires_exactly_three_owned_sources(self) -> None:
        candidate = self._candidate(1)

        self.assertTrue(
            cv._exact_color_source_ownership([[candidate], [candidate], [candidate]])
        )
        self.assertFalse(
            cv._exact_color_source_ownership(
                [[candidate], [candidate, candidate], [candidate]]
            )
        )
        self.assertFalse(
            cv._exact_color_source_ownership([[candidate], [candidate]])
        )
        self.assertFalse(
            cv._exact_color_source_ownership(
                [[candidate], [candidate], [candidate], [candidate]]
            )
        )
        owned = [[candidate], [candidate], [candidate]]
        self.assertTrue(
            cv._short_color_source_span_is_proven(owned, [0.81, 0.81, 0.81])
        )
        self.assertFalse(
            cv._short_color_source_span_is_proven(owned, [0.79, 0.81, 0.81])
        )
        self.assertFalse(
            cv._short_color_source_span_is_proven(
                [[candidate], [candidate]], [0.81, 0.81]
            )
        )

    def test_thin_strokes_require_explicit_opt_in(self) -> None:
        drawing = {
            "type": "s",
            "color": (0.0, 0.0, 0.0),
            "width": 0.51,
            "items": [
                (
                    "l",
                    pymupdf.Point(10.0, 80.0),
                    pymupdf.Point(90.0, 20.0),
                )
            ],
        }
        plot = pymupdf.Rect(0.0, 0.0, 100.0, 100.0)

        self.assertEqual(cv._vector_curve_edges([drawing], plot), [])
        self.assertEqual(
            len(
                cv._vector_curve_edges(
                    [drawing], plot, min_stroke_width=0.4
                )
            ),
            1,
        )


@unittest.skipUnless(NVMFS5C460NL.exists(), "local NVMFS5C460NL datasheet unavailable")
class OnsemiSharedEndpointVectorEndToEnd(unittest.TestCase):
    def test_rescued_source_paths_withhold_unproven_physical_scale(self) -> None:
        with TemporaryDirectory(prefix="nvmfs5c460-vector-") as tmp:
            out = Path(tmp)
            panels = find_charts.process_pdf(NVMFS5C460NL, out, dpi=220)
            panel = next(
                panel
                for panel in panels
                if panel.kind == "capacitances" and panel.title == "Capacitance Variation"
            )
            chart = {
                "pdf": str(NVMFS5C460NL),
                "part": "NVMFS5C460NLWFT3G",
                "page": panel.page,
                "diagram": 7,
                "bbox_pt": list(panel.bbox_pt),
                "crop_box_pt": list(panel.crop_box_pt),
                "text": panel.text,
            }
            result = mc.process_chart(
                chart,
                out / panel.crop_png,
                out,
                Path("nvmfs5c460"),
                NVMFS5C460NL.parent,
            )

        self.assertEqual(result["extraction_method"], "vector")
        self.assertEqual(result["vector_selection_method"], "source_drawing_rescue")
        self.assertIsNone(result["vector_error"])
        self.assertEqual(result["status"], "overlay-review-required")
        self.assertFalse(result["physical_output_available"])
        self.assertIn(
            "source_drawing_rescue_axis_center_review_required",
            result["status_reasons"],
        )
        self.assertEqual([trace["points"] for trace in result["traces"]], [627] * 3)
        self.assertEqual(result["diagnostics"]["checks"]["ciss_coss_rank_swap_count"], 0)


@unittest.skipUnless(IPD50N10S3L.exists(), "local IPD50N10S3L datasheet unavailable")
class InfineonFilledCurveEndToEnd(unittest.TestCase):
    def test_three_filled_source_paths_recover_inline_label_occlusion(self) -> None:
        with TemporaryDirectory(prefix="ipd50-filled-cap-") as tmp:
            out = Path(tmp)
            panels = find_charts.process_pdf(IPD50N10S3L, out, dpi=220)
            panel = next(
                panel
                for panel in panels
                if panel.kind == "capacitances" and panel.page == 6
            )
            chart = {
                "pdf": str(IPD50N10S3L),
                "part": "IPD50N10S3L-16",
                "page": panel.page,
                "diagram": 10,
                "bbox_pt": list(panel.bbox_pt),
                "crop_box_pt": list(panel.crop_box_pt),
                "text": panel.text,
            }
            result = mc.process_chart(
                chart,
                out / panel.crop_png,
                out,
                Path("ipd50"),
                IPD50N10S3L.parent,
            )

            with (out / result["points"]).open(newline="") as handle:
                point_rows = list(csv.DictReader(handle))

        self.assertEqual("vector", result["extraction_method"])
        self.assertEqual("exact_filled_source_paths", result["vector_selection_method"])
        self.assertEqual("ok", result["status"])
        self.assertTrue(result["physical_output_available"])
        self.assertEqual([74, 37, 656, 755], result["plot_box_px"])
        self.assertEqual([583, 583, 583], [trace["points"] for trace in result["traces"]])
        for name in ("Ciss", "Coss", "Crss"):
            max_vds = max(
                float(row["vds_V"])
                for row in point_rows
                if row["trace"] == name
            )
            self.assertAlmostEqual(98.272809, max_vds, places=6)


if __name__ == "__main__":
    unittest.main()
