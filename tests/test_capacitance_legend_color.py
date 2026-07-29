"""Legend-color identity binding for colored vector capacitance charts.

EPC GaN datasheets print Ciss/Coss/Crss as three colored curves with a
colored-swatch legend. Identity must come from that source evidence, bind
decisively, and refuse on any ambiguity -- never guess.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

import numpy as np

from datasheet_chart_digitizer.capacitance_vector import (
    _is_chromatic_stroke,
    _legend_color_names,
    _legend_swatch_bindings,
)


class _Rect:
    def __init__(self, x0, y0, x1, y1):
        self.x0, self.y0, self.x1, self.y1 = x0, y0, x1, y1

    @property
    def height(self):
        return self.y1 - self.y0

    @property
    def width(self):
        return self.x1 - self.x0


class _Page:
    def __init__(self, words, drawings):
        self._words = words
        self._drawings = drawings

    def get_text(self, kind):
        return list(self._words)

    def get_drawings(self):
        return list(self._drawings)


def _swatch(x0, y, x1, color):
    return {"color": color, "rect": _Rect(x0, y, x1, y)}


CLIP = _Rect(60.0, 70.0, 285.0, 240.0)
DARK_RED = (0.6, 0.12, 0.17)
DARK_GREEN = (0.27, 0.63, 0.48)
LIGHT_GREEN = (0.65, 0.81, 0.22)


def _legend_words():
    return [
        (215.0, 126.0, 223.0, 136.0, "COSS"),
        (215.0, 135.5, 222.0, 145.0, "CISS"),
        (215.0, 145.0, 223.0, 154.5, "CRSS"),
    ]


def _legend_swatches():
    return [
        _swatch(200.0, 130.5, 211.0, DARK_RED),
        _swatch(200.0, 140.3, 211.0, DARK_GREEN),
        _swatch(200.0, 150.0, 211.0, LIGHT_GREEN),
    ]


class LegendSwatchBindingTests(unittest.TestCase):
    def test_three_row_legend_binds(self) -> None:
        page = _Page(_legend_words(), _legend_swatches())
        bindings = _legend_swatch_bindings(page, CLIP)
        self.assertEqual(
            bindings,
            {"Coss": DARK_RED, "Ciss": DARK_GREEN, "Crss": LIGHT_GREEN},
        )

    def test_formula_occurrence_without_swatch_is_skipped(self) -> None:
        # "COSS" also appears in the Qoss formula below the chart with no
        # adjacent swatch; that word must not poison the legend.
        words = _legend_words() + [(200.0, 228.0, 214.0, 238.0, "COSS")]
        page = _Page(words, _legend_swatches())
        bindings = _legend_swatch_bindings(page, CLIP)
        self.assertIsNotNone(bindings)
        assert bindings is not None
        self.assertEqual(bindings["Coss"], DARK_RED)

    def test_no_legend_returns_none(self) -> None:
        page = _Page([(100.0, 100.0, 130.0, 110.0, "Ciss")], [])
        self.assertIsNone(_legend_swatch_bindings(page, CLIP))

    def test_incomplete_legend_returns_none(self) -> None:
        # TI prints two-row swatch legends; partial legend evidence must fall
        # back to positional naming, never break a working extraction.
        page = _Page(_legend_words()[:2], _legend_swatches()[:2])
        self.assertIsNone(_legend_swatch_bindings(page, CLIP))

    def test_two_colors_eligible_for_one_row_return_none(self) -> None:
        # A decoy colored segment printed slightly CLOSER to the word than the
        # real swatch must not win on proximity: that yields a complete,
        # positionally-consistent, wrong mapping. Ambiguity is unreadable
        # evidence, not a tie to break.
        swatches = _legend_swatches() + [
            _swatch(203.0, 130.5, 213.0, (0.1, 0.2, 0.9)),  # closer decoy
        ]
        page = _Page(_legend_words(), swatches)
        self.assertIsNone(_legend_swatch_bindings(page, CLIP))

    def test_non_distinct_swatch_colors_return_none(self) -> None:
        swatches = [
            _swatch(200.0, 130.5, 211.0, DARK_RED),
            _swatch(200.0, 140.3, 211.0, DARK_RED),
            _swatch(200.0, 150.0, 211.0, LIGHT_GREEN),
        ]
        page = _Page(_legend_words(), swatches)
        self.assertIsNone(_legend_swatch_bindings(page, CLIP))


class LegendColorNamingTests(unittest.TestCase):
    @staticmethod
    def _candidates():
        flat = [(i, 30) for i in range(60, 280)]
        steep = [(i, 30 + i // 2) for i in range(60, 280)]
        low = [(i, 235) for i in range(60, 280)]
        return [(220.0, flat), (260.0, steep), (200.0, low)]

    def test_epc2361_shade_still_binds_ciss(self) -> None:
        # EPC2361 draws Ciss in (0.0, 0.47, 0.29) while its own swatch is the
        # lighter (0.27, 0.63, 0.48): distance 0.37 with the runner-up 1.9x
        # away must still bind to Ciss.
        candidates = self._candidates()
        colors = {
            id(candidates[0][1]): (0.0, 0.47, 0.29),
            id(candidates[1][1]): (0.5, 0.08, 0.09),
            id(candidates[2][1]): (0.61, 0.79, 0.23),
        }
        page = _Page(_legend_words(), _legend_swatches())
        named = _legend_color_names(page, CLIP, candidates, colors)
        self.assertIsNotNone(named)
        assert named is not None
        self.assertEqual(named[id(candidates[0][1])], "Ciss")
        self.assertEqual(named[id(candidates[1][1])], "Coss")
        self.assertEqual(named[id(candidates[2][1])], "Crss")

    def test_unmatched_curve_color_returns_none(self) -> None:
        candidates = self._candidates()
        colors = {
            id(candidates[0][1]): (0.0, 0.0, 1.0),  # blue: matches no swatch
            id(candidates[1][1]): (0.5, 0.08, 0.09),
            id(candidates[2][1]): (0.61, 0.79, 0.23),
        }
        page = _Page(_legend_words(), _legend_swatches())
        self.assertIsNone(_legend_color_names(page, CLIP, candidates, colors))

    def test_two_curves_binding_one_row_returns_none(self) -> None:
        candidates = self._candidates()
        colors = {
            id(candidates[0][1]): (0.6, 0.12, 0.17),
            id(candidates[1][1]): (0.5, 0.08, 0.09),  # also nearest DARK_RED
            id(candidates[2][1]): (0.61, 0.79, 0.23),
        }
        page = _Page(_legend_words(), _legend_swatches())
        self.assertIsNone(_legend_color_names(page, CLIP, candidates, colors))

    def test_uncolored_candidates_return_none(self) -> None:
        candidates = self._candidates()
        page = _Page(_legend_words(), _legend_swatches())
        self.assertIsNone(_legend_color_names(page, CLIP, candidates, {}))


class ChromaticStrokeTests(unittest.TestCase):
    def test_saturated_colors_are_chromatic(self) -> None:
        self.assertTrue(_is_chromatic_stroke((0.65, 0.81, 0.22)))
        self.assertTrue(_is_chromatic_stroke((0.6, 0.12, 0.17)))

    def test_neutral_strokes_are_not(self) -> None:
        self.assertFalse(_is_chromatic_stroke((0.0, 0.0, 0.0)))
        self.assertFalse(_is_chromatic_stroke((0.82, 0.83, 0.83)))
        self.assertFalse(_is_chromatic_stroke(None))


_GAN = Path(
    "/Users/fab/dev/pv/pwr-mosfet-lib/out/fugu2-100v-LS2p-gan/coss-review-top50-2026-07-28"
)


@unittest.skipUnless(_GAN.exists(), "local GaN packet not available")
class EpcEndToEndTests(unittest.TestCase):
    @staticmethod
    def _extract(part: str, diagram: int):
        from PIL import Image
        from datasheet_chart_digitizer.capacitance_plot_box import (
            find_capacitance_plot_box,
        )
        from datasheet_chart_digitizer.capacitance_vector import (
            extract_vector_trace_components_with_provenance,
        )

        charts = json.loads((_GAN / "charts.json").read_text())
        chart = next(
            c for c in charts if c["part"] == part and c["diagram"] == diagram
        )
        image = np.asarray(Image.open(_GAN / chart["crop_png"]).convert("L"))
        plot = find_capacitance_plot_box(image)
        return extract_vector_trace_components_with_provenance(chart, image, plot)

    def test_epc2367_binds_identities_from_legend(self) -> None:
        traces, method = self._extract("EPC2367", 901)
        self.assertEqual(method, "legend_color_components")
        by_name = {t.name: t for t in traces}
        right_y = {
            name: trace.points[-1][1] for name, trace in by_name.items()
        }
        self.assertLess(right_y["Ciss"], right_y["Coss"])
        self.assertLess(right_y["Coss"], right_y["Crss"])

    def test_epc2367_legend_reads_the_documents_own_swatch_colors(self) -> None:
        # Independent of the positional ordering the production path also
        # computes: assert the SOURCE colors this datasheet prints, so a
        # regression that silently stops reading swatches (and falls back to
        # positional naming) fails here instead of passing on order alone.
        import pymupdf

        from datasheet_chart_digitizer.capacitance_vector import (
            _legend_swatch_bindings,
        )

        charts = json.loads((_GAN / "charts.json").read_text())
        chart = next(
            c for c in charts if c["part"] == "EPC2367" and c["diagram"] == 901
        )
        doc = pymupdf.open(chart["pdf"])
        page = doc[chart["page"] - 1]
        bindings = _legend_swatch_bindings(page, pymupdf.Rect(*chart["crop_box_pt"]))
        self.assertEqual(
            bindings,
            {
                "Coss": (0.6, 0.12, 0.17),
                "Ciss": (0.0, 0.47, 0.29),
                "Crss": (0.65, 0.81, 0.22),
            },
        )

    def test_epc2088_dashed_flat_tails_recover(self) -> None:
        # Dashed flat Ciss carries its right half in one endpoint vertex and
        # the near-zero Crss tail rides the bottom axis; both must survive.
        traces, method = self._extract("EPC2088", 901)
        self.assertEqual(method, "legend_color_components")
        for trace in traces:
            xs = [p[0] for p in trace.points]
            self.assertGreater(max(xs) - min(xs), 400)


if __name__ == "__main__":
    unittest.main()
