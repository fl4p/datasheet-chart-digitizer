"""Title matching folds typographic dash variants (bench round 1, bug 1).

IPP050N03LF2S and ISC040N10NM8 print "Normalized drain‑source on resistance"
with U+2011 (non-breaking hyphen). The RDS(on)(Tj) title stem accepted only an
ASCII '-' in "drain-source", so the finder-identified panel was skipped with no
result and no refusal.
"""

from __future__ import annotations

import unittest

from datasheet_chart_digitizer.axis_title_identity import body_diode_title_contradiction
from datasheet_chart_digitizer.chart_classifier import classify_chart
from datasheet_chart_digitizer.find_charts import ChartPanel, PageText, Word
from datasheet_chart_digitizer.rdson_current import _rdson_current_titles
from datasheet_chart_digitizer.rdson_temperature import (
    _rdson_temperature_panel_owned,
    _rdson_temperature_titles,
)
from datasheet_chart_digitizer.text_dashes import normalize_dashes

DASHES = {
    "U+2010 hyphen": "‐",
    "U+2011 non-breaking hyphen": "‑",
    "U+2012 figure dash": "‒",
    "U+2013 en dash": "–",
    "U+2014 em dash": "—",
    "U+2015 horizontal bar": "―",
    "U+2212 minus": "−",
    "U+00AD soft hyphen": "­",
}


def _page(caption: str) -> PageText:
    words = []
    x = 45.0
    for text in caption.split():
        words.append(Word(text, x, 100, x + 8, 108))
        x += 9
    return PageText(1, 612, 792, words)


class TitleDashTests(unittest.TestCase):
    def test_every_dash_variant_folds_to_ascii(self) -> None:
        for name, dash in DASHES.items():
            with self.subTest(dash=name):
                self.assertEqual(normalize_dashes(f"drain{dash}source"), "drain-source")
        self.assertEqual(normalize_dashes("plain-ascii"), "plain-ascii")

    def test_rdson_temperature_stem_title_found_with_every_dash(self) -> None:
        # Known-bad calibration: main returned [] for the U+2011 spelling.
        for name, dash in DASHES.items():
            with self.subTest(dash=name):
                titles = _rdson_temperature_titles(
                    _page(f"Diagram 9: Normalized drain{dash}source on resistance")
                )
                self.assertEqual(
                    [(title.number, title.title) for title in titles],
                    [(9, "Normalized drain-source on resistance")],
                )

    def test_rdson_temperature_ownership_reads_folded_title(self) -> None:
        panel = ChartPanel(
            "sample.pdf", "sample", 9, 9,
            "Normalized drain‑source on resistance", "rds_on",
            (0, 0, 100, 100), (0, 0, 100, 100), "crop.png",
            "T j [°C] R DS(on) =f(T j ), I D =25 A, V GS =10 V", "", [],
        )
        self.assertTrue(_rdson_temperature_panel_owned(panel))

    def test_rdson_current_title_found_with_nonbreaking_hyphen(self) -> None:
        titles = _rdson_current_titles(
            _page("Figure 6: Drain‑source on‑resistance vs drain current")
        )
        self.assertEqual([title.number for title in titles], [6])

    def test_classifier_folds_dashes_in_titles(self) -> None:
        for name, dash in DASHES.items():
            with self.subTest(dash=name):
                self.assertEqual(
                    classify_chart(f"Min. drain{dash}source breakdown voltage", "T j [°C]"),
                    "breakdown_voltage",
                )

    def test_body_diode_axis_identity_reads_folded_source_to_drain(self) -> None:
        # "gate" in a neighbour's text must not contradict an owned
        # source-to-drain title just because it is spelled with U+2011.
        self.assertIsNone(
            body_diode_title_contradiction(
                "V SD, Source‑to‑Drain Voltage (V) gate",
                "I S, Source Current (A)",
            )
        )


if __name__ == "__main__":
    unittest.main()
