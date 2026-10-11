"""Refuse a review packet that would show Fab a chart he cannot review in one go.

usage: review_packet_lint.py ROWS.jsonl [ROWS.jsonl ...] [--html PACKET.html ...]

Every card must carry BOTH the digitized axis and the digitized curves (Fab,
2026-10-11: "NEVER EVER SHOW ME A CHART WITH JUST THE AXIS OR JUST THE CURVE
DIGITIZED"; 2026-10-05: "never ever show me charts for review without tick
labels"). Checks, per row:

  axis    axis_ticks with a plot box and >= 2 labelled ticks on x AND on y
  curves  >= 1 curve with >= 2 points, most of them inside the plot box
  served  no curve labelled withheld/refused/unserved, none drawn grey as a
          placeholder (a withheld chart is a README line, not a card)
  count   every row declares expected_curve_count (the curves PRINTED on the
          chart); it must draw that many, or say "<n> of <m> curves served"
          in its chip (a 3-VDD chart drawn with one curve is half a review)
  dupes   one card per chart (same source_pdf and pdf_page, plot boxes
          overlapping > 50 %): never main and branch as two near-identical
          cards -- put the before-state in the chip

With --html, also: the rendered packet has no "no axis ticks" chip and as many
cards as rows. Exit 0 = clean; exit 1 = refused (every problem printed). It
cannot see whether a curve sits ON the printed curve: screenshot every card
and look, as before.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_WITHHELD_RE = re.compile(r"withheld|refused|unserved|not served", re.IGNORECASE)
_GREY = {"#888888", "#888", "#999999", "#999", "#aaaaaa", "#aaa", "grey", "gray"}
_PARTIAL_RE = re.compile(r"\b(\d+)\s+of\s+(\d+)\s+curves?\s+served\b", re.IGNORECASE)


def _points(curve) -> list:
    return curve.get("points") or curve.get("points_px") or []


def lint_row(row: dict) -> list[str]:
    name = f"{row.get('review_id') or row.get('part')}"
    problems: list[str] = []
    ticks = row.get("axis_ticks") or {}
    box = ticks.get("plot_box_px")
    for axis in ("x", "y"):
        labelled = [t for t in ticks.get(axis) or [] if t.get("pixel") is not None and str(t.get("label", "")).strip()]
        if len(labelled) < 2:
            problems.append(f"{name}: {len(labelled)} labelled {axis} ticks (need >= 2)")
    if not box or len(box) != 4:
        problems.append(f"{name}: no plot_box_px")
    curves = row.get("curves") or []
    drawn = [c for c in curves if len(_points(c)) >= 2]
    if not drawn:
        problems.append(f"{name}: no digitized curve with >= 2 points")
    for c in curves:
        label = f"{c.get('label', '')} {c.get('short_label', '')}"
        if _WITHHELD_RE.search(label):
            problems.append(f"{name}: curve '{c.get('label')}' is withheld -- not reviewable")
        if str(c.get("color", "")).lower() in _GREY:
            problems.append(f"{name}: curve '{c.get('label')}' drawn grey (placeholder)")
    if box and len(box) == 4 and drawn:
        x0, y0, x1, y1 = box
        mx, my = 0.02 * (x1 - x0), 0.02 * (y1 - y0)
        for c in drawn:
            pts = _points(c)
            inside = sum(x0 - mx <= p[0] <= x1 + mx and y0 - my <= p[1] <= y1 + my for p in pts)
            if inside < 0.8 * len(pts):
                problems.append(f"{name}: curve '{c.get('label')}' has {inside}/{len(pts)} points inside the plot box")
    expected = row.get("expected_curve_count")
    if expected is None:
        problems.append(f"{name}: no expected_curve_count (how many curves the chart prints)")
    elif len(drawn) < int(expected):
        m = _PARTIAL_RE.search(row.get("chip") or "")
        if not (m and int(m.group(1)) == len(drawn) and int(m.group(2)) == int(expected)):
            problems.append(
                f"{name}: {len(drawn)} of {expected} curves drawn and the chip does not say "
                f"'{len(drawn)} of {expected} curves served'"
            )
    return problems


def _overlap(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    small = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return ix * iy / small if small > 0 else 0.0


def lint_rows(rows: list[dict]) -> list[str]:
    problems: list[str] = []
    seen: list[tuple] = []
    for row in rows:
        problems.extend(lint_row(row))
        box = (row.get("axis_ticks") or {}).get("plot_box_px")
        page = (row.get("source_pdf"), row.get("pdf_page"))
        for other_page, other_box, other_id in seen:
            if page == other_page and (box is None or other_box is None or _overlap(box, other_box) > 0.5):
                problems.append(f"{row.get('review_id')}: same chart as {other_id} -- one card per chart")
                break
        seen.append((page, box, row.get("review_id")))
    return problems


def lint_html(path: Path, n_rows: int) -> list[str]:
    text = path.read_text(errors="replace")
    problems = []
    if "no axis ticks" in text:
        problems.append(f"{path.name}: {text.count('no axis ticks')} 'no axis ticks' chip(s)")
    cards = len(re.findall(r'<article[^>]*class="[^"]*\bcard\b', text))
    if cards != n_rows:
        problems.append(f"{path.name}: {cards} cards for {n_rows} rows")
    return problems


def main(argv: list[str]) -> int:
    rows_paths, html_paths, mode = [], [], "rows"
    for arg in argv:
        if arg == "--html":
            mode = "html"
            continue
        (html_paths if mode == "html" else rows_paths).append(Path(arg))
    if not rows_paths:
        print(__doc__)
        return 2
    rows = [json.loads(line) for p in rows_paths for line in p.read_text().splitlines() if line.strip()]
    problems = lint_rows(rows)
    for html in html_paths:
        problems.extend(lint_html(html, len(rows)))
    for p in problems:
        print("REFUSED", p)
    print(f"{len(rows)} rows, {len(problems)} problems: {'CLEAN' if not problems else 'DO NOT SHOW THIS PACKET'}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
