"""A curve merged into a rule's run must not move the rule, nor invent one.

Found on the vendor-truth benchmark: on Infineon ISC058N04NM5 (Diagram 5,
output characteristics) the V_GS = 4 V curve runs ~3 px above the 0 A rule.
On the raw raster both form one 6 px run whose centre is 2 px off the rule;
the curve-ink-suppressed raster sees the rule alone. The two rasters bound
"0" to different lines and the y axis was refused as contradictory.

``detect_axis_lines`` now splits a run that is anomalously wide against the
chart's other rules AND whose coverage dips between two strokes. The pieces
are candidate lines (source ``split_rule``); the registration chooses among
them exactly as among separate lines, so a piece the pitch cannot seat is
not bound and two that both fit still refuse. A curve lying ON the rule (no
dip) is left as it is.
"""

from __future__ import annotations

import unittest
from unittest import mock
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image

from datasheet_chart_digitizer import gridline_anchor as ga
from datasheet_chart_digitizer.gridline_anchor import (
    AmbiguousRegistration,
    ObservedLine,
    anchor_axis_on_grid_attempts,
    check_served_on_grid_attempts,
    detect_axis_lines,
)
from datasheet_chart_digitizer.numeric_axis import AxisTick, NumericAxis, fit_axis_ticks

FIXTURE = Path(__file__).parent / "fixtures" / "gridline_merged_curve" / "ifx_ISC058N04NM5_d05_output.webp"
# value: (vector label centre, vector rule), pixel-index coordinates (see PROVENANCE.md)
Y_TRUTH = {250.0: (57.562, 57.292), 200.0: (177.962, 177.692), 150.0: (298.362, 298.092),
           100.0: (418.762, 418.492), 50.0: (539.162, 538.892), 0.0: (659.561, 659.291)}
PLOT = SimpleNamespace(x0=97.18, y0=57.29, x1=637.32, y1=659.29)
SPAN = (PLOT.x0, PLOT.x1)


def _shifted(axis: NumericAxis, shift_px: float) -> NumericAxis:
    return NumericAxis(axis.model, axis.m, axis.b - axis.m * shift_px, (), 0.0, ())


def _isc058():
    gray = np.asarray(Image.open(FIXTURE).convert("L"))
    labels = [AxisTick(f"{v:g}", v, lab) for v, (lab, _) in Y_TRUTH.items()]
    true_axis = fit_axis_ticks(
        [AxisTick(f"{v:g}", v, rule) for v, (_, rule) in Y_TRUTH.items()], "y", model="linear"
    )
    return gray, labels, true_axis


class ISC058N04NM5MergedRuleTests(unittest.TestCase):
    def test_raw_raster_splits_the_merged_zero_amp_run(self):
        gray, labels, _ = _isc058()
        lines = detect_axis_lines(
            gray, orientation="y", along=(20.0, 700.0), cross_span=SPAN,
            max_width=10, tick_band=6,
        )
        near = [(line.center_px, line.width_px, line.source) for line in lines
                if 650 < line.center_px < 665]
        # main: one 6 px run centred 657.5 (curve + gap + rule)
        self.assertEqual(near, [(656.0, 3, "split_rule"), (659.5, 2, "split_rule")])
        # every other rule of the chart is untouched
        self.assertTrue(all(line.source == "gridline" for line in lines
                            if not 650 < line.center_px < 665))

    def test_y_axis_is_served_on_the_vector_rules(self):
        gray, labels, _ = _isc058()
        anchored, _attempt = anchor_axis_on_grid_attempts(
            gray, fit_axis_ticks(labels, "y", model="linear"), frame=PLOT,
            orientation="y", cross_span=SPAN, name="y",
        )
        self.assertEqual({a.value for a in anchored.anchors}, set(Y_TRUTH))
        for anchor in anchored.anchors:
            self.assertLess(abs(anchor.served_px - Y_TRUTH[anchor.value][1]), 0.5, anchor)

    def test_served_check_verifies_truth_and_fails_the_merged_centre(self):
        """Known-bad control: serving 0 A on the merged run's centre (2 px
        off) or on the curve stroke (3.3 px off) must fail."""
        gray, labels, true_axis = _isc058()
        check = check_served_on_grid_attempts(
            gray, true_axis, labels, frame=PLOT, orientation="y", cross_span=SPAN, name="y"
        )
        self.assertEqual(check.status, "verified", check.reason)
        for shift in (-2.0, -3.3, 2.0, 120.4):
            with self.subTest(shift=shift):
                check = check_served_on_grid_attempts(
                    gray, _shifted(true_axis, shift), labels, frame=PLOT,
                    orientation="y", cross_span=SPAN, name="y",
                )
                self.assertEqual(check.status, "failed", check.reason)


