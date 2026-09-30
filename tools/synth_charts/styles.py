"""Style and difficulty factors, sampled per case and tier, recorded verbatim in the case.

Every factor that influences a rendering is drawn here (never ad hoc in the renderer), so a
result can be broken down by any of them. ``sample_style`` returns a flat dict of JSON-safe
values.
"""
from __future__ import annotations

from matplotlib import font_manager

TIERS = ("easy", "medium", "hard", "adversarial")

_FONTS = ["DejaVu Sans", "DejaVu Serif", "Arial", "Times New Roman", "Verdana", "Georgia", "Arial Narrow"]
SERIF = {"DejaVu Serif", "Times New Roman", "Georgia"}


def available_fonts():
    have = {f.name for f in font_manager.fontManager.ttflist if f.fname.lower().endswith(".ttf")}
    return [f for f in _FONTS if f in have] or ["DejaVu Sans"]


PALETTES = {
    "tab": ["#1f77b4", "#d62728", "#2ca02c", "#ff7f0e", "#9467bd", "#8c564b", "#17becf"],
    "office": ["#4472C4", "#ED7D31", "#A5A5A5", "#FFC000", "#5B9BD5", "#70AD47", "#264478"],
    "primary": ["#0000ff", "#ff0000", "#008000", "#ff00ff", "#00a0a0", "#808000", "#000000"],
    "muted": ["#3b5b92", "#b0413e", "#5a8f29", "#c28f2c", "#6a4c93", "#2b7a78", "#555555"],
}
DASHES = ["solid", (0, (5, 2)), (0, (1.2, 1.2)), (0, (6, 2, 1.5, 2)), (0, (3, 1.5)), (0, (8, 2, 1.5, 2, 1.5, 2))]


def _p(rng, table: dict):
    keys = list(table)
    w = [table[k] for k in keys]
    s = sum(w)
    return keys[int(rng.choice(len(keys), p=[x / s for x in w]))]


def _u(rng, a, b, nd=3):
    return round(float(rng.uniform(a, b)), nd)


