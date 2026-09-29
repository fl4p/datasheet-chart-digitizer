"""Review artifacts for generate_transfer_retrace.py (overlays, 5x crops, evidence).

Human overlay: the source at 2x with the SERVED curve points as discrete
markers in colours no source chart uses, crosshairs at every consumed tick
drawn at the served calibration (not at the detected line), and a caption with
the fail-closed signals.  5x crops of every tick crosshair, every curve
crossing and both served ends of every curve are separate artifacts.
"""

from __future__ import annotations

import subprocess
import tempfile
import textwrap
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from datasheet_chart_digitizer.capacitance_types import PlotBox

UP = 2  # overlay upscale
ZOOM = 5
CROSS = (0, 90, 255)
OLD = (230, 0, 0)
CONFLICT = (230, 0, 0)
UNSERVED = (150, 150, 150)
COLLAPSE = (255, 200, 0)
# extraction markers: none of these hues is used by the six source charts
PALETTE = [(255, 0, 255), (0, 0, 0), (0, 200, 200), (140, 70, 0)]


def font(size: int):
    for name in ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Helvetica.ttc"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


def save_webp(image: Image.Image, path: Path, lossless: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        png = Path(tmp) / "img.png"
        image.convert("RGB").save(png)
        quality = ["-lossless"] if lossless else ["-q", "80"]
        subprocess.run(["cwebp", "-quiet", *quality, str(png), "-o", str(path)], check=True)
    return path


def _text(draw, xy, text, size=13, fill=(0, 0, 0), anchor="la", halo=True):
    f = font(size)
    if halo:
        x, y = xy
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                draw.text((x + dx, y + dy), text, font=f, fill=(255, 255, 255), anchor=anchor)
    draw.text(xy, text, font=f, fill=fill, anchor=anchor)


def _crosshair(draw, x, y, arm, gap, color, width=1):
    draw.line((x - arm, y, x - gap, y), fill=color, width=width)
    draw.line((x + gap, y, x + arm, y), fill=color, width=width)
    draw.line((x, y - arm, x, y - gap), fill=color, width=width)
    draw.line((x, y + gap, x, y + arm), fill=color, width=width)


def curve_crossings(curves, frame: PlotBox, x_axis, y_axis) -> list[dict]:
    """Sign changes of the row-wise horizontal separation of every curve pair."""
    rows = {}
    for index, curve in enumerate(curves):
        by_row: dict[int, list[float]] = {}
        for x, y in curve["points_px"]:
            if y < frame.y1 - 2:
                by_row.setdefault(int(round(y)), []).append(x)
        rows[index] = {y: float(np.mean(v)) for y, v in by_row.items()}
    found = []
    for a in range(len(curves)):
        for b in range(a + 1, len(curves)):
            common = sorted(set(rows[a]) & set(rows[b]))
            previous = None
            for y in common:
                delta = rows[a][y] - rows[b][y]
                if abs(delta) < 0.5:
                    continue
                if previous is not None and np.sign(delta) != np.sign(previous[1]):
                    yc = (y + previous[0]) / 2
                    xc = (rows[a][y] + rows[b][y] + rows[a][previous[0]] + rows[b][previous[0]]) / 4
                    found.append(
                        {
                            "curves": [f"curve_{a + 1}", f"curve_{b + 1}"],
                            "temperatures_c": [curves[a]["temperature_c"], curves[b]["temperature_c"]],
                            "px": (xc, yc),
                            "Vgs_V": round(x_axis.value(xc), 4),
                            "Id_A": round(y_axis.value(yc), 3),
                        }
                    )
                previous = (y, delta)
    return found


def overlay(rgb, crop_box, frame, x_axis, y_axis, curves, rows, crossings, part, path: Path) -> Path:
    x0, y0, x1, y1 = crop_box
    base = Image.fromarray(rgb).crop(crop_box).resize(((x1 - x0) * UP, (y1 - y0) * UP), Image.Resampling.LANCZOS)
    caption_lines = _caption(part, x_axis, y_axis, curves, crossings)
    caption_h = 22 * len(caption_lines) + 16
    canvas = Image.new("RGB", (base.width, base.height + caption_h), "white")
    canvas.paste(base, (0, 0))
    draw = ImageDraw.Draw(canvas)

    def up(x, y):
        return ((x - x0) * UP, (y - y0) * UP)

    fx0, fy0 = up(frame.x0, frame.y0)
    fx1, fy1 = up(frame.x1, frame.y1)
    draw.rectangle((fx0, fy0, fx1, fy1), outline=(225, 0, 190), width=1)
    sx0, sx1 = (up(p, 0)[0] for p in x_axis.served_px)
    sy0, sy1 = (up(0, p)[1] for p in y_axis.served_px)
    if max(abs(sx0 - fx0), abs(sx1 - fx1), abs(sy0 - fy0), abs(sy1 - fy1)) > 2 * UP:
        for x in range(int(sx0), int(sx1), 12):
            draw.line((x, sy0, min(x + 6, sx1), sy0), fill=(0, 150, 0), width=2)
            draw.line((x, sy1, min(x + 6, sx1), sy1), fill=(0, 150, 0), width=2)
        for y in range(int(sy0), int(sy1), 12):
            draw.line((sx0, y, sx0, min(y + 6, sy1)), fill=(0, 150, 0), width=2)
            draw.line((sx1, y, sx1, min(y + 6, sy1)), fill=(0, 150, 0), width=2)
    for index, curve in enumerate(curves):
        color = PALETTE[index]
        mine = [r for r in rows if r["curve_id"] == f"curve_{index + 1}"]
        for k, r in enumerate(mine):
            if k % 2:
                continue
            x, y = up(*r["px"])
            if not r["served"]:
                draw.ellipse((x - 1.5, y - 1.5, x + 1.5, y + 1.5), outline=UNSERVED)
            elif r["collapsed"]:
                draw.ellipse((x - 2.2, y - 2.2, x + 2.2, y + 2.2), fill=COLLAPSE, outline=(0, 0, 0))
            else:
                draw.ellipse((x - 1.6, y - 1.6, x + 1.6, y + 1.6), fill=color)
        served = [r for r in mine if r["served"]]
        if served:
            end = min(served, key=lambda r: r["px"][1])
            ex, ey = up(*end["px"])
            right = ex > 0.75 * base.width
            _text(
                draw,
                (ex - 8 if right else ex + 8, max(4, ey + 6 + 16 * index)),
                f"curve_{index + 1}  Tj={curve['temperature_c']:g}°C",
                13,
                color,
                anchor="ra" if right else "la",
            )
    for axis, is_x in ((x_axis, True), (y_axis, False)):
        for tick in axis.inliers:
            p = axis.pixel(tick.value)
            cx, cy = up(p, frame.y1) if is_x else up(frame.x0, p)
            _crosshair(draw, cx, cy, 9, 0, CROSS, 1)
            if is_x:
                _text(draw, (cx + 3, cy - 18), f"{tick.value:g}", 11, CROSS)
            else:
                _text(draw, (cx + 5, cy - 14), f"{tick.value:g}", 11, CROSS)
        for conflict in axis.conflicts:
            lx, ly = (up(conflict.label_px, frame.y1 + 14) if is_x else up(frame.x0 - 14, conflict.label_px))
            draw.line((lx - 6, ly - 6, lx + 6, ly + 6), fill=CONFLICT, width=2)
            draw.line((lx - 6, ly + 6, lx + 6, ly - 6), fill=CONFLICT, width=2)
    for number, crossing in enumerate(crossings, 1):
        cx, cy = up(*crossing["px"])
        draw.ellipse((cx - 7, cy - 7, cx + 7, cy + 7), outline=(0, 0, 0), width=1)
        _text(draw, (cx + 8, cy - 16), f"X{number}", 11)
    y = base.height + 8
    for line in caption_lines:
        _text(draw, (10, y), line, 15, (40, 0, 60), halo=False)
        y += 22
    return save_webp(canvas, path)


def _fmt(value: float) -> str:
    return "0" if abs(value) < 5e-3 else f"{value:.4g}"


def _axis_line(name, axis):
    worst = max(abs(float(r["error_px"])) for r in axis.tick_hits() if r["consumed"])
    lo, hi = axis.frame_values
    slo, shi = axis.served_values
    text = (
        f"{name}: linear, frame {_fmt(lo)}..{_fmt(hi)}, SERVED {_fmt(slo)}..{_fmt(shi)}; "
        f"{len(axis.inliers)} ticks consumed, max |err| {worst:.2f}px"
    )
    if axis.conflicts:
        text += f"; NOT consumed (red X): {', '.join(c.text for c in axis.conflicts)}"
    return text


def _caption(part, x_axis, y_axis, curves, crossings):
    lines = [
        f"{part} — Id=f(Vgs) retrace 2026-09-29 — human_verified: false (pending Fab)",
        _axis_line("X Vgs [V]", x_axis),
        _axis_line("Y Id [A]", y_axis),
        "blue crosshairs = SERVED calibration at every consumed printed tick (on its own axis line)",
        "markers: " + "; ".join(f"curve_{i + 1} Tj={c['temperature_c']:g}°C" for i, c in enumerate(curves))
        + " — magenta/black/cyan in curve order; gold = within 1.5 px of another curve for >=12 rows (no per-T info)",
        f"curve crossings inspected: {len(crossings)} (X1.. ; 5x crops in crops/)",
    ]
    for reason in (*x_axis.unserved_reasons, *y_axis.unserved_reasons):
        lines.append("UNSERVED (grey hollow markers, green dashes = served region): " + reason)
    return lines


def _zoom(rgb, cx, cy, half, draw_fn, label, path: Path) -> Path:
    x0, y0 = int(np.floor(cx)) - half, int(np.floor(cy)) - half
    crop = Image.fromarray(rgb).crop((x0, y0, x0 + 2 * half, y0 + 2 * half))
    big = crop.resize((crop.width * ZOOM, crop.height * ZOOM), Image.Resampling.NEAREST)
    lines = _wrap(label, 64)
    canvas = Image.new("RGB", (max(big.width, 420), big.height + 18 * len(lines) + 8), "white")
    canvas.paste(big, (0, 0))
    draw = ImageDraw.Draw(canvas)
    draw_fn(draw, lambda x, y: ((x - x0) * ZOOM, (y - y0) * ZOOM))
    for k, line in enumerate(lines):
        _text(draw, (4, big.height + 4 + 18 * k), line, 12, (0, 0, 0), halo=False)
    return save_webp(canvas, path, lossless=True)


def _wrap(label: str, width: int) -> list[str]:
    return [piece for line in label.split("\n") for piece in (textwrap.wrap(line, width) or [""])]


def zoom_crops(rgb, frame, x_axis, y_axis, curves, crossings, rows, outdir: Path) -> list[dict]:
    """5x crops of every consumed tick crosshair, curve crossing and served curve end."""
    records = []
    for axis, is_x in ((x_axis, True), (y_axis, False)):
        for row in axis.tick_hits():
            if not row["consumed"]:
                continue
            p = axis.pixel(float(row["value"]))
            cx, cy = (p, frame.y1 + 0.5) if is_x else (frame.x0 + 0.5, p)

            def draw_fn(draw, map_, cx=cx, cy=cy):
                X, Y = map_(cx, cy)
                _crosshair(draw, X, Y, 60, 6, CROSS, 1)

            name = f"tick_{'x' if is_x else 'y'}_{row['text']}.webp"
            label = (
                f"{'Vgs' if is_x else 'Id'}={row['text']}: served {row['served_px']}px, "
                f"gridline {row['grid_px']}px, err {row['error_px']}px"
            )
            records.append({"crop": f"crops/{outdir.name}/{name}", "what": label})
            _zoom(rgb, cx, cy, 18, draw_fn, label, outdir / name)
    for number, crossing in enumerate(crossings, 1):
        cx, cy = crossing["px"]

        def draw_fn(draw, map_):
            for r in rows:
                if abs(r["px"][0] - cx) > 24 or abs(r["px"][1] - cy) > 24:
                    continue
                index = int(r["curve_id"].split("_")[1]) - 1
                X, Y = map_(*r["px"])
                if r["served"]:
                    draw.ellipse((X - 2.5, Y - 2.5, X + 2.5, Y + 2.5), fill=PALETTE[index])
                else:  # unserved: hollow, still in the curve's colour for identity
                    draw.ellipse((X - 3, Y - 3, X + 3, Y + 3), outline=PALETTE[index], width=2)

        name = f"crossing_X{number}.webp"
        label = f"X{number} {crossing['curves'][0]}/{crossing['curves'][1]} at Vgs {crossing['Vgs_V']} V, Id {crossing['Id_A']} A" + (
            "" if crossing.get("served", True) else " (NOT served region)"
        )
        records.append({"crop": f"crops/{outdir.name}/{name}", "what": label})
        _zoom(rgb, cx, cy, 22, draw_fn, label, outdir / name)
    for index, curve in enumerate(curves):
        served = [r for r in rows if r["curve_id"] == f"curve_{index + 1}" and r["served"]]
        if not served:
            continue
        rising = [r for r in served if r["px"][1] < frame.y1 - 3] or served
        for tag, r in (("low", min(rising, key=lambda r: -r["px"][1])), ("high", min(served, key=lambda r: r["px"][1]))):
            cx, cy = r["px"]

            def draw_fn(draw, map_, index=index):
                for q in served:
                    if abs(q["px"][0] - cx) <= 20 and abs(q["px"][1] - cy) <= 20:
                        X, Y = map_(*q["px"])
                        draw.ellipse((X - 2, Y - 2, X + 2, Y + 2), fill=PALETTE[index])

            name = f"end_curve{index + 1}_{tag}.webp"
            label = f"curve_{index + 1} Tj={curve['temperature_c']:g}C served {tag} end: Vgs {r['Vgs_V']:.3f} V, Id {r['Id_A']:.2f} A"
            records.append({"crop": f"crops/{outdir.name}/{name}", "what": label})
            _zoom(rgb, cx, cy, 20, draw_fn, label, outdir / name)
    return records


def refusal_evidence(rgb, crop_box, frame, x_axis, y_axis, path: Path, part: str) -> Path:
    """Gridlines (cyan) vs label glyph centres (red) with per-label offsets."""
    x0, y0, x1, y1 = crop_box
    base = Image.fromarray(rgb).crop(crop_box).resize(((x1 - x0) * UP, (y1 - y0) * UP), Image.Resampling.LANCZOS)
    lines = [f"{part} — REFUSED: axis cannot be anchored (fail closed, no points served)"]
    for name, axis in (("X", x_axis), ("Y", y_axis)):
        if hasattr(axis, "error"):
            lines.append(f"{name}: {axis.error}")
        else:
            lines.append(f"{name}: fitted, served {axis.served_values[0]:.4g}..{axis.served_values[1]:.4g}; " + "; ".join(axis.unserved_reasons))
    canvas = Image.new("RGB", (base.width, base.height + 22 * len(lines) + 16), "white")
    canvas.paste(base, (0, 0))
    draw = ImageDraw.Draw(canvas)
    for axis, is_x in ((x_axis, True), (y_axis, False)):
        grid = axis.grid if hasattr(axis, "grid") else [t.grid_px for t in axis.inliers] + [c.grid_px for c in axis.conflicts if c.grid_px]
        labels = axis.labels if hasattr(axis, "labels") else [(t.text, t.value, t.label_px) for t in axis.inliers] + [(c.text, c.value, c.label_px) for c in axis.conflicts]
        for g in grid:
            if is_x:
                X = (g - x0) * UP
                draw.line((X, (frame.y1 - y0) * UP, X, (frame.y1 - y0) * UP + 24), fill=(0, 190, 190), width=2)
            else:
                Y = (g - y0) * UP
                draw.line(((frame.x0 - x0) * UP - 60, Y, (frame.x0 - x0) * UP, Y), fill=(0, 190, 190), width=2)
        for text, _value, px in labels:
            nearest = min(grid, key=lambda g: abs(g - px))
            if is_x:
                X = (px - x0) * UP
                draw.line((X, (frame.y1 - y0) * UP + 26, X, (frame.y1 - y0) * UP + 60), fill=OLD, width=2)
            else:
                Y = (px - y0) * UP
                draw.line(((frame.x0 - x0) * UP - 110, Y, (frame.x0 - x0) * UP - 64, Y), fill=OLD, width=2)
                _text(draw, ((frame.x0 - x0) * UP - 8, Y - 6), f"'{text}' {px - nearest:+.1f}px", 12, OLD, anchor="ra")
    y = base.height + 8
    for line in lines:
        _text(draw, (10, y), line[:170], 14, (120, 0, 0), halo=False)
        y += 22
    return save_webp(canvas, path)


def defect_zoom(rgb, center, half, marks, label, path: Path, zoom: int = 4) -> Path:
    """Evidence crop: ``marks`` = [(x, y, colour, 'h'|'v'|'+')] in page px."""
    x0, y0 = int(center[0]) - half[0], int(center[1]) - half[1]
    crop = Image.fromarray(rgb).crop((x0, y0, x0 + 2 * half[0], y0 + 2 * half[1]))
    big = crop.resize((crop.width * zoom, crop.height * zoom), Image.Resampling.NEAREST)
    lines = _wrap(label, 90)
    canvas = Image.new("RGB", (max(big.width, 700), big.height + 22 * len(lines) + 10), "white")
    canvas.paste(big, (0, 0))
    draw = ImageDraw.Draw(canvas)
    for x, y, color, kind in marks:
        X, Y = (x - x0) * zoom, (y - y0) * zoom
        if kind == "h":
            for xx in range(0, big.width, 10):
                draw.line((xx, Y, xx + 5, Y), fill=color, width=2)
        elif kind == "v":
            for yy in range(0, big.height, 10):
                draw.line((X, yy, X, yy + 5), fill=color, width=2)
        else:
            _crosshair(draw, X, Y, 30, 5, color, 2)
    y = big.height + 5
    for line in lines:
        _text(draw, (6, y), line, 14, (0, 0, 0), halo=False)
        y += 22
    return save_webp(canvas, path, lossless=True)
