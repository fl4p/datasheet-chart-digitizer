import unittest

from datasheet_chart_digitizer import find_charts


class DetachedNumberedCaptionTests(unittest.TestCase):
    def test_two_column_number_row_owns_supported_titles_below(self) -> None:
        words = [
            find_charts.Word("Fig.", 39, 80, 55, 91),
            find_charts.Word("1", 59, 80, 65, 91),
            find_charts.Word("Fig.", 309, 80, 325, 91),
            find_charts.Word("2", 329, 80, 335, 91),
            find_charts.Word("Typical", 115, 98, 144, 109),
            find_charts.Word("Transfer", 148, 98, 189, 109),
            find_charts.Word("Characteristics", 193, 98, 247, 109),
            find_charts.Word("Typical", 392, 98, 420, 109),
            find_charts.Word("Output", 424, 98, 457, 109),
            find_charts.Word("Characteristics", 461, 98, 515, 109),
            find_charts.Word("Fig.", 39, 314, 55, 325),
            find_charts.Word("3", 59, 314, 65, 325),
            find_charts.Word("Fig.", 309, 314, 325, 325),
            find_charts.Word("4", 329, 314, 335, 325),
            find_charts.Word("Gate", 94, 321, 119, 332),
            find_charts.Word("Charge", 123, 321, 157, 332),
            find_charts.Word("vs.", 161, 321, 176, 332),
            find_charts.Word("Gate-to-Source", 180, 321, 248, 332),
            find_charts.Word("Voltage", 252, 321, 288, 332),
            find_charts.Word("Extended", 402, 321, 445, 332),
            find_charts.Word("Typical", 449, 321, 477, 332),
            find_charts.Word("Output", 481, 321, 514, 332),
            find_charts.Word("Characteristics", 518, 321, 586, 332),
        ]
        page = find_charts.PageText(3, 612, 792, words)

        titles = find_charts.find_caption_titles(page)

        self.assertEqual(
            [(title.number, title.title) for title in titles],
            [
                (1, "Typical Transfer Characteristics"),
                (3, "Gate Charge vs. Gate-to-Source Voltage"),
            ],
        )
        self.assertTrue(all(title.number < 900 for title in titles))
        self.assertLess(titles[0].bbox_pt[1], 90)
        self.assertGreater(titles[0].bbox_pt[3], 108)

    def test_number_row_does_not_claim_distant_prose(self) -> None:
        page = find_charts.PageText(
            1,
            612,
            792,
            [
                find_charts.Word("Fig.", 39, 80, 55, 91),
                find_charts.Word("5", 59, 80, 65, 91),
                find_charts.Word("Gate", 120, 125, 145, 136),
                find_charts.Word("Charge", 149, 125, 183, 136),
                find_charts.Word("Characteristics", 187, 125, 255, 136),
            ],
        )

        titles = find_charts.find_caption_titles(page)
        self.assertEqual(len(titles), 1)
        self.assertGreaterEqual(titles[0].number, 900)
        self.assertEqual(titles[0].bbox_pt, (120, 125, 255, 136))

    def test_number_row_does_not_promote_unsupported_output_title(self) -> None:
        page = find_charts.PageText(
            1,
            612,
            792,
            [
                find_charts.Word("Fig.", 309, 80, 325, 91),
                find_charts.Word("2", 329, 80, 335, 91),
                find_charts.Word("Typical", 392, 98, 420, 109),
                find_charts.Word("Output", 424, 98, 457, 109),
                find_charts.Word("Characteristics", 461, 98, 515, 109),
            ],
        )

        self.assertEqual(find_charts.find_caption_titles(page), [])