# ----------------------------------------------------------------- synthetic
# 6 labelled 2 px grey rules 120 px apart, grey minors, and a black curve
# hugging the 0 rule drawn row by row as (row offset from the rule, share of
# the span covered from the left). ISC058N04NM5 measured 0.76/0.92/0.94 on
# the curve rows and 0.53 on the gap row.
H, W, LEFT, RIGHT, TOP, PITCH = 720, 640, 100, 620, 60, 120.0
SIX = (250.0, 200.0, 150.0, 100.0, 50.0, 0.0)


def _rule_row(value: float) -> int:
    return int(round(TOP + (250.0 - value) / 50.0 * PITCH))


def _panel(curve=(), extra_rule=None, rule_grey=128):
    image = np.full((H, W), 255, dtype=np.uint8)
    for value in SIX:
        image[_rule_row(value):_rule_row(value) + 2, LEFT:RIGHT + 1] = rule_grey
    for value in np.arange(10.0, 250.0, 10.0):
        if value % 50:
            image[_rule_row(value):_rule_row(value) + 2, LEFT:RIGHT + 1] = 185
    image[_rule_row(250):_rule_row(0) + 2, LEFT:LEFT + 2] = rule_grey
    image[_rule_row(250):_rule_row(0) + 2, RIGHT - 1:RIGHT + 1] = rule_grey
    if extra_rule is not None:
        image[_rule_row(0) + extra_rule:_rule_row(0) + extra_rule + 2, LEFT:RIGHT + 1] = rule_grey
    for offset, share in curve:
        image[_rule_row(0) + offset, LEFT:LEFT + int(share * (RIGHT - LEFT)) + 1] = 0
    return image


def _isc_curve(gap_share=0.5):
    return ((-4, 0.76), (-3, 0.95), (-2, 0.95), (-1, gap_share))


def _true_px(value: float) -> float:
    return _rule_row(value) + 0.5


def _anchor(image, values=SIX):
    labels = [AxisTick(f"{v:g}", v, _true_px(v)) for v in values]
    frame = SimpleNamespace(x0=LEFT, y0=_true_px(max(values)), x1=RIGHT, y1=_true_px(min(values)))
    return anchor_axis_on_grid_attempts(
        image, fit_axis_ticks(labels, "y", model="linear"), frame=frame,
        orientation="y", cross_span=(LEFT, RIGHT), name="y",
    )


def _max_error(anchored) -> float:
    return max(abs(a.served_px - _true_px(a.value)) for a in anchored.anchors)