def sample_style(rng, tier: str, spec: dict) -> dict:
    """Style factors for one chart. ``spec`` is consulted only for its axis scales / curve count."""
    T = TIERS.index(tier)  # 0..3
    logx, logy = spec["x"]["scale"].startswith("log"), spec["y"]["scale"].startswith("log")
    n = len(spec["curves"])
    s = {"tier": tier}
    fonts = available_fonts()
    s["font"] = fonts[int(rng.integers(len(fonts)))]
    s["font_serif"] = s["font"] in SERIF
    s["font_pt"] = _u(rng, 7.5, 10) if T == 0 else _u(rng, 5.5, 10)
    s["tick_font_pt"] = round(s["font_pt"] * _u(rng, 0.8, 1.0), 2)

    s["grid_major"] = _p(rng, {"solid": 6, "dotted": 2 + T, "dashed": T, "none": 0 if T == 0 else 1})
    s["grid_minor"] = _p(rng, {"none": 4 - T, "sparse": 2, "dense": 1 + 2 * T})
    s["minor_per_decade"] = 9 if s["grid_minor"] == "dense" else (4 if s["grid_minor"] == "sparse" else 0)
    s["minor_per_major"] = {"none": 0, "sparse": int(rng.choice([2, 4])), "dense": int(rng.choice([5, 10]))}[s["grid_minor"]]
    s["grid_grey"] = _u(rng, 0.45, 0.85, 2)
    s["grid_lw"] = _u(rng, 0.2, 0.8, 2)
    s["tick_dir"] = _p(rng, {"in": 3, "out": 3, "inout": 1, "none": 1 + T})
    s["frame"] = _p(rng, {"box": 7, "open": T, "box_thick": 1})
    s["frame_lw"] = _u(rng, 0.4, 1.4, 2) if s["frame"] != "box_thick" else _u(rng, 1.4, 2.2, 2)

    logfmt = {"plain": 3, "pow10": 3, "pow10_uni": 1, "sci_e": 1 + T, "sci_e2": T, "si": 2, "pow10_all": 1}
    linfmt = {"plain": 8, "comma": 1 + T, "si": 1}
    if T == 0:  # easy = clean vendor style: plain / 10^n labels only
        logfmt, linfmt = {"plain": 1, "pow10": 1, "pow10_all": 1}, {"plain": 1}
    s["x_tick_format"] = _p(rng, logfmt if logx else linfmt)
    s["y_tick_format"] = _p(rng, logfmt if logy else linfmt)
    s["label_every"] = 1 if T < 2 or rng.random() < 0.6 else 2
    # label-centre calibration trap: every label of an axis displaced from its gridline
    s["label_offset_pt"] = [0.0, 0.0]
    if T >= 2 and rng.random() < (0.4 if T == 2 else 0.6):
        k = int(rng.integers(2))
        s["label_offset_pt"][k] = _u(rng, 0.8, 3.0, 2) * (1 if rng.random() < 0.5 else -1)
    s["label_jitter_pt"] = _u(rng, 1.0, 4.0, 2) if (T == 3 and rng.random() < 0.3) else 0.0
    s["drop_edge_label"] = _p(rng, {"none": 8 - 2 * T, "first": T, "last": T})
    s["frame_past_last_label"] = bool(T >= 2 and rng.random() < 0.35)
    s["contradicting_label"] = bool(T == 3 and rng.random() < 0.15)

    s["axis_title_pos"] = _p(rng, {"center": 6, "end": 2 + T, "top": 1 + T})
    s["caption"] = _p(rng, {"figure_below": 4, "diagram_above": 3, "fig_below": 2, "figure_above": 1})
    s["conditions_in_plot"] = bool(rng.random() < 0.7)

    multi = n > 1 and spec["match"] != "single"
    if multi:
        s["identity"] = _p(rng, {"legend": 4, "inplot": 3 + T, "inplot_leader": 1 + T})
    else:
        s["identity"] = _p(rng, {"none": 6, "legend": 1, "inplot": 1})
    s["legend_opaque"] = bool(s["identity"] == "legend" and rng.random() < 0.5)
    s["color_mode"] = _p(rng, {"color": 6 - T, "bw": 3 + T})
    s["distinguish"] = (_p(rng, {"color": 6, "dash": 2, "width": 1, "none": T}) if s["color_mode"] == "color"
                        else _p(rng, {"dash": 3, "width": 1, "none": 2 + T}))
    if s["identity"] == "legend" and s["distinguish"] == "none" and multi:
        # a legend over identical strokes identifies nothing: the GT would be unbindable
        s["distinguish"] = "color" if s["color_mode"] == "color" else "dash"
    s["palette"] = _p(rng, {"tab": 3, "office": 2, "primary": 2, "muted": 2})
    s["stroke_pt"] = {0: _u(rng, 0.8, 1.6, 2), 1: _u(rng, 0.5, 1.6, 2), 2: _u(rng, 0.25, 2.0, 2),
                      3: float(rng.choice([_u(rng, 0.25, 0.45, 2), _u(rng, 1.8, 2.2, 2)]))}[T]
    s["markers"] = bool(rng.random() < (0.1 if T == 0 else 0.2))

    s["vector_structure"] = _p(rng, {"single": 8 - 2 * T, "split": 1 + T, "merged": T, "filled": T, "bezier": 1})
    if s["vector_structure"] == "merged" and s["identity"] == "legend":
        s["identity"] = "inplot"  # merged curves share one style, so only in-plot labels can identify them
    s["pdf_clip"] = _p(rng, {"clip_path": 3, "pre_clipped": 1})
    s["raster_embed"] = bool(rng.random() < [0.0, 0.05, 0.12, 0.3][T])
    s["dpi"] = int(rng.choice({0: [150, 200, 300], 1: [100, 120, 150, 200], 2: [72, 96, 110, 150],
                               3: [72, 80, 96, 110]}[T]))
    s["degrade"] = bool(rng.random() < [0.0, 0.25, 0.5, 0.8][T])
    # legibility floor: tick labels at least ~6.5 px tall in the case image
    s["tick_font_pt"] = round(max(s["tick_font_pt"], 6.5 * 72 / s["dpi"]), 2)
    s["font_pt"] = round(max(s["font_pt"], s["tick_font_pt"]), 2)

    s["annotation_cross"] = bool(T >= 2 and rng.random() < 0.35)
    s["reference_line"] = bool(T == 3 and rng.random() < 0.3)
    s["curve_on_gridline"] = bool(T == 3 and rng.random() < 0.2)
    s["signed_axis"] = (_p(rng, {"none": 8, "neg_left": 1, "neg_reversed": 1})
                        if (T >= 1 and not logx and spec["cls"] in ("transfer", "output", "rdson_id",
                                                                    "capacitance", "gate_charge"))
                        else "none")
    s["model_trap_prob"] = [0.0, 0.2, 0.5, 0.8][T]
    return s


def sample_page(rng, tier: str) -> dict:
    T = TIERS.index(tier)
    return {
        "panels": int(rng.choice([1, 2, 2, 4] if T < 2 else [2, 3, 4, 4])),
        "gap_pt": round(float(rng.uniform(40, 70)) if T < 2 else float(rng.choice([rng.uniform(8, 18),
                                                                                    rng.uniform(25, 60)])), 1),
        "header": bool(rng.random() < 0.8),
        "footer": bool(rng.random() < 0.8),
    }
