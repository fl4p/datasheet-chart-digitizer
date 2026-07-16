from __future__ import annotations

import unittest

from datasheet_chart_digitizer.numeric_axis import axis_to_json, fit_numeric_axis


class EngineeringTimeAxisTests(unittest.TestCase):
    def test_scientific_and_si_time_tokens_share_the_log_axis_fit(self):
        axis = fit_numeric_axis(
            [("1E-5", 10), ("100u", 60), ("1m", 110), ("10m", 160), ("0.1", 210)],
            "pulse duration",
            quantity="time_s",
            text_source="native",
        )
        self.assertEqual(axis.model, "log10")
        self.assertLess(axis.residual_px, 1e-9)
        self.assertEqual([tick.text for tick in axis.ticks], ["1E-5", "100u", "1m", "10m", "0.1"])
        for actual, expected in zip(
            [tick.value for tick in axis.ticks], [1e-5, 1e-4, 1e-3, 1e-2, 1e-1], strict=True
        ):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual(axis.ticks[0].normalized_text, "1e-5")

    def test_micro_glyph_and_kilo_seconds_are_time_context_only(self):
        axis = fit_numeric_axis(
            [("10µ", 0), ("1m", 100), ("0.1", 200), ("10", 300), ("1k", 400)],
            "wide pulse duration",
            quantity="time_s",
        )
        self.assertEqual(axis.model, "log10")
        for actual, expected in zip(
            [tick.value for tick in axis.ticks], [1e-5, 1e-3, 0.1, 10.0, 1000.0], strict=True
        ):
            self.assertAlmostEqual(actual, expected)
        for labels in (
            [("1m", 0), ("10m", 100), ("100m", 200)],
            [("1E-5", 0), ("1E-4", 100), ("1E-3", 200)],
        ):
            with self.subTest(labels=labels), self.assertRaisesRegex(RuntimeError, "non-numeric"):
                fit_numeric_axis(labels, "ordinary axis")

    def test_tesseract_im_correction_requires_a_bracketed_log_sequence(self):
        axis = fit_numeric_axis(
            [("10u", 0), ("100u", 50), ("Im", 100), ("10m", 150), ("100m", 200)],
            "OCR pulse duration",
            quantity="time_s",
            text_source="tesseract",
        )
        corrected = axis.ticks[2]
        self.assertEqual(corrected.text, "Im")
        self.assertEqual(corrected.normalized_text, "1m")
        self.assertEqual(corrected.value, 1e-3)
        self.assertEqual(axis_to_json(axis)["ticks"][2]["normalized_text"], "1m")

        for source, quantity in (("native", "time_s"), ("tesseract", None)):
            with self.subTest(source=source, quantity=quantity), self.assertRaisesRegex(
                RuntimeError, "non-numeric"
            ):
                fit_numeric_axis(
                    [("10u", 0), ("100u", 50), ("Im", 100), ("10m", 150)],
                    "ordinary Im",
                    quantity=quantity,
                    text_source=source,
                )

        with self.assertRaisesRegex(RuntimeError, "sequence"):
            fit_numeric_axis(
                [("10u", 0), ("Im", 60), ("10m", 100), ("100m", 150)],
                "misplaced OCR token",
                quantity="time_s",
                text_source="tesseract",
            )

    def test_lowercase_l_correction_is_tesseract_only_and_cannot_be_an_endpoint(self):
        axis = fit_numeric_axis(
            [("10u", 0), ("100u", 50), ("lm", 100), ("10m", 150), ("100m", 200)],
            "OCR lowercase-l pulse duration",
            quantity="time_s",
            text_source="tesseract",
        )
        self.assertEqual(axis.ticks[2].text, "lm")
        self.assertEqual(axis.ticks[2].normalized_text, "1m")
        with self.assertRaisesRegex(RuntimeError, "non-numeric"):
            fit_numeric_axis(
                [("10u", 0), ("100u", 50), ("lm", 100), ("10m", 150)],
                "native lowercase-l",
                quantity="time_s",
                text_source="native",
            )
        for labels in (
            [("lm", 0), ("10m", 50), ("100m", 100)],
            [("10u", 0), ("100u", 50), ("lm", 100)],
        ):
            with self.subTest(labels=labels), self.assertRaisesRegex(
                RuntimeError, "not sequence-bracketed"
            ):
                fit_numeric_axis(
                    labels,
                    "endpoint OCR correction",
                    quantity="time_s",
                    text_source="tesseract",
                )

    def test_existing_decimal_and_log_json_contract_is_unchanged(self):
        linear = axis_to_json(fit_numeric_axis([("0", 10), ("0.5", 60), ("1.0", 110)]))
        logarithmic = axis_to_json(fit_numeric_axis([("1", 10), ("10", 60), ("100", 110)]))
        self.assertEqual(
            linear,
            {
                "model": "linear",
                "m": 0.010000000000000002,
                "b": -0.1000000000000003,
                "residual_px": 2.209167143284393e-14,
                "candidate_residuals_px": {"linear": 2.209167143284393e-14},
                "ticks": [
                    {"text": "0", "value": 0.0, "pixel": 10.0},
                    {"text": "0.5", "value": 0.5, "pixel": 60.0},
                    {"text": "1.0", "value": 1.0, "pixel": 110.0},
                ],
            },
        )
        self.assertEqual(logarithmic["model"], "log10")
        self.assertEqual(logarithmic["ticks"], [
            {"text": "1", "value": 1.0, "pixel": 10.0},
            {"text": "10", "value": 10.0, "pixel": 60.0},
            {"text": "100", "value": 100.0, "pixel": 110.0},
        ])


if __name__ == "__main__":
    unittest.main()