class SyntheticMergedRuleTests(unittest.TestCase):
    def test_isc058_analogue_binds_the_rule_not_the_merged_centre(self):
        anchored, _ = _anchor(_panel(_isc_curve()))
        self.assertLess(_max_error(anchored), 0.5)

    def test_black_rules_too(self):
        anchored, _ = _anchor(_panel(_isc_curve(), rule_grey=0))
        self.assertLess(_max_error(anchored), 0.5)

    def test_curve_lying_on_the_rule_is_not_split(self):
        """No dip, no split: the run is kept whole, never a guessed centre."""
        for curve in (((-1, .95), (0, .95), (1, .95), (2, .95)),
                      ((-2, .95), (-1, .95), (0, .95)),
                      ((-3, .95), (-2, .95), (-1, .95))):
            with self.subTest(curve=curve):
                lines = detect_axis_lines(
                    _panel(curve), orientation="y", along=(20.0, 700.0),
                    cross_span=(LEFT, RIGHT), max_width=10, tick_band=6,
                )
                self.assertFalse([line for line in lines if line.source == "split_rule"])

    def test_dip_sweep_is_monotone_and_never_serves_wrong(self):
        """As the gap row fills in (the evidence for two strokes weakens) the
        split fires on a prefix of the sweep only, and never comes back.
        Where it fires the axis is served on the rule; where it does not, the
        result is exactly what detection without the split gives."""
        fired, rows = [], []
        for gap in np.arange(0.40, 0.96, 0.05):
            image = _panel(_isc_curve(float(gap)))
            lines = detect_axis_lines(
                image, orientation="y", along=(20.0, 700.0), cross_span=(LEFT, RIGHT),
                max_width=10, tick_band=6,
            )
            fired.append(any(line.source == "split_rule" for line in lines))
            outcome = self._result_of(image)
            with mock.patch.object(ga, "_split_merged_runs", lambda lines, cov, off: lines):
                without = self._result_of(image)
            rows.append((round(float(gap), 2), fired[-1], outcome, without))
            if fired[-1]:
                self.assertEqual(outcome[0], "served", rows[-1])
                self.assertLess(outcome[1], 0.5, rows[-1])
            else:
                self.assertEqual(outcome, without, rows[-1])
        self.assertTrue(fired[0] and not fired[-1], rows)
        self.assertEqual(fired, sorted(fired, reverse=True), rows)  # True... then False...

    @staticmethod
    def _result_of(image):
        try:
            anchored, _ = _anchor(image)
        except RuntimeError:
            return ("refused", None)
        return ("served", round(_max_error(anchored), 3))

    def test_curve_fused_with_the_rule_is_served_on_the_rule(self):
        """Was the pinned OPEN case (8b9ed96, main 6a1a61b): with the gap row
        >= 80 % ink both rasters saw one fused 6 px run, agreed, and served
        0 A 0.78 px off. ``_guard_fused_runs`` re-measures the run where the
        curve leaves the rule (the last 5 % of the span) and serves it there."""
        anchored, _ = _anchor(_panel(_isc_curve(0.9)))
        self.assertLess(_max_error(anchored), 0.5)
        self.assertIn("recovered_rule", {a.source for a in anchored.anchors})

    def test_pieces_the_pitch_cannot_tell_apart_refuse(self):
        """Known-bad: with no interior label (or one far from the end), curve
        stroke and rule both fit the labels; the registration must tie and
        refuse rather than pick a piece by proximity."""
        for name, image in (("curve", _panel(_isc_curve())),
                            ("two rules", _panel(((-2, .5), (-1, .5)), extra_rule=-4))):
            for values in ((250.0, 0.0), (250.0, 100.0, 0.0)):
                with self.subTest(case=name, labels=values):
                    with self.assertRaisesRegex(RuntimeError, "equally well"):
                        _anchor(image, values)

    def test_split_pieces_never_settle_a_tie_by_rule_weight(self):
        """A black curve stroke outweighs a grey rule: the opt-in major-rule
        evidence must not compare a split piece."""
        tied = [(0, 0.0, (0, 2)), (0, 0.0, (1, 3))]
        lines = [ObservedLine(10.0, 2, "split_rule"), ObservedLine(13.5, 2, "split_rule"),
                 ObservedLine(200.0, 2, "split_rule"), ObservedLine(203.5, 2, "split_rule")]
        gray = np.full((220, 50), 255, dtype=np.uint8)
        gray[9:11, :] = 0
        gray[199:201, :] = 0
        gray[13:15, :] = 128
        gray[203:205, :] = 128
        self.assertIsNone(ga._heavier_rule_registration(gray, lines, tied, "y", (0, 49)))

    def test_trigger_needs_an_anomalous_width_and_peers(self):
        cov = np.array([0.0, 1.0, 1.0, 0.5, 1.0, 1.0, 0.0])
        merged = ObservedLine(3.0, 5, "gridline")
        peers = [ObservedLine(100.0 + 50 * i, 2, "gridline") for i in range(3)]
        coverage = np.zeros(300)
        coverage[1:6] = cov[1:6]
        # with three peers of width 2 the 5 px run is anomalous and dips: split
        split = ga._split_merged_runs([merged, *peers], coverage, 0)
        self.assertEqual([(l.center_px, l.width_px) for l in split if l.source == "split_rule"],
                         [(1.5, 2), (4.5, 2)])
        # two peers cannot establish the chart's rule width: untouched
        self.assertEqual(ga._split_merged_runs([merged, *peers[:2]], coverage, 0),
                         [merged, *peers[:2]])
        # a black 3 px frame among 1 px hairlines is not anomalous enough
        frame = ObservedLine(3.0, 3, "gridline")
        hair = [ObservedLine(100.0 + 50 * i, 1, "gridline") for i in range(3)]
        self.assertEqual(ga._split_merged_runs([frame, *hair], coverage, 0), [frame, *hair])



