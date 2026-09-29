"""Review artifacts for generate_capacitance_retrace.py.

Human overlay: the PDF region rendered at ``UP`` x the tracking resolution with
enough margin that the printed tick numerals (exponents included) and both axis
titles are on the image; the SERVED points as discrete markers with a white
halo so a trace that runs along the frame or a gridline cannot hide in it; a
legend; blue crosshairs at every consumed tick drawn from the SERVED
calibration; and a caption with the fail-closed signals.  5x crops of every
tick crosshair, every Ciss/Coss crossing and both ends of every served curve
are separate artifacts.

Generic drawing helpers (webp writer, fonts, crosshair, 5x zoom, evidence zoom)
are shared with the transfer retrace: ``tools/transfer_review/retrace_render``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "transfer_review"))
import retrace_render as base  # noqa: E402

from datasheet_chart_digitizer.gridline_anchor import served_pixel  # noqa: E402

CROSS = base.CROSS
ZOOM = base.ZOOM
COLORS = {"Ciss": (225, 0, 225), "Coss": (0, 150, 255), "Crss": (255, 110, 0)}
FRAME = (225, 0, 190)
OLD = {"Ciss": (255, 40, 40), "Coss": (20, 90, 255), "Crss": (30, 180, 30)}
STATUS_TEXT = {
    "ok": "filled = served, isolated stroke",
    "touching": "ring = served, two strokes touching (centre half a stroke inside its own edge)",
    "shared": "gold = served, ONE stroke shared by two curves",
    "sub_resolution": "diamond = vector path < 4 px from the 0 pF rail (raster cannot verify)",
}


def thin(rows, spacing_px):
    """Every served point would bury the source stroke; keep one per ``spacing_px`` of path."""
    out, last = [], {}
    for r in rows:
        if r["status"] == "unresolved":
            continue
        prev = last.get(r["curve"])
        if prev is None or (r["px"][0] - prev[0]) ** 2 + (r["px"][1] - prev[1]) ** 2 >= spacing_px ** 2:
            out.append(r)
            last[r["curve"]] = r["px"]
    return out


def marker(draw, x, y, color, status, r=2.0):
    """A served point with a white halo (visible even on a black frame rail)."""
    if status == "shared":
        draw.ellipse((x - r - 1, y - r - 1, x + r + 1, y + r + 1), fill=(255, 255, 255))
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(235, 190, 0), outline=color)
    elif status == "touching":
        draw.ellipse((x - r - 1, y - r - 1, x + r + 1, y + r + 1), outline=(255, 255, 255), width=1)
        draw.ellipse((x - r, y - r, x + r, y + r), outline=color, width=2)
    elif status == "sub_resolution":
        pts = [(x, y - r - 1.5), (x + r + 1.5, y), (x, y + r + 1.5), (x - r - 1.5, y)]
        draw.polygon(pts, outline=color, fill=(255, 255, 255))
    else:
        draw.ellipse((x - r - 1, y - r - 1, x + r + 1, y + r + 1), fill=(255, 255, 255))
        draw.ellipse((x - r, y - r, x + r, y + r), fill=color)


def overlay(big_rgb, up, crop_box, frame, axes, rows, crossings, caption_lines, path: Path) -> Path:
    """``big_rgb`` is the page rendered at ``up`` x; everything else is in 1x px."""
    x0, y0, x1, y1 = crop_box
    base_img = Image.fromarray(big_rgb).crop((round(x0 * up), round(y0 * up), round(x1 * up), round(y1 * up)))
    legend = [(name, "ok", f"{name} retrace") for name in COLORS] + [
        ((80, 80, 80), status, STATUS_TEXT[status]) for status in ("ok", "touching", "shared", "sub_resolution")
    ]
    width_chars = max(60, int(base_img.width / 7.4))
    cap = [piece for line in caption_lines for piece in base._wrap(line, width_chars)]
    canvas = Image.new("RGB", (base_img.width, base_img.height + 20 * len(cap) + 18 * 4 + 24), "white")
    canvas.paste(base_img, (0, 0))
    draw = ImageDraw.Draw(canvas)

    def U(x, y):  # 1x pixel-index -> index coordinate of the same point in the up-x render
        return ((x - x0) * up + (up - 1) / 2.0, (y - y0) * up + (up - 1) / 2.0)

    fx0, fy0 = U(frame.x0, frame.y0)
    fx1, fy1 = U(frame.x1, frame.y1)
    draw.rectangle((fx0, fy0, fx1, fy1), outline=FRAME, width=1)
    for row in thin(rows, 6.0 / up):
        X, Y = U(*row["px"])
        marker(draw, X, Y, COLORS[row["curve"]], row["status"])
    # curve names ON each served curve, staggered along x so they never overlap
    for index, name in enumerate(COLORS):
        mine = [r for r in rows if r["curve"] == name and r["status"] != "unresolved"]
        if not mine:
            continue
        target = frame.x0 + (0.62 + 0.12 * index) * (frame.x1 - frame.x0)
        at = min(mine, key=lambda r: abs(r["px"][0] - target))
        X, Y = U(*at["px"])
        base._text(draw, (X, Y - 8), f"{name} (retrace)", 13, COLORS[name], anchor="ld")
    # crosshairs from the SERVED calibration at each consumed tick, on the frame edge
    for name, (axis, payload) in axes.items():
        for tick in payload["ticks"]:
            p = served_pixel(axis, tick["value"])
            if name == "x":
                X, Y = U(p, frame.y1)
                base._crosshair(draw, X, Y, 10, 0, CROSS, 1)
                base._text(draw, (X + 3, Y - 18), f"{tick['value']:g}", 11, CROSS)
            else:
                X, Y = U(frame.x0, p)
                base._crosshair(draw, X, Y, 10, 0, CROSS, 1)
                base._text(draw, (X + 5, Y - 14), f"{tick['value']:g}", 11, CROSS)
    for number, crossing in enumerate(crossings, 1):
        X, Y = U(*crossing["px"])
        draw.ellipse((X - 8, Y - 8, X + 8, Y + 8), outline=(0, 0, 0), width=1)
        base._text(draw, (X + 9, Y - 20), f"X{number}", 12)
    y = base_img.height + 8
    for line in cap:
        base._text(draw, (10, y), line, 14, (40, 0, 60), halo=False)
        y += 20
    # legend below the chart so it can never cover a curve
    y += 4
    for k, (color, status, text) in enumerate(legend):
        col, row = divmod(k, 4)
        lx, ly = 16 + col * (canvas.width // 2), y + 18 * row
        marker(draw, lx, ly + 7, COLORS.get(color, color) if isinstance(color, str) else color, status)
        base._text(draw, (lx + 10, ly), text, 12, (40, 40, 40), halo=False)
    return base.save_webp(canvas, path)


def zoom_crops(rgb, frame, axes, rows, crossings, outdir: Path, stem: str) -> list[dict]:
    """5x crops of every consumed tick crosshair, curve crossing and curve end."""
    records = []
    for name, (axis, payload) in axes.items():
        for tick in payload["ticks"]:
            p = served_pixel(axis, tick["value"])
            cx, cy = (p, frame.y1) if name == "x" else (frame.x0, p)

            def draw_fn(draw, map_, cx=cx, cy=cy):
                X, Y = map_(cx + 0.5, cy + 0.5)
                base._crosshair(draw, X, Y, 60, 6, CROSS, 1)

            label = (
                f"{name}={tick['text']} ({tick['value']:g}): served {tick['served_px']:.2f}px, "
                f"gridline {tick['line_px']:.2f}px, err {tick['served_error_px']:+.2f}px"
            )
            fname = f"tick_{name}_{tick['text'].replace('^', 'e')}.webp"
            records.append({"crop": f"crops/{stem}/{fname}", "what": label})
            base._zoom(rgb, cx + 0.5, cy + 0.5, 18, draw_fn, label, outdir / fname)
    for number, crossing in enumerate(crossings, 1):
        cx, cy = crossing["px"]

        def draw_fn(draw, map_):
            for r in rows:
                if r["status"] == "unresolved" or abs(r["px"][0] - cx) > 26 or abs(r["px"][1] - cy) > 26:
                    continue
                X, Y = map_(r["px"][0] + 0.5, r["px"][1] + 0.5)
                marker(draw, X, Y, COLORS[r["curve"]], r["status"], r=2.5)

        fname = f"crossing_X{number}.webp"
        label = (
            f"X{number} {crossing['curves'][0]}/{crossing['curves'][1]} at VDS {crossing['vds_V']:.4g} V, "
            f"C {crossing['cap']:.4g} (approach on both sides shown)"
        )
        records.append({"crop": f"crops/{stem}/{fname}", "what": label})
        base._zoom(rgb, cx + 0.5, cy + 0.5, 26, draw_fn, label, outdir / fname)
    for name in COLORS:
        mine = [r for r in rows if r["curve"] == name and r["status"] != "unresolved"]
        if not mine:
            continue
        for tag, r in (("low", min(mine, key=lambda q: q["px"][0])), ("high", max(mine, key=lambda q: q["px"][0]))):
            cx, cy = r["px"]

            def draw_fn(draw, map_, name=name):
                for q in rows:
                    if q["status"] != "unresolved" and abs(q["px"][0] - cx) <= 20 and abs(q["px"][1] - cy) <= 20:
                        X, Y = map_(q["px"][0] + 0.5, q["px"][1] + 0.5)
                        marker(draw, X, Y, COLORS[q["curve"]], q["status"], r=2.5)

            fname = f"end_{name}_{tag}.webp"
            label = f"{name} served {tag}-VDS end: VDS {r['vds_V']:.4g} V, C {r['cap']:.4g} ({r['status']})"
            records.append({"crop": f"crops/{stem}/{fname}", "what": label})
            base._zoom(rgb, cx + 0.5, cy + 0.5, 20, draw_fn, label, outdir / fname)
    return records


def old_overlay_evidence(big_rgb, up, crop_box, old_traces, old_box, frame, caption, path: Path) -> Path:
    """The OLD reviewed traces (their own colours) and plot box on the page region."""
    x0, y0, x1, y1 = crop_box
    img = Image.fromarray(big_rgb).crop((round(x0 * up), round(y0 * up), round(x1 * up), round(y1 * up)))
    lines = base._wrap(caption, 150)
    canvas = Image.new("RGB", (img.width, img.height + 20 * len(lines) + 12), "white")
    canvas.paste(img, (0, 0))
    draw = ImageDraw.Draw(canvas)

    def c(v, origin):  # 1x pixel-index -> up-x pixel-index
        return (v - origin) * up + (up - 1) / 2.0

    if old_box is not None:
        bx0, by0, bx1, by1 = old_box
        draw.rectangle((c(bx0, x0), c(by0, y0), c(bx1, x0), c(by1, y0)), outline=(255, 180, 0), width=2)
    for name, pts in old_traces.items():
        for x, y in pts:
            X, Y = c(x, x0), c(y, y0)
            draw.ellipse((X - 1.8, Y - 1.8, X + 1.8, Y + 1.8), fill=OLD[name])
    fx0, fy0, fx1, fy1 = (c(frame.x0, x0), c(frame.y0, y0), c(frame.x1, x0), c(frame.y1, y0))
    for xx in np.arange(fx0, fx1, 14):
        draw.line((xx, fy0, min(xx + 7, fx1), fy0), fill=FRAME, width=1)
        draw.line((xx, fy1, min(xx + 7, fx1), fy1), fill=FRAME, width=1)
    y = img.height + 6
    for line in lines:
        base._text(draw, (8, y), line, 14, (120, 0, 0), halo=False)
        y += 20
    return base.save_webp(canvas, path)


def defect_zoom(rgb, crop_box, center, half, old_traces, new_rows, label, path: Path, zoom: int = 4) -> Path:
    """Old (dsdig palette) vs new (retrace palette) points around one defect.

    Left: the whole chart region WITH its printed tick numerals and axis
    titles, the zoom window outlined; right: the 4x zoom of that window.
    """
    x0, y0 = int(center[0]) - half[0], int(center[1]) - half[1]
    crop = Image.fromarray(rgb).crop((x0, y0, x0 + 2 * half[0], y0 + 2 * half[1]))
    big = crop.resize((crop.width * zoom, crop.height * zoom), Image.Resampling.NEAREST)
    context = Image.fromarray(rgb).crop(crop_box)
    ctx_draw = ImageDraw.Draw(context)
    ctx_draw.rectangle((x0 - crop_box[0], y0 - crop_box[1], x0 + 2 * half[0] - crop_box[0], y0 + 2 * half[1] - crop_box[1]),
                       outline=(230, 0, 0), width=2)
    for name, pts in old_traces.items():
        for x, y in pts[::2]:
            X, Y = x - crop_box[0], y - crop_box[1]
            ctx_draw.ellipse((X - 1, Y - 1, X + 1, Y + 1), fill=OLD[name])
    lines = base._wrap(label, 150)
    width = context.width + 12 + big.width
    height = max(context.height, big.height) + 20 * len(lines) + 10
    canvas = Image.new("RGB", (width, height), "white")
    canvas.paste(context, (0, 0))
    ox = context.width + 12
    canvas.paste(big, (ox, 0))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((ox, 0, ox + big.width - 1, big.height - 1), outline=(230, 0, 0), width=2)
    for name, pts in old_traces.items():
        for x, y in pts:
            X, Y = (x - x0 + 0.5) * zoom, (y - y0 + 0.5) * zoom
            if 0 <= X < big.width and 0 <= Y < big.height:
                draw.rectangle((ox + X - 2, Y - 2, ox + X + 2, Y + 2), outline=OLD[name])
    for r in new_rows:
        if r["status"] == "unresolved":
            continue
        X, Y = (r["px"][0] - x0 + 0.5) * zoom, (r["px"][1] - y0 + 0.5) * zoom
        if 0 <= X < big.width and 0 <= Y < big.height:
            marker(draw, ox + X, Y, COLORS[r["curve"]], r["status"], r=2.5)
    y = max(context.height, big.height) + 5
    for line in lines:
        base._text(draw, (6, y), line, 14, (0, 0, 0), halo=False)
        y += 20
    return base.save_webp(canvas, path, lossless=True)
