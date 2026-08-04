from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pymupdf

from datasheet_chart_digitizer.capacitance_types import PlotBox
from datasheet_chart_digitizer.diode_legend_color import colored_temperature_bindings
from datasheet_chart_digitizer.find_charts import ChartPanel
from datasheet_chart_digitizer.source_color_binding import bind_two_source_color_legend


class _IdentityTransform:
    @staticmethod
    def to_px(x: float, y: float) -> tuple[float, float]:
        return x, y

    @staticmethod
    def to_pt(x: float, y: float) -> tuple[float, float]:
        return x, y


class _Page:
    def __init__(self, drawings, words=()):
        self._drawings = drawings
        self._words = list(words)

    def get_drawings(self):
        return self._drawings

    def get_text(self, kind: str):
        if kind != "words":
            raise AssertionError(kind)
        return self._words


class _Document:
    def __init__(self, page):
        self._page = page

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def __getitem__(self, _index):
        return self._page


def _line(color, x0, y0, x1, y1, *, candidate=None):
    return {
        "color": color,
        "items": [("l", pymupdf.Point(x0, y0), pymupdf.Point(x1, y1))],
        "candidate": candidate,
    }


class SourceColorBindingTests(unittest.TestCase):
    def setUp(self):
        self.plot = PlotBox(0, 0, 100, 100)
        self.curves = [
            [(x, 20) for x in range(101)],
            [(x, 70) for x in range(101)],
        ]
        self.labels = [(25.0, (60, 10, 90, 30)), (125.0, (60, 60, 90, 80))]
        self.red = (1.0, 0.0, 0.0)
        self.blue = (0.0, 0.0, 1.0)

    @staticmethod
    def _candidate(drawing, _rect, _transform, _plot):
        return drawing.get("candidate")

    @staticmethod
    def _is_curve_color(color):
        return color in {(1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.5, 0.0, 0.5)}

    def _bind(self, drawings):
        return bind_two_source_color_legend(
            _Page(drawings),
            _IdentityTransform(),
            self.plot,
            self.curves,
            self.labels,
            drawing_candidate=self._candidate,
            is_curve_color=self._is_curve_color,
        )

    def test_exact_distinct_swatches_bind_one_to_one(self):
        drawings = [
            _line(self.red, 0, 20, 100, 20, candidate=self.curves[0]),
            _line(self.blue, 0, 70, 100, 70, candidate=self.curves[1]),
            _line(self.red, 20, 20, 50, 20),
            _line(self.blue, 20, 70, 50, 70),
        ]

        self.assertEqual(self._bind(drawings), [(25.0, 0), (125.0, 1)])

    def test_ambiguous_swatch_color_refuses(self):
        purple = (0.5, 0.0, 0.5)
        drawings = [
            _line(self.red, 0, 20, 100, 20, candidate=self.curves[0]),
            _line(self.blue, 0, 70, 100, 70, candidate=self.curves[1]),
            _line(purple, 20, 20, 50, 20),
            _line(self.blue, 20, 70, 50, 70),
        ]

        self.assertIsNone(self._bind(drawings))

    def test_duplicate_source_drawing_for_one_curve_refuses(self):
        drawings = [
            _line(self.red, 0, 20, 100, 20, candidate=self.curves[0]),
            _line(self.red, 0, 20, 100, 20, candidate=self.curves[0]),
            _line(self.blue, 0, 70, 100, 70, candidate=self.curves[1]),
            _line(self.red, 20, 20, 50, 20),
            _line(self.blue, 20, 70, 50, 70),
        ]

        self.assertIsNone(self._bind(drawings))

    def test_two_labels_cannot_bind_to_the_same_curve(self):
        drawings = [
            _line(self.red, 0, 20, 100, 20, candidate=self.curves[0]),
            _line(self.blue, 0, 70, 100, 70, candidate=self.curves[1]),
            _line(self.red, 20, 20, 50, 20),
            _line(self.red, 20, 70, 50, 70),
        ]

        self.assertIsNone(self._bind(drawings))

    def test_diode_wrapper_owns_normalized_temperature_words_inside_hint(self):
        panel = ChartPanel(
            "sample.pdf",
            "sample",
            1,
            1,
            "Body Diode",
            "body_diode",
            (0, 0, 100, 100),
            (0, 0, 100, 100),
            "crop.png",
            "",
            "",
            [],
        )
        page = _Page(
            [],
            words=[
                (60.0, 10.0, 90.0, 30.0, "25˚C"),
                (60.0, 60.0, 90.0, 80.0, "125°C"),
                (120.0, 10.0, 150.0, 30.0, "175°C"),
            ],
        )
        document = _Document(page)
        calibration = SimpleNamespace(hint=self.plot, plot=self.plot)
        sentinel = [(25.0, 0), (125.0, 1)]

        with patch(
            "datasheet_chart_digitizer.diode_legend_color.cv2.imread",
            return_value=np.zeros((100, 100), dtype=np.uint8),
        ), patch(
            "datasheet_chart_digitizer.diode_legend_color.CropTransform.for_chart",
            return_value=_IdentityTransform(),
        ), patch(
            "datasheet_chart_digitizer.diode_legend_color.pymupdf.open",
            return_value=document,
        ), patch(
            "datasheet_chart_digitizer.diode_legend_color.bind_two_source_color_legend",
            return_value=sentinel,
        ) as bind:
            result = colored_temperature_bindings(
                panel,
                Path("crop.png"),
                calibration,
                self.curves,
                drawing_candidate=self._candidate,
                is_curve_color=self._is_curve_color,
            )

        self.assertEqual(result, sentinel)
        self.assertEqual(bind.call_args.args[4], self.labels)


if __name__ == "__main__":
    unittest.main()