# ------------------------------------------------------- fused, no dip at all
# A curve lying on the 0 rule, or beside it with the gap row filled in, fuses
# with it into one wide run that _split_merged_runs cannot split; both rasters
# agree on its centre. _guard_fused_runs re-measures the run where the curve
# leaves the rule, refuses a binding to it when it shows two strokes along its
# whole length, and otherwise keeps it (a bold rule).
def _fused(gap=0.9, share=0.95, grey=128):
    curve = ((-4, min(0.76, share)), (-3, share), (-2, share), (-1, gap * share))
    return _panel(curve, rule_grey=grey)


def _on_rule(rows, share=0.95, grey=128):
    return _panel(tuple((row, share) for row in rows), rule_grey=grey)


def _outcome(image):
    try:
        anchored, _ = _anchor(image)
    except RuntimeError as exc:
        return ("refused", str(exc))
    return ("served", round(_max_error(anchored), 3))


def _served_wrong(outcome) -> bool:
    return outcome[0] == "served" and outcome[1] >= 0.5


TY_FIXTURE = (Path(__file__).parent / "fixtures" / "gridline_fused_rule"
              / "ty_MBASL042SCG1R5CWNA01_c_vs_f.webp")
# value: (vector label centre, vector rule), case px (see PROVENANCE.md)
TY_TICKS = {
    "x": [(1.0, 95.158, 95.237), (10.0, 169.889, 169.994), (100.0, 244.796, 244.761),
          (1000.0, 319.522, 319.518), (10000.0, 394.256, 394.018), (100000.0, 469.112, 468.771)],
    "y": [(100.0, 50.033, 50.565), (10.0, 158.684, 159.175), (1.0, 267.35, 267.784),
          (0.1, 376.01, 376.393)],
}
TY_BOX = (95.24, 50.57, 468.76, 376.39)


