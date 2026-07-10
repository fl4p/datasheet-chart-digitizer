"""Raster trace extraction and semantic checks for MOSFET capacitance charts."""

from __future__ import annotations

import cv2
import numpy as np

from .capacitance_types import CapAnchor, PlotBox, Trace

def find_plot_box(gray: np.ndarray) -> PlotBox:
    height, width = gray.shape
    _, bw = cv2.threshold(gray, 245, 255, cv2.THRESH_BINARY_INV)

    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(80, height // 5)))
    v_lines = cv2.morphologyEx(bw, cv2.MORPH_OPEN, v_kernel)
    contours, _ = cv2.findContours(v_lines, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    v_boxes: list[tuple[int, int, int, int]] = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w <= 8 and h >= height * 0.45 and width * 0.08 <= x <= width * 0.96:
            v_boxes.append((x, y, w, h))

    if len(v_boxes) < 6:
        raise RuntimeError(f"could not find plot grid verticals; found {len(v_boxes)}")

    centers = np.array([x + w / 2 for x, _, w, _ in v_boxes])
    y_starts = np.array([y for _, y, _, _ in v_boxes])
    y_ends = np.array([y + h - 1 for _, y, _, h in v_boxes])
    x0 = int(round(float(centers.min())))
    x1 = int(round(float(centers.max())))
    y0 = int(round(float(np.median(y_starts))))
    y1 = int(round(float(np.median(y_ends))))

    if x1 - x0 < width * 0.5 or y1 - y0 < height * 0.45:
        raise RuntimeError(f"implausible plot box: {(x0, y0, x1, y1)} for image {(width, height)}")

    return PlotBox(x0=x0, y0=y0, x1=x1, y1=y1)


def extract_trace_components(
    gray: np.ndarray, plot: PlotBox, anchors: dict[str, CapAnchor] | None = None
) -> list[Trace]:
    roi = gray[plot.y0 : plot.y1 + 1, plot.x0 : plot.x1 + 1]

    # The Infineon traces are black; gridlines are gray. Keeping only very dark
    # pixels separates traces from the log grid. Work column-by-column instead
    # of relying on connected components: on some low-voltage parts Ciss and
    # Coss touch at the left edge and become one connected component.
    mask = _trace_fragment_mask((roi < 90).astype(np.uint8), plot)
    centers_by_x = [_cluster_column_runs(mask[:, x]) for x in range(mask.shape[1])]

    band_samples = [[], [], []]
    for centers in centers_by_x:
        if len(centers) >= 3:
            band_samples[0].append(centers[0])
            band_samples[1].append(centers[len(centers) // 2])
            band_samples[2].append(centers[-1])
    if any(len(samples) < plot.width * 0.15 for samples in band_samples):
        raise RuntimeError(
            "could not establish three stable trace bands: "
            + ", ".join(str(len(samples)) for samples in band_samples)
        )

    assigned = _track_directional_traces(centers_by_x, plot, anchors or {})
    assigned = _repair_leading_steep_coss(mask, centers_by_x, assigned, plot)
    assigned = _repair_coss_ciss_overlap_gap(assigned, plot)
    assigned = _repair_leading_steep_crss(mask, assigned, plot)

    traces: list[Trace] = []
    for name in ["Ciss", "Coss", "Crss"]:
        points = _smooth_points(assigned[name])
        if len(points) < plot.width * 0.25:
            raise RuntimeError(f"{name} has too few sampled columns: {len(points)}")
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        bbox = (min(xs) - plot.x0, min(ys) - plot.y0, max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)
        traces.append(Trace(name=name, area=len(points), bbox=bbox, points=points))
    return traces



def trace_semantic_diagnostics(traces: list[Trace], plot: PlotBox) -> dict[str, object]:
    by_name = {trace.name: trace.points for trace in traces}
    diagnostics: dict[str, object] = {}
    for name, points in by_name.items():
        if not points:
            diagnostics[name] = {
                "points": 0,
                "x_span_fraction": 0.0,
                "y_range_px": 0,
            }
            continue
        xs = [x for x, _ in points]
        ys = [y for _, y in points]
        diagnostics[name] = {
            "points": len(points),
            "x_span_fraction": (max(xs) - min(xs)) / max(1, plot.width - 1),
            "y_range_px": max(ys) - min(ys),
        }

    if all(name in by_name and by_name[name] for name in ("Ciss", "Coss", "Crss")):
        x_min = max(min(x for x, _ in by_name[name]) for name in ("Ciss", "Coss", "Crss"))
        x_max = min(max(x for x, _ in by_name[name]) for name in ("Ciss", "Coss", "Crss"))
        samples = list(range(x_min, x_max + 1, max(1, (x_max - x_min) // 200 or 1)))
        ciss = np.array([_interp_y(by_name["Ciss"], x) for x in samples])
        coss = np.array([_interp_y(by_name["Coss"], x) for x in samples])
        crss = np.array([_interp_y(by_name["Crss"], x) for x in samples])
        signs = np.sign(ciss - coss)
        nonzero = signs[signs != 0]
        swap_count = int(np.sum(nonzero[1:] != nonzero[:-1])) if len(nonzero) > 1 else 0
        crss_bottom = float(np.mean(crss >= np.maximum(ciss, coss))) if len(samples) else 0.0
        ciss_range = int(max(y for _, y in by_name["Ciss"]) - min(y for _, y in by_name["Ciss"]))
        coss_range = int(max(y for _, y in by_name["Coss"]) - min(y for _, y in by_name["Coss"]))
        diagnostics["checks"] = {
            "common_samples": len(samples),
            "ciss_coss_rank_swap_count": swap_count,
            "crss_bottom_fraction": crss_bottom,
            "ciss_y_range_px": ciss_range,
            "coss_y_range_px": coss_range,
            "ciss_flatter_than_coss": ciss_range < coss_range,
        }
    return diagnostics


def trace_validation_summary(diagnostics: dict[str, object]) -> dict[str, object]:
    reasons: list[str] = []
    for name in ("Ciss", "Coss", "Crss"):
        trace_diag = diagnostics.get(name)
        if not isinstance(trace_diag, dict):
            reasons.append(f"missing_{name}")
            continue
        points = int(trace_diag.get("points") or 0)
        span = float(trace_diag.get("x_span_fraction") or 0.0)
        if points < 8:
            reasons.append(f"{name}_too_few_points")
        if span < 0.75:
            reasons.append(f"{name}_short_x_span")

    checks = diagnostics.get("checks")
    if not isinstance(checks, dict):
        reasons.append("missing_semantic_checks")
    else:
        samples = int(checks.get("common_samples") or 0)
        swaps = int(checks.get("ciss_coss_rank_swap_count") or 0)
        crss_bottom = float(checks.get("crss_bottom_fraction") or 0.0)
        ciss_flatter = bool(checks.get("ciss_flatter_than_coss"))
        if samples < 20:
            reasons.append("too_few_common_samples")
        if swaps not in (0, 1):
            reasons.append("ciss_coss_rank_swap_count")
        if crss_bottom < 0.95:
            reasons.append("crss_not_bottom")
        if not ciss_flatter:
            reasons.append("ciss_not_flatter_than_coss")

    return {
        "status": "pass" if not reasons else "suspect",
        "reasons": reasons,
    }


def _interp_y(points: list[tuple[int, int]], x: int) -> float:
    ordered = sorted(points)
    if x <= ordered[0][0]:
        return float(ordered[0][1])
    if x >= ordered[-1][0]:
        return float(ordered[-1][1])
    for (x0, y0), (x1, y1) in zip(ordered, ordered[1:]):
        if x0 <= x <= x1:
            if x1 == x0:
                return float(y0)
            t = (x - x0) / (x1 - x0)
            return float(y0 + (y1 - y0) * t)
    return float(ordered[-1][1])


def _track_directional_traces(
    centers_by_x: list[list[float]], plot: PlotBox, anchors: dict[str, CapAnchor]
) -> dict[str, list[tuple[int, int]]]:
    seed_x = _seed_x_from_anchors(centers_by_x, anchors)
    specs = {
        "Ciss": {"seed_index": 0, "candidate": "upper"},
        "Coss": {"seed_index": 1, "candidate": "upper"},
        "Crss": {"seed_index": -1, "candidate": "bottom"},
    }
    tracked: dict[str, list[tuple[int, int]]] = {}
    for name, spec in specs.items():
        tracked[name] = _track_one_trace(
            centers_by_x,
            seed_x=seed_x,
            seed_index=int(spec["seed_index"]),
            candidate_kind=str(spec["candidate"]),
            plot=plot,
        )
    return tracked


def _repair_leading_steep_coss(
    mask: np.ndarray,
    centers_by_x: list[list[float]],
    assigned: dict[str, list[tuple[int, int]]],
    plot: PlotBox,
) -> dict[str, list[tuple[int, int]]]:
    if not all(name in assigned for name in ("Ciss", "Coss")):
        return assigned
    assigned = _repair_leading_coss_upper_envelope(centers_by_x, assigned, plot)
    ciss_by_x = {x: y for x, y in assigned["Ciss"]}
    coss = sorted(assigned["Coss"])
    if len(coss) < 8:
        return assigned

    repaired_coss = _repair_missing_leading_knee(mask, coss, plot)
    if repaired_coss is not None:
        if not _repair_shape_guard(repaired_coss, coss, plot, peers={"Ciss": assigned["Ciss"]}):
            repaired_coss = _trim_repair_points_on_peer(repaired_coss, coss, assigned["Ciss"], plot)
        if repaired_coss is not None and _repair_shape_guard(repaired_coss, coss, plot, peers={"Ciss": assigned["Ciss"]}):
            repaired_coss = _enforce_low_v_coss_monotone(repaired_coss, plot)
            if not _repair_shape_guard(repaired_coss, coss, plot, peers={"Ciss": assigned["Ciss"]}):
                return assigned
            out = dict(assigned)
            out["Coss"] = repaired_coss
            return out

    leading_overlap = [(x, y) for x, y in coss[:8] if x in ciss_by_x and abs(y - ciss_by_x[x]) <= 8]
    if not leading_overlap:
        return assigned

    stable: tuple[int, int] | None = None
    for x, y in coss:
        if x in ciss_by_x and y - ciss_by_x[x] >= 45 and x - plot.x0 <= plot.width * 0.20:
            stable = (x, y)
            break
    if stable is None:
        return assigned

    anchor_x = stable[0] - plot.x0
    anchor_y = stable[1] - plot.y0
    known: list[tuple[float, float]] = []
    for local_x in range(max(0, anchor_x - 24), anchor_x + 1):
        centers = centers_by_x[local_x]
        if len(centers) < 2:
            continue
        upper = centers[0]
        for center in centers[1:]:
            if center - upper >= 20 and center <= anchor_y + 20:
                known.append((float(local_x), float(center)))
                break
    if len(known) < 3:
        return assigned

    known.append((float(anchor_x), float(anchor_y)))
    known = sorted(set(known), key=lambda point: point[1])
    min_y = int(round(min(y for _, y in known)))
    max_y = int(round(anchor_y))
    if max_y - min_y < 20:
        return assigned

    repaired_by_x: dict[int, list[float]] = {}
    known_y = np.array([y for _, y in known], dtype=float)
    known_x = np.array([x for x, _ in known], dtype=float)
    for local_y in range(min_y, max_y + 1):
        expected_x = float(np.interp(local_y, known_y, known_x))
        row_centers = _cluster_row_runs(mask[local_y, :])
        candidates = [x for x in row_centers if abs(x - expected_x) <= 8.0 and x <= anchor_x + 4]
        if not candidates:
            continue
        local_x = int(round(min(candidates, key=lambda x: abs(x - expected_x))))
        repaired_by_x.setdefault(local_x, []).append(float(local_y))

    if len(repaired_by_x) < 5:
        return assigned

    repaired_points = [
        (plot.x0 + local_x, plot.y0 + int(round(float(np.median(ys)))))
        for local_x, ys in sorted(repaired_by_x.items())
    ]
    first_repair_x = repaired_points[0][0]
    repaired_coss = [
        point
        for point in coss
        if not (point[0] < stable[0] and point[0] >= first_repair_x and point[0] in ciss_by_x and abs(point[1] - ciss_by_x[point[0]]) <= 12)
    ]
    by_x = {x: y for x, y in repaired_coss}
    for x, y in repaired_points:
        if x < stable[0]:
            by_x[x] = y
    out = dict(assigned)
    repaired_coss = sorted(by_x.items())
    if not _repair_shape_guard(repaired_coss, coss, plot, peers={"Ciss": assigned["Ciss"]}):
        return assigned
    out["Coss"] = repaired_coss
    return out


def _repair_leading_coss_upper_envelope(
    centers_by_x: list[list[float]],
    assigned: dict[str, list[tuple[int, int]]],
    plot: PlotBox,
) -> dict[str, list[tuple[int, int]]]:
    """Recover Coss when the low-VDS Ciss/Coss traces share a column center.

    The raster tracker starts in the unambiguous right half of the plot. When it
    walks left through a low-VDS Ciss/Coss crossing, the two upper traces can be
    close enough that the greedy tracker assigns both labels to the flatter Ciss
    branch. The column data still contains the missing high-capacitance Coss
    branch as the upper envelope, so splice that envelope into the leading Coss
    segment before the generic vertical-knee repair runs.
    """
    ciss = sorted(assigned.get("Ciss", []))
    coss = sorted(assigned.get("Coss", []))
    if len(ciss) < 8 or len(coss) < 8:
        return assigned

    ciss_by_x = {x: y for x, y in ciss}
    coss_by_x = {x: y for x, y in coss}
    common_xs = sorted(set(ciss_by_x) & set(coss_by_x))
    if not common_xs:
        return assigned

    low_v_limit = plot.x0 + int(round(plot.width * 0.22))
    leading_common = [
        x for x in common_xs
        if x <= low_v_limit and abs(ciss_by_x[x] - coss_by_x[x]) <= 6
    ]
    if len(leading_common) < 5:
        return assigned

    replacement: dict[int, int] = {}
    misses_after_hit = 0
    for local_x in range(0, min(len(centers_by_x), int(round(plot.width * 0.35)))):
        global_x = plot.x0 + local_x
        ciss_y = _interp_y_in_range(ciss, global_x)
        if ciss_y is None:
            continue
        centers = centers_by_x[local_x]
        if len(centers) < 2:
            if replacement:
                misses_after_hit += 1
                if misses_after_hit > 10:
                    break
            continue
        # Pixel y decreases as capacitance increases. A real low-VDS Coss
        # branch is visibly above Ciss; ignore tiny separations from stroke
        # thickness or anti-aliasing. If multiple branches are above Ciss, use
        # the nearest one, not the topmost envelope: the topmost trace can be
        # Ciss on charts with a steep low-V input-capacitance knee.
        min_separation = max(8.0, plot.height * 0.012)
        candidates = [
            plot.y0 + int(round(center))
            for center in centers
            if ciss_y - (plot.y0 + int(round(center))) >= min_separation
        ]
        if candidates:
            replacement[global_x] = max(candidates)
            misses_after_hit = 0
        elif replacement:
            misses_after_hit += 1
            if misses_after_hit > 10:
                break

    if len(replacement) < 6:
        return assigned

    replacement_min = min(replacement)
    replacement_max = max(replacement)
    merged = {
        x: y for x, y in coss
        if not (replacement_min <= x <= replacement_max and abs(y - ciss_by_x.get(x, y)) <= 8)
    }
    merged.update(replacement)

    bridge_width = max(12, int(round(plot.width * 0.04)))
    bridge_target_x = min(
        [x for x in coss_by_x if x > replacement_max + bridge_width],
        default=None,
    )
    if bridge_target_x is not None:
        start_x = replacement_max
        start_y = replacement[replacement_max]
        target_y = coss_by_x[bridge_target_x]
        for x in range(start_x + 1, bridge_target_x):
            if x in coss_by_x and abs(coss_by_x[x] - ciss_by_x.get(x, coss_by_x[x])) > 8:
                continue
            t = (x - start_x) / max(1, bridge_target_x - start_x)
            merged[x] = int(round(start_y + (target_y - start_y) * t))

    repaired_coss = sorted(merged.items())
    repaired_coss = _enforce_low_v_coss_monotone(repaired_coss, plot)

    changed = _changed_repair_segment(repaired_coss, coss)
    if not changed:
        return assigned
    if _overlaps_peer_for_too_long(changed, {"Ciss": ciss}):
        return assigned
    if not _low_v_nonfolding(repaired_coss, plot):
        return assigned

    out = dict(assigned)
    out["Coss"] = repaired_coss
    return out


def _repair_coss_ciss_overlap_gap(
    assigned: dict[str, list[tuple[int, int]]],
    plot: PlotBox,
) -> dict[str, list[tuple[int, int]]]:
    """Bridge long Coss/Ciss overlap runs caused by text-label occlusion.

    On some raster charts the "Coss" label and leader line obscure the actual
    Coss stroke around the Ciss/Coss crossing. Column tracking then glues Coss
    to Ciss for a long run, even though Coss is clearly above Ciss before the
    label and below Ciss after it. Treat only that rank-swap overlap pattern as
    missing data and interpolate through it.
    """
    ciss = sorted(assigned.get("Ciss", []))
    coss = sorted(assigned.get("Coss", []))
    if len(ciss) < 8 or len(coss) < 8:
        return assigned

    close_tol = max(6.0, plot.height * 0.012)
    sep_tol = max(12.0, plot.height * 0.020)
    min_run = max(18, int(round(plot.width * 0.05)))
    max_run = int(round(plot.width * 0.45))
    xs = sorted(x for x, _ in coss if _interp_y_in_range(ciss, x) is not None)
    if not xs:
        return assigned

    runs: list[list[int]] = []
    current: list[int] = []
    for x in xs:
        ciss_y = _interp_y_in_range(ciss, x)
        coss_y = _interp_y_in_range(coss, x)
        close = ciss_y is not None and coss_y is not None and abs(coss_y - ciss_y) <= close_tol
        if close:
            if current and x > current[-1] + 1:
                runs.append(current)
                current = []
            current.append(x)
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)

    coss_by_x = {x: y for x, y in coss}
    repaired_by_x = dict(coss_by_x)
    changed = False
    for run in runs:
        if not (min_run <= len(run) <= max_run):
            continue
        start_x = run[0]
        end_x = run[-1]
        left = _nearest_separated_coss_sample(ciss, coss, start_x, -1, sep_tol, plot)
        right = _nearest_separated_coss_sample(ciss, coss, end_x, 1, sep_tol, plot)
        if left is None or right is None:
            continue
        left_delta = left[1] - float(_interp_y_in_range(ciss, left[0]) or left[1])
        right_delta = right[1] - float(_interp_y_in_range(ciss, right[0]) or right[1])
        if not (left_delta <= -sep_tol and right_delta >= sep_tol):
            continue
        if right[0] <= left[0] or right[0] - left[0] > plot.width * 0.55:
            continue
        for x in range(left[0] + 1, right[0]):
            if x not in repaired_by_x:
                continue
            t = (x - left[0]) / max(1, right[0] - left[0])
            repaired_by_x[x] = int(round(left[1] + (right[1] - left[1]) * t))
        changed = True

    if not changed:
        return assigned

    repaired_coss = sorted(repaired_by_x.items())
    if not _repair_shape_guard(repaired_coss, coss, plot, peers={"Ciss": ciss}):
        return assigned
    out = dict(assigned)
    out["Coss"] = repaired_coss
    return out


def _nearest_separated_coss_sample(
    ciss: list[tuple[int, int]],
    coss: list[tuple[int, int]],
    x: int,
    direction: int,
    sep_tol: float,
    plot: PlotBox,
) -> tuple[int, float] | None:
    limit = plot.x0 if direction < 0 else plot.x1
    max_distance = int(round(plot.width * 0.20))
    coss_by_x = {px: py for px, py in coss}
    px = x + direction
    while (px >= limit if direction < 0 else px <= limit) and abs(px - x) <= max_distance:
        coss_y = coss_by_x.get(px)
        ciss_y = _interp_y_in_range(ciss, px)
        if coss_y is not None and ciss_y is not None and abs(coss_y - ciss_y) >= sep_tol:
            return (px, float(coss_y))
        px += direction
    return None


def _enforce_low_v_coss_monotone(
    points: list[tuple[int, int]], plot: PlotBox, fraction: float = 0.35
) -> list[tuple[int, int]]:
    """Remove small low-VDS Coss folds introduced by label-gap repairs.

    Coss is physically non-increasing with VDS on these plots, so in image
    coordinates its y should be nondecreasing as x increases. Raster label gaps
    can make a repaired prefix jump back upward by a few pixels; clamp only the
    low-VDS repair region so the rest of the extracted curve remains untouched.
    """
    limit_x = plot.x0 + int(round(plot.width * fraction))
    max_jitter = max(8.0, plot.height * 0.02)
    out: list[tuple[int, int]] = []
    running_y: int | None = None
    for x, y in sorted(points):
        if x <= limit_x:
            if running_y is not None and y < running_y and running_y - y <= max_jitter:
                y = running_y
            running_y = y
        out.append((x, y))
    return out


def _trim_repair_points_on_peer(
    repaired: list[tuple[int, int]],
    original: list[tuple[int, int]],
    peer: list[tuple[int, int]],
    plot: PlotBox,
) -> list[tuple[int, int]] | None:
    """Keep only repaired points that have separated from a peer trace.

    Row-wise knee recovery can follow the shared top of a crossing before it
    reaches the intended Coss branch. Those points are visually on Ciss and
    should not be grafted into Coss. Once the recovered path drops below Ciss by
    a visible margin, keep it as the missing Coss knee.
    """
    margin = max(10.0, plot.height * 0.018)
    keep: list[tuple[int, int]] = []
    for x, y in _changed_repair_segment(repaired, original):
        peer_y = _interp_y_in_range(peer, x)
        # Coss sits below Ciss in image coordinates after the low-VDS crossing.
        # Points above Ciss are still the shared/peer branch and must not be
        # grafted into Coss, even if they are well separated.
        if peer_y is not None and y - peer_y >= margin:
            keep.append((x, y))
    if len(keep) < 6:
        return None
    merged = {x: y for x, y in original}
    for x, y in keep:
        merged[x] = y
    return sorted(merged.items())


def _repair_missing_leading_knee(
    mask: np.ndarray, points: list[tuple[int, int]], plot: PlotBox
) -> list[tuple[int, int]] | None:
    """Prepend a near-vertical left-edge knee as one y per x.

    This handles raster Coss traces on high-voltage SiC charts where the first
    tracked column is already below the nearly vertical low-VDS rise. The row
    walk recovers the steep segment; grouping by x with a median center keeps
    the exported curve single-valued.
    """
    if len(points) < 8:
        return None
    ordered = sorted(points)
    anchor = ordered[0]
    anchor_x = anchor[0] - plot.x0
    anchor_y = anchor[1] - plot.y0
    if anchor_x > plot.width * 0.16 or anchor_y < plot.height * 0.10:
        return None

    max_band = int(round(plot.height * 0.45))
    y_min = max(0, anchor_y - max_band)
    left_limit = max(anchor_x + 18.0, plot.width * 0.07)
    row_points: list[tuple[int, int]] = []
    last_x: float | None = None
    for local_y in range(anchor_y - 1, y_min - 1, -1):
        row_centers = _cluster_row_runs(mask[local_y, :])
        candidates = [x for x in row_centers if 0 <= x <= left_limit]
        if not candidates:
            continue
        if last_x is None:
            best = min(candidates, key=lambda x: abs(x - anchor_x))
        else:
            monotone_candidates = [x for x in candidates if x <= last_x + 4.0]
            if not monotone_candidates:
                continue
            best = min(monotone_candidates, key=lambda x: abs(x - last_x))
            if abs(best - last_x) > 12.0:
                continue
        last_x = float(best)
        row_points.append((plot.x0 + int(round(best)), plot.y0 + local_y))

    if len(row_points) < 10:
        return None

    by_x: dict[int, list[int]] = {}
    for x, y in row_points + [anchor]:
        by_x.setdefault(x, []).append(y)
    repaired: list[tuple[int, int]] = []
    running_y: int | None = None
    for x in sorted(by_x):
        # Use the upper envelope for Coss: this repair only covers the missing
        # high-capacitance left-edge knee, and the visible stroke top is the
        # conservative continuation toward VDS=0.
        y = int(min(by_x[x]))
        if running_y is not None and y < running_y:
            y = running_y
        running_y = y
        repaired.append((x, y))
    if len(repaired) < 2:
        return None

    x_cover_min = min(x for x, _ in repaired)
    x_cover_max = max(x for x, _ in repaired)
    merged = {x: y for x, y in ordered if not (x_cover_min <= x <= x_cover_max)}
    for x, y in repaired:
        merged[x] = y
    return sorted(merged.items())


def _repair_leading_steep_crss(
    mask: np.ndarray,
    assigned: dict[str, list[tuple[int, int]]],
    plot: PlotBox,
) -> dict[str, list[tuple[int, int]]]:
    """Recover the near-vertical low-VDS Crss knee in raster charts.

    Column sampling deliberately ignores tall runs so grid lines and merged
    strokes do not collapse multiple traces into one center. That also drops
    the left-edge Crss knee on SiC capacitance plots, where the trace is almost
    vertical. Repair that local segment by sampling row runs near the left edge
    and splicing them before the first column-tracked Crss point.
    """
    crss = assigned.get("Crss")
    if not crss or len(crss) < 8:
        return assigned

    crss_by_path = list(crss)
    left_candidates = [point for point in crss_by_path if point[0] - plot.x0 <= plot.width * 0.16]
    if not left_candidates:
        return assigned
    anchor = min(left_candidates, key=lambda point: (point[0], point[1]))
    anchor_x = anchor[0] - plot.x0
    anchor_y = anchor[1] - plot.y0
    if anchor_x > plot.width * 0.16 or anchor_y < plot.height * 0.25:
        return assigned

    # Only repair the local missing knee. Extending too far upward can steal the
    # overlapping Coss/Ciss low-VDS rise, so cap the search to a modest vertical
    # band above the first stable Crss point.
    max_band = int(round(plot.height * 0.45))
    y_min = max(0, anchor_y - max_band)
    left_limit = max(anchor_x + 18.0, plot.width * 0.07)

    row_points: list[tuple[int, int]] = []
    last_x: float | None = None
    for local_y in range(anchor_y - 1, y_min - 1, -1):
        row_centers = _cluster_row_runs(mask[local_y, :])
        candidates = [x for x in row_centers if 0 <= x <= left_limit]
        if not candidates:
            continue
        if last_x is None:
            monotone_candidates = [x for x in candidates if x <= anchor_x + 4.0]
            if not monotone_candidates:
                continue
            best = min(monotone_candidates, key=lambda x: abs(x - anchor_x))
        else:
            # Crss is a single-valued decreasing C(VDS) curve. Walking toward
            # higher capacitance (smaller pixel-y) must not move the trace to
            # larger VDS, otherwise we are following a different left-edge
            # branch and will create a loop in the overlay/data.
            monotone_candidates = [x for x in candidates if x <= last_x + 4.0]
            if not monotone_candidates:
                continue
            best = min(monotone_candidates, key=lambda x: abs(x - last_x))
            if abs(best - last_x) > 12.0:
                continue
        last_x = float(best)
        row_points.append((plot.x0 + int(round(best)), plot.y0 + local_y))

    if len(row_points) < 12:
        return assigned

    # Convert the row-walk back to a function y(x). Raster strokes can be nearly
    # vertical, but the digitized C(V) curve must still have one capacitance per
    # VDS. Use the upper envelope for each x, then enforce nondecreasing y as x
    # increases so the repaired Crss knee cannot fold back on itself.
    by_x: dict[int, int] = {}
    for x, y in row_points + [anchor]:
        old = by_x.get(x)
        if old is None or y < old:
            by_x[x] = y
    repaired: list[tuple[int, int]] = []
    running_y: int | None = None
    for x in sorted(by_x):
        y = by_x[x]
        if running_y is not None and y < running_y:
            y = running_y
        running_y = y
        repaired.append((x, y))
    if len(repaired) < 2:
        return assigned

    x_cover_min = min(x for x, _ in repaired)
    x_cover_max = max(x for x, _ in repaired)
    remainder = [
        point
        for point in crss_by_path
        if not (
            x_cover_min <= point[0] <= x_cover_max
            and point != anchor
        )
    ]

    out = dict(assigned)
    merged_by_x = {x: y for x, y in remainder}
    for x, y in repaired:
        merged_by_x[x] = y
    repaired_crss = sorted(merged_by_x.items())
    peers = {name: points for name, points in assigned.items() if name in ("Ciss", "Coss")}
    if not _repair_shape_guard(repaired_crss, crss_by_path, plot, peers=peers, require_bottom=True):
        return assigned
    out["Crss"] = repaired_crss
    return out


def _repair_shape_guard(
    repaired: list[tuple[int, int]],
    original: list[tuple[int, int]],
    plot: PlotBox,
    *,
    peers: dict[str, list[tuple[int, int]]] | None = None,
    require_bottom: bool = False,
) -> bool:
    if not repaired or not _single_valued_by_x(repaired):
        return False
    if not _low_v_nonfolding(repaired, plot):
        return False
    if not _splice_continuity_ok(repaired, original, plot):
        return False
    repair_segment = _changed_repair_segment(repaired, original) or repaired
    if peers and _overlaps_peer_for_too_long(repair_segment, peers):
        return False
    if require_bottom and peers and not _is_bottom_branch(repair_segment, peers):
        return False
    return True


def _single_valued_by_x(points: list[tuple[int, int]]) -> bool:
    return len({x for x, _ in points}) == len(points)


def _low_v_nonfolding(points: list[tuple[int, int]], plot: PlotBox) -> bool:
    low_v_limit = plot.x0 + int(round(plot.width * 0.20))
    low_v_points = [(x, y) for x, y in sorted(points) if x <= low_v_limit]
    if len(low_v_points) < 3:
        return True
    ys = np.array([y for _, y in low_v_points], dtype=float)
    return bool(np.all(np.diff(ys) >= -3.0))


def _splice_continuity_ok(
    repaired: list[tuple[int, int]], original: list[tuple[int, int]], plot: PlotBox
) -> bool:
    changed_runs = _changed_repair_runs(repaired, original)
    if not changed_runs:
        return True
    repaired_sorted = sorted(repaired)
    for changed in changed_runs:
        changed_min_x = min(x for x, _ in changed)
        changed_max_x = max(x for x, _ in changed)
        first_changed = min(changed, key=lambda point: point[0])
        last_changed = max(changed, key=lambda point: point[0])
        prev_tail = [point for point in repaired_sorted if point[0] < changed_min_x]
        next_tail = [point for point in repaired_sorted if point[0] > changed_max_x]
        if prev_tail and not _splice_pair_continuous(prev_tail[-1], first_changed, plot):
            return False
        if next_tail and not _splice_pair_continuous(last_changed, next_tail[0], plot):
            return False
    return True


def _changed_repair_segment(
    repaired: list[tuple[int, int]], original: list[tuple[int, int]], y_tol: int = 3
) -> list[tuple[int, int]]:
    original_by_x = dict(original)
    return [(x, y) for x, y in sorted(repaired) if x not in original_by_x or abs(y - original_by_x[x]) > y_tol]


def _changed_repair_runs(
    repaired: list[tuple[int, int]], original: list[tuple[int, int]], y_tol: int = 3
) -> list[list[tuple[int, int]]]:
    changed = _changed_repair_segment(repaired, original, y_tol=y_tol)
    if not changed:
        return []
    runs: list[list[tuple[int, int]]] = [[changed[0]]]
    for point in changed[1:]:
        if point[0] <= runs[-1][-1][0] + 1:
            runs[-1].append(point)
        else:
            runs.append([point])
    return runs


def _splice_pair_continuous(a: tuple[int, int], b: tuple[int, int], plot: PlotBox) -> bool:
    dx = b[0] - a[0]
    dy = abs(b[1] - a[1])
    return 0 < dx <= max(8, plot.width * 0.04) and dy <= max(24, plot.height * 0.08)


def _overlaps_peer_for_too_long(
    points: list[tuple[int, int]], peers: dict[str, list[tuple[int, int]]]
) -> bool:
    shared = 0
    close = 0
    for x, y in points:
        for peer_points in peers.values():
            peer_y = _interp_y_in_range(peer_points, x)
            if peer_y is None:
                continue
            shared += 1
            if abs(y - peer_y) <= 4:
                close += 1
    return shared >= 6 and close / shared > 0.45


def _is_bottom_branch(points: list[tuple[int, int]], peers: dict[str, list[tuple[int, int]]]) -> bool:
    samples = 0
    bottom = 0
    for x, y in points:
        peer_ys = [
            peer_y
            for peer_points in peers.values()
            for peer_y in [_interp_y_in_range(peer_points, x)]
            if peer_y is not None
        ]
        if not peer_ys:
            continue
        samples += 1
        if y >= max(peer_ys) - 8:
            bottom += 1
    return samples < 4 or bottom / samples >= 0.80


def _interp_y_in_range(points: list[tuple[int, int]], x: int) -> float | None:
    if not points:
        return None
    ordered = sorted(points)
    if x < ordered[0][0] or x > ordered[-1][0]:
        return None
    return _interp_y(ordered, x)


def _seed_x_from_anchors(centers_by_x: list[list[float]], anchors: dict[str, CapAnchor]) -> int:
    vds_values = [anchor.vds_v for anchor in anchors.values() if anchor.vds_v > 0]
    if not vds_values:
        return _seed_x_from_middle(centers_by_x)

    # Infineon capacitance tables quote the characteristic point halfway along
    # these plots: 15 V on 0..30 V charts, 30 V on 0..60 V, 40 V on 0..80 V.
    anchor_vds = float(np.median(vds_values))
    axis_max_v = anchor_vds * 2.0
    target = int(round((anchor_vds / axis_max_v) * (len(centers_by_x) - 1)))
    candidates = [x for x, centers in enumerate(centers_by_x) if len(centers) >= 3]
    if not candidates:
        return _seed_x_from_middle(centers_by_x)
    return min(candidates, key=lambda x: abs(x - target))


def _seed_x_from_middle(centers_by_x: list[list[float]]) -> int:
    target = int(len(centers_by_x) * 0.55)
    candidates = [x for x, centers in enumerate(centers_by_x) if len(centers) >= 3]
    if not candidates:
        raise RuntimeError("could not find a three-trace seed column")
    return min(candidates, key=lambda x: abs(x - target))


def _track_one_trace(
    centers_by_x: list[list[float]],
    seed_x: int,
    seed_index: int,
    candidate_kind: str,
    plot: PlotBox,
) -> list[tuple[int, int]]:
    seed_centers = centers_by_x[seed_x]
    if len(seed_centers) < 3:
        raise RuntimeError(f"seed column {seed_x} has only {len(seed_centers)} centers")
    seed_y = float(seed_centers[seed_index])
    local_points = [(seed_x, seed_y)]
    local_points.extend(
        _track_direction(centers_by_x, seed_x, seed_y, -1, candidate_kind)
    )
    local_points.extend(
        _track_direction(centers_by_x, seed_x, seed_y, 1, candidate_kind)
    )

    by_x: dict[int, float] = {}
    for x, y in local_points:
        if x < 3:
            continue
        old = by_x.get(x)
        if old is None or abs(y - seed_y) < abs(old - seed_y):
            by_x[x] = y

    return [(plot.x0 + x, plot.y0 + int(round(by_x[x]))) for x in sorted(by_x)]


def _track_direction(
    centers_by_x: list[list[float]],
    seed_x: int,
    seed_y: float,
    direction: int,
    candidate_kind: str,
) -> list[tuple[int, float]]:
    points: list[tuple[int, float]] = [(seed_x, seed_y)]
    out: list[tuple[int, float]] = []
    misses = 0
    max_misses = 80
    max_step = 32.0
    max_reacquire_step = 24.0
    x = seed_x + direction
    while 0 <= x < len(centers_by_x):
        candidates = _trace_candidates(centers_by_x[x], candidate_kind)
        pred = _predict_y(points, x)
        if candidates:
            best = min(candidates, key=lambda y: abs(y - pred))
            step_limit = max_reacquire_step if misses else max_step
            if abs(best - pred) <= step_limit:
                points.append((x, best))
                out.append((x, best))
                misses = 0
            else:
                misses += 1
        else:
            misses += 1
        if misses > max_misses:
            break
        x += direction
    return out


def _trace_candidates(centers: list[float], candidate_kind: str) -> list[float]:
    if not centers:
        return []
    if candidate_kind == "upper":
        if len(centers) >= 3:
            return centers[:2]
        if len(centers) == 2:
            return [centers[0]]
        return []
    if candidate_kind == "bottom":
        return [centers[-1]]
    return []


def _predict_y(points: list[tuple[int, float]], x: int) -> float:
    if len(points) < 2:
        return points[-1][1]
    x1, y1 = points[-1]
    x0, y0 = points[-2]
    if x1 == x0:
        return y1
    return y1 + (y1 - y0) * ((x - x1) / (x1 - x0))


def _trace_fragment_mask(mask: np.ndarray, plot: PlotBox) -> np.ndarray:
    num, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    cleaned = np.zeros_like(mask)
    min_width = max(50, int(plot.width * 0.12))
    for component in range(1, num):
        _, _, w, _, area = stats[component]
        if area >= 80 and w >= min_width:
            cleaned[labels == component] = 1
    return cleaned


def _cluster_column_runs(column: np.ndarray) -> list[float]:
    ys = np.where(column > 0)[0]
    if len(ys) == 0:
        return []

    centers: list[float] = []
    start = int(ys[0])
    prev = int(ys[0])
    for y_raw in ys[1:]:
        y = int(y_raw)
        if y == prev + 1:
            prev = y
            continue
        if prev - start + 1 <= 12:
            centers.append((start + prev) / 2)
        start = y
        prev = y
    if prev - start + 1 <= 12:
        centers.append((start + prev) / 2)

    if not centers:
        return []

    clustered: list[list[float]] = []
    for center in sorted(centers):
        if clustered and center - clustered[-1][-1] <= 14:
            clustered[-1].append(center)
        else:
            clustered.append([center])
    return [float(np.median(group)) for group in clustered]


def _cluster_row_runs(row: np.ndarray) -> list[float]:
    xs = np.where(row > 0)[0]
    if len(xs) == 0:
        return []

    centers: list[float] = []
    start = int(xs[0])
    prev = int(xs[0])
    for x_raw in xs[1:]:
        x = int(x_raw)
        if x == prev + 1:
            prev = x
            continue
        if prev - start + 1 <= 18:
            centers.append((start + prev) / 2)
        start = x
        prev = x
    if prev - start + 1 <= 18:
        centers.append((start + prev) / 2)

    if not centers:
        return []
    clustered: list[list[float]] = []
    for center in sorted(centers):
        if clustered and center - clustered[-1][-1] <= 10:
            clustered[-1].append(center)
        else:
            clustered.append([center])
    return [float(np.median(group)) for group in clustered]


def _smooth_points(points: list[tuple[int, int]], window: int = 7) -> list[tuple[int, int]]:
    if len(points) < window:
        return points
    half = window // 2
    smoothed: list[tuple[int, int]] = []
    for idx, (x, y) in enumerate(points):
        lo = max(0, idx - half)
        hi = min(len(points), idx + half + 1)
        smoothed.append((x, int(round(float(np.median([py for _, py in points[lo:hi]]))))))
    return smoothed

