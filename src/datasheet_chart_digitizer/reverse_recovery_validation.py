"""Validation for digitized reverse-recovery charts (split from extraction).

Everything in here answers one question — "are the digitized values RIGHT?" —
and none of it extracts anything. Mirrors capacitance_validation.py's role for
the capacitance pipeline. Sources of truth, in independence order:

  1. spec-table anchors  — the datasheet's own electrical table quotes trr/Qrr
     typ at (IF, dI/dt); the digitized 25 C curve must reproduce them.
  2. cross-panel checks  — the same physical point is drawn in two charts
     (Qrr-vs-IF at fixed di/dt, and Qrr-vs-di/dt at fixed IF); both reads must
     agree.
  3. physics invariants  — Qrr/Irm/trr strictly increase 25 -> 125 C; a
     violated order means a swapped temperature label.
  4. axis-side units     — the quantity->y-axis mapping is checked against the
     rotated unit labels ("(nC)", "(A)", "(ns)") printed next to each axis.

Every panel ends up 'verified', 'FAIL', or explicitly 'unverified' (no anchor
applied) — absence of evidence is never reported as success.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # import only for type hints; runtime stays cycle-free
    from .reverse_recovery import Panel

ANCHOR_IF_RE = re.compile(r"(?i)I\s*F?\s*=\s*([\d.]+)\s*A")
ANCHOR_DIDT_RE = re.compile(r"(?i)dI\s*/\s*dt\s*=\s*([\d.]+)\s*A/[µμu]s")

SCALE_TOL = 0.25  # chart "typical" curves vs table typ; observed spread <= ~10%


def spec_anchors(doc) -> list[dict]:
    """Reverse-recovery rows of the electrical-characteristics table.

    AO layout: symbol column ('trr'/'Qrr') at the left, 'IF=..A, dI/dt=..A/µs'
    condition mid-row, then min/typ/max numeric cells. Parsed geometrically —
    the raw text stream interleaves neighboring rows. Typ = middle of 3 cells,
    the single cell when only one is given; ambiguous rows are skipped."""
    anchors = []
    for pno in range(len(doc)):
        words = doc[pno].get_text("words")
        # cheap page gate: only pages that talk about reverse recovery
        if not any("Recovery" in w[4] for w in words):
            continue
        left_x = min((w[0] for w in words if w[4] in ("trr", "Qrr")), default=None)
        if left_x is None:
            continue
        for sym in words:
            if sym[4] not in ("trr", "Qrr") or sym[0] > left_x + 8:
                continue
            # rows overlap in bbox; gate on vertical CENTER distance (row pitch
            # ~13 pt) so adjacent rows' cells don't bleed into the band
            sc = (sym[1] + sym[3]) / 2
            band = [w for w in words if abs((w[1] + w[3]) / 2 - sc) < 5.5]
            row_text = " ".join(w[4] for w in sorted(band, key=lambda w: w[0]))
            m_if = ANCHOR_IF_RE.search(row_text)
            m_dd = ANCHOR_DIDT_RE.search(row_text)
            unit = "ns" if sym[4] == "trr" else "nC"
            if not (m_if and m_dd and unit in row_text.split()):
                continue
            nums = [float(w[4]) for w in sorted(band, key=lambda w: w[0])
                    if w[0] > sym[2] + 20 and re.fullmatch(r"[\d.]+", w[4])]
            if len(nums) == 3:
                typ = nums[1]  # min / typ / max
            elif len(nums) == 1:
                typ = nums[0]
            else:
                continue  # ambiguous column layout: no anchor rather than a wrong one
            anchors.append(dict(quantity=sym[4],
                                IF=float(m_if.group(1)), didt=float(m_dd.group(1)),
                                typ=typ))
    return anchors


def interp_curve(values: list[tuple[float, float]], x: float) -> float | None:
    pts = sorted(values)
    if not pts or not pts[0][0] <= x <= pts[-1][0]:
        return None
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= x <= x1:
            return y0 + (y1 - y0) * (x - x0) / max(1e-12, x1 - x0)
    return None


def verify_scale(panels: list["Panel"], anchors: list[dict]) -> None:
    """Anchor every panel's values to independent numbers, fail loud.

    Two sources: (a) the spec table's trr/Qrr typ at (IF, di/dt) — matches the
    25 C curve of a panel whose fixed condition equals the anchor's other axis;
    (b) cross-panel consistency — an IF-panel evaluated at the didt-panel's
    fixed IF must equal the didt-panel evaluated at the IF-panel's fixed di/dt
    (same physical point drawn in two charts; the check is recorded on BOTH
    panels). A panel with no applicable check is marked 'unverified', never
    silently passed."""
    # (a) table anchors
    for p in panels:
        cond = p.conditions
        for a in anchors:
            for c in p.curves:
                if c.quantity != a["quantity"] or c.temp_c != 25.0 or not c.values:
                    continue
                if p.x_quantity == "didt":
                    if cond.get("IF") != a["IF"]:
                        continue
                    got = interp_curve(c.values, a["didt"])
                elif p.x_quantity == "IF":
                    if cond.get("didt") != a["didt"]:
                        continue
                    got = interp_curve(c.values, a["IF"])
                else:
                    continue
                if got is None:
                    continue  # anchor outside the drawn span
                p.checks.append(dict(
                    kind="table", quantity=c.quantity,
                    at=f"IF={a['IF']:g}A,di/dt={a['didt']:g}A/us",
                    anchor=a["typ"], chart=round(got, 2),
                    err=round(got / a["typ"] - 1, 3)))
    # (b) cross-panel: same quantity+temp in an IF-panel and a didt-panel
    for p in panels:
        if p.x_quantity != "IF" or "didt" not in p.conditions:
            continue
        for q in panels:
            if q.x_quantity != "didt" or "IF" not in q.conditions:
                continue
            for c in p.curves:
                cc = next((k for k in q.curves if k.quantity == c.quantity
                           and k.temp_c == c.temp_c and k.values), None)
                if cc is None or not c.values:
                    continue
                v_if = interp_curve(c.values, q.conditions["IF"])
                v_dd = interp_curve(cc.values, p.conditions["didt"])
                if v_if is None or v_dd is None:
                    continue
                check = dict(
                    kind="cross-panel", quantity=c.quantity, temp_c=c.temp_c,
                    at=f"IF={q.conditions['IF']:g}A,di/dt={p.conditions['didt']:g}A/us",
                    fig_if=round(v_if, 2), fig_didt=round(v_dd, 2),
                    err=round(v_if / v_dd - 1, 3))
                p.checks.append(check)
                q.checks.append(dict(check))


def integrity_warnings(panel: "Panel") -> None:
    """Cheap invariants that catch silent mis-extraction (review findings)."""
    if len(panel.curves) != 4:
        panel.warnings.append(
            f"expected 4 curves (2 quantities x 2 temps), got {len(panel.curves)}")
    # physics: Qrr/Irm/trr strictly increase with Tj — a violated order means a
    # swapped or misassigned temperature label (S is ~temp-flat, excluded)
    for qty in ("Qrr", "Irm", "trr"):
        pair = {c.temp_c: c for c in panel.curves if c.quantity == qty and c.values}
        if 25.0 in pair and 125.0 in pair:
            hot = {round(x, 1): y for x, y in pair[125.0].values}
            gaps = [y_hot - y_cold
                    for x, y_cold in pair[25.0].values
                    for y_hot in (hot.get(round(x, 1)),) if y_hot is not None]
            if gaps and sum(1 for g in gaps if g <= 0) > len(gaps) * 0.2:
                panel.warnings.append(
                    f"temp order violates physics for {qty}: "
                    f"{qty}(125C) not above {qty}(25C) — check label assignment")


def verify_axis_sides(panel: "Panel", words, quantity_unit: dict[str, str]) -> None:
    """The Qrr/trr->left, Irm/S->right mapping is an AO-layout assumption;
    verify it against the rotated y-axis unit labels when they are present."""
    units = {"left": set(), "right": set()}
    for w in words:
        t = w[4].strip()
        if t not in ("(nC)", "(A)", "(ns)"):
            continue
        cy = (w[1] + w[3]) / 2
        if not panel.plot.y0 - 6 < cy < panel.plot.y1 + 6:
            continue
        if -60 < w[2] - panel.plot.x0 < -6:
            units["left"].add(t.strip("()"))
        elif 6 < w[0] - panel.plot.x1 < 60:
            units["right"].add(t.strip("()"))
    for c in panel.curves:
        expect = quantity_unit.get(c.quantity)
        side_units = units.get(c.axis) or set()
        if expect and side_units and expect not in side_units:
            panel.warnings.append(
                f"{c.quantity} read off the {c.axis} axis, but that axis is "
                f"labeled {sorted(side_units)} not ({expect}) — values suspect")


def scale_verdict(checks: list[dict], warnings: list[str]) -> str:
    """'verified' / 'FAIL' / 'unverified' — no applicable check is stated
    explicitly, never passed off as success."""
    if any(abs(k["err"]) > SCALE_TOL for k in checks) or any(
            "violates physics" in w or "values suspect" in w for w in warnings):
        return "FAIL"
    return "verified" if checks else "unverified"