class FusedRuleTests(unittest.TestCase):
    def test_partial_fusion_is_recovered_at_any_gap_ink(self):
        """Known-bad: gap row 80-100 % ink, curve along 50 % or 95 % of the
        rule. 8b9ed96 refused at 0.80 and served 0.78 px off from 0.90."""
        for grey in (128, 0):
            for share in (0.5, 0.95):
                for gap in (0.8, 0.9, 1.0):
                    with self.subTest(grey=grey, share=share, gap=gap):
                        outcome = _outcome(_fused(gap, share, grey))
                        self.assertEqual(outcome[0], "served", outcome)
                        self.assertLess(outcome[1], 0.5, outcome)

    def test_curve_on_the_rule_off_centre_is_recovered(self):
        """Known-bad: a 3-4 px curve ON the 2 px rule, centred 0-2 px off it,
        along 95 % of the span. 8b9ed96 served 0.52 / 0.52 / 0.78 px off for
        the three off-centre 4- and 3-row cases."""
        for rows in ((-1, 0, 1, 2), (-2, -1, 0, 1), (-1, 0, 1), (-2, -1, 0), (-3, -2, -1, 0)):
            for grey in (128, 0):
                with self.subTest(rows=rows, grey=grey):
                    outcome = _outcome(_on_rule(rows, 0.95, grey))
                    self.assertEqual(outcome[0], "served", outcome)
                    self.assertLess(outcome[1], 0.5, outcome)

    def test_whole_length_fusion_showing_two_strokes_refuses(self):
        """Known-bad: the curve runs the WHOLE rule, so nowhere does the rule
        show alone. Two strokes are still visible -- a grey rule under a black
        curve, or (black on black) paper between them along part of the
        length -- and the centre is refused, never guessed. 8b9ed96 served
        all four 0.78 px off."""
        for grey, gap in ((128, 0.9), (128, 1.0), (0, 0.9), (0, 0.95)):
            with self.subTest(grey=grey, gap=gap):
                outcome = _outcome(_fused(gap, 1.0, grey))
                self.assertEqual(outcome[0], "refused", outcome)
                self.assertIn("fused into along its whole length", outcome[1])

    @unittest.expectedFailure
    def test_known_open_rule_hidden_in_a_same_ink_bar(self):
        """OPEN: a curve along the WHOLE rule, same ink, gap filled (or the
        rule painted over): the run is a uniform bar with no stretch where the
        rule shows alone and no second stroke. That is what a bold rule looks
        like; refusing it on width alone refuses the bold decade rules of 11
        good vendor axes (Taiyo Yuden). Served 0.78 px off, as on 8b9ed96."""
        for image in (_fused(1.0, 1.0, 0), _on_rule((-3, -2, -1, 0), 1.0, 128)):
            self.assertFalse(_served_wrong(_outcome(image)))

    def test_gap_ink_sweep_never_serves_wrong(self):
        """Monotonicity: as the gap row fills in, at every share of the span
        and both rule inks, the verdict is served-on-the-rule or refused, and
        never comes back to served-wrong. The one exception is the far tail of
        the black, whole-length family (gap 1.00: a uniform bar, pinned as
        OPEN above); its sweep stops at 0.95."""
        for grey in (128, 0):
            for share in (0.5, 0.95, 1.0):
                top = 0.95 if (grey, share) == (0, 1.0) else 1.0
                rows = [(round(float(gap), 2), _outcome(_fused(float(gap), share, grey)))
                        for gap in np.arange(0.40, top + 1e-9, 0.05)]
                with self.subTest(grey=grey, share=share):
                    self.assertFalse([r for r in rows if _served_wrong(r[1])], rows)

    def test_curve_sliding_onto_the_rule_never_serves_wrong(self):
        """Monotonicity in position: a 3 px black curve along 95 % of the span
        moves from 6 px above the 0 rule onto it and through it."""
        for grey in (128, 0):
            for top in range(-6, 2):
                with self.subTest(grey=grey, top=top):
                    outcome = _outcome(_on_rule((top, top + 1, top + 2), 0.95, grey))
                    self.assertFalse(_served_wrong(outcome), outcome)

    def test_served_check_on_a_fused_run(self):
        """The served check uses the same detection. On the partial fusion it
        verifies the truth against the RULE (on 8b9ed96 it bound 0 to the fused
        centre, 2 px off, and the fused-centre mapping, 0.78 px off after the
        fit, verified inside the 1.8 px tolerance); a 2 px shift fails. On a
        whole-length two-stroke fusion it verifies nothing (unverified)."""
        labels = [AxisTick(f"{v:g}", v, _true_px(v)) for v in SIX]
        frame = SimpleNamespace(x0=LEFT, y0=_true_px(250.0), x1=RIGHT, y1=_true_px(0.0))
        truth = fit_axis_ticks(labels, "y", model="linear")
        fused_centre = fit_axis_ticks(
            [AxisTick(t.text, t.value, t.pixel - 2.0 * (t.value == 0.0)) for t in labels],
            "y", model="linear",
        )

        def check(image, axis):
            return check_served_on_grid_attempts(
                image, axis, labels, frame=frame, orientation="y",
                cross_span=(LEFT, RIGHT), name="y",
            )

        verified = check(_fused(0.9, 0.95), truth)
        self.assertEqual(verified.status, "verified", verified.reason)
        zero = [t for t in verified.ticks if t["value"] == 0.0][0]
        self.assertLess(abs(float(zero["line_px"]) - _true_px(0.0)), 0.5, zero)
        self.assertEqual(zero["line_source"], "recovered_rule")
        self.assertEqual(check(_fused(0.9, 0.95), _shifted(truth, -2.0)).status, "failed")
        self.assertEqual(check(_fused(0.9, 1.0), truth).status, "unverified")
        self.assertEqual(check(_fused(0.9, 1.0), fused_centre).status, "unverified")

    def test_bold_rules_are_kept(self):
        """Known-good: rules legitimately thicker than the chart's median rule
        are not re-measured or refused -- every labelled rule bold (4 px black
        among 2 px minors), a lone bold 0 rule, and a bold rule thinned by one
        anti-aliased row along part of its length."""
        bold_all = _panel(rule_grey=0)
        for value in SIX:
            bold_all[_rule_row(value) - 1:_rule_row(value) + 3, LEFT:RIGHT + 1] = 0
        lone = _panel(rule_grey=0)
        lone[_rule_row(0) - 1:_rule_row(0) + 3, LEFT:RIGHT + 1] = 0
        thinned = lone.copy()
        thinned[_rule_row(0) + 2, LEFT:LEFT + 60] = 255  # one edge row missing on 60 px
        for name, image in (("all bold", bold_all), ("lone bold", lone), ("thinned", thinned)):
            with self.subTest(name):
                anchored, _ = _anchor(image)
                self.assertLess(_max_error(anchored), 0.5)
                self.assertEqual({a.source for a in anchored.anchors}, {"gridline"})

    def test_taiyo_yuden_bold_decades_are_kept(self):
        """Real known-good control (see fixtures/gridline_fused_rule): bold
        3 px decade rules, locally one row thinner. Served on the vector rules
        as on 8b9ed96 (y 0.03 px); a draft that re-measured them served 0.32."""
        gray = np.asarray(Image.open(TY_FIXTURE).convert("L"))
        x0, y0, x1, y1 = TY_BOX
        frame = SimpleNamespace(x0=x0 - 0.5, y0=y0 - 0.5, x1=x1 - 0.5, y1=y1 - 0.5)
        for axis, span in (("x", (y0 - 0.5, y1 - 0.5)), ("y", (x0 - 0.5, x1 - 0.5))):
            with self.subTest(axis=axis):
                ticks = [AxisTick(f"{v:g}", v, lab - 0.5) for v, lab, _ in TY_TICKS[axis]]
                rule = {v: line - 0.5 for v, _, line in TY_TICKS[axis]}
                anchored, _ = anchor_axis_on_grid_attempts(
                    gray, fit_axis_ticks(ticks, axis, model="log10"), frame=frame,
                    orientation=axis, cross_span=span, name=axis,
                )
                self.assertEqual({a.source for a in anchored.anchors}, {"gridline"})
                for anchor in anchored.anchors:
                    self.assertLess(abs(anchor.served_px - rule[anchor.value]), 0.5, anchor)

    def test_guard_needs_peers_and_an_anomalous_width(self):
        """Unevaluable: fewer than 4 rules give no chart rule width, and a run
        only 1 px wider than the median is anti-aliasing: both untouched."""
        ink = np.zeros((100, 60), dtype=bool)
        grey = np.full((100, 60), 255, dtype=np.uint8)
        ink[:, 2:8] = True  # a 6 px run ...
        ink[90:, 2:6] = False  # ... where the "rule" (rows 6-7) shows alone on 10 positions
        grey[ink] = 0
        fused = ObservedLine(4.5, 6, "gridline")
        peers = [ObservedLine(20.0 + 10 * i, 2, "gridline") for i in range(3)]
        out = ga._guard_fused_runs([fused, *peers], ink, grey, 0)
        self.assertEqual([(line.center_px, line.width_px, line.source) for line in out][0], (6.5, 2, "recovered_rule"))
        self.assertEqual(ga._guard_fused_runs([fused, *peers[:2]], ink, grey, 0), [fused, *peers[:2]])
        wide3 = [ObservedLine(4.5, 3, "gridline"), *peers]
        self.assertEqual(ga._guard_fused_runs(wide3, ink, grey, 0), wide3)


if __name__ == "__main__":
    unittest.main()
