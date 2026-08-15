"""An unresolvable trace with NO spec-table anchor must be recorded, not crashed on.

`unresolved_anchor_traces` decides resolvability on the served curve's own floor when no anchor
exists, so it can name a trace absent from `anchors`. The caller indexed `anchors[name]` and
raised a bare KeyError, which surfaced as `ERROR: 'Crss'` and dropped the entire panel: the
branch that exists to RECORD a downgrade instead destroyed the record. Seen on EPC2361's
linear-scale capacitance panel, whose Crss is crushed onto the axis.
"""

from __future__ import annotations

import pytest

from datasheet_chart_digitizer.capacitance_validation import (
    TINY_CRSS_ANCHOR_PF,
    anchor_resolution_reason,
    unresolved_anchor_traces,
)


def test_curve_floor_alone_can_flag_a_trace_with_no_anchor():
    unresolved = unresolved_anchor_traces(
        False, 9.78, {"Ciss": None, "Coss": None, "Crss": None},
        {"Ciss": 3800.0, "Coss": 900.0, "Crss": 0.4},
    )

    assert "Crss" in unresolved, "an unresolvable anchor-less trace must still be flagged"
    assert "Ciss" not in unresolved and "Coss" not in unresolved


def test_reason_renders_without_an_anchor_and_names_what_decided():
    reason = anchor_resolution_reason("Crss", 0.04, 9.78, None, 0.4)

    assert "curve_floor" in reason
    assert "no spec-table anchor" in reason
    assert "9.78 pF/px" in reason
    assert "None" not in reason, "the message must not leak a None into operator-facing text"


def test_anchorless_is_never_export_exempt():
    """The exemption exists because the export applies an offset DERIVED FROM the anchor.
    With no anchor there is no such correction, so treating absence as exemption would
    silence the downgrade for exactly the parts with the least evidence."""
    exempt_marker = "_offset_corrected_at_export"

    with_anchor = anchor_resolution_reason(
        "Crss", 0.5, 9.78, TINY_CRSS_ANCHOR_PF / 2, 0.4)
    without_anchor = anchor_resolution_reason("Crss", 0.5, 9.78, None, 0.4)

    assert exempt_marker in with_anchor, "the exemption must still apply when an anchor exists"
    assert exempt_marker not in without_anchor


def test_anchored_message_is_unchanged():
    """The anchor-less branch must not perturb the existing, anchored wording."""
    reason = anchor_resolution_reason("Crss", 0.84, 9.78, 12.0, None)

    assert reason.startswith("Crss_anchor_below_axis_resolution")
    assert "anchor 12 pF" in reason


@pytest.mark.parametrize(
    "y_log, y_scale",
    [
        (True, 9.78),    # log axis: every decade gets the same pixel budget
        (None, None),    # scale unknown: nothing to measure resolvability against
        (False, None),
        (False, 0.0),
    ],
)
def test_no_linear_scale_reports_no_resolution_failure(y_log, y_scale):
    """Absence of a usable linear scale is absence of THIS failure mode, not a clean bill of
    health -- pinned so the empty return is never repurposed as evidence the trace is fine."""
    assert unresolved_anchor_traces(y_log, y_scale, {"Crss": 0.4}, {"Crss": 0.4}) == {}


def test_unknown_y_log_with_a_real_linear_scale_is_still_checked():
    """y_log=None is not a reason to skip: the scale is what decides, and skipping on a
    missing flag would let an unresolvable trace through unrecorded."""
    assert "Crss" in unresolved_anchor_traces(None, 9.78, {"Crss": 0.4}, {"Crss": 0.4})
