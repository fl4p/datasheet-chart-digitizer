# EPC colored-vector capacitance (top50-fugu2-gan-ls feedback)

Status: LANDED + HUMAN-GREEN. Legend-color identity binding lives in
`capacitance_vector.py` (`_legend_swatch_bindings` / `_legend_color_names`,
provenance `legend_color_components`). Fab reviewed the rebuilt packet on
2026-07-29: 22 green / 1 flagged / 2 pending — all 10 re-extracted EPC panels
GREEN, including EPC2022 (whose earlier flag was a bulk comment; its
extraction was already correct, verified at 2.2x for Crss and 5x through its
Ciss/Coss crossing).

Codex adversarial review returned two P1 fail-opens, both fixed before the
commit: (1) proximity was arbitrating between differently-colored segments on
one legend row — now two distinct eligible colors return None; (2) the
chromatic-slope qualifier accepted same-color ink anywhere on the page — now
the sloped evidence must be plot-relevant. Its P2 (tests restating positional
order) is addressed by asserting EPC2367's actual printed swatch colors.

Y-axis labels on the EPC panels (Fab's follow-up) needed no new code: the
vector-gridline seating landed in `a48ae35` upgrades six EPC panels
(2053/2088/2218/2305/2361/2367) from `overlay-review-required` to trusted
`ok`; the packet must be rebuilt on that HEAD to show the ticks.

NEW open item from the same review — `toshiba/TPH2R70AR5/capacitance/fig811`:
"coss snaps to ciss". Confirmed on the overlay (Coss rides Ciss from 0.1 V to
~1.7 V, then separates). Already fail-closed as `unverified` /
`ciss_coss_shared_trace_orphans_source_branch`; log-log Toshiba raster, a
separate lane from the EPC color work.

Source of record:
`datasheet-chart-digitizer/.vibe-drops/191ccc0b-top50-fugu2-gan-ls-001.review.json`
(2026-07-29, 25 items: 13 green — all infineon fig11 — and 11 EPC parts
flagged/rework: "all wrong, real crss near 0. can you use colors for
classifications?"). Packet built at `674aab1`, so these are live defects.

## Root cause

EPC GaN datasheets print Ciss/Coss/Crss as three COLORED vector curves
(legend: C_OSS dark red, C_ISS dark green, C_RSS light green). The grayscale
band extractor merges the two dark colors (Ciss=Coss shared collapse onto the
top curve) and the light-green Crss never crosses the `<90` dark threshold at
all, so "Crss" lands on the real Ciss. Everything was fail-closed (`gap`,
`ciss_coss_unresolved_shared_collapse` + untrusted axis) — wrong picture, no
served values.

## Prototype (validated on 5 parts)

`out/fugu2-100v-LS2p-gan/coss-review-top50-2026-07-28/color-proto-samples/`
(EPC2367, EPC2361, EPC2302 d904, EPC2091, EPC2022 + the script). Approach,
all fail-closed:

- legend binding: each CISS/COSS/CRSS legend word must own a colored swatch
  segment within 12 pt on its row; formula occurrences without swatches are
  skipped; conflicting or non-distinct swatches refuse;
- curve strokes bind to the NEAREST legend color, requiring distance <= 0.40
  and >= 1.8x separation from the runner-up (curve shades differ slightly
  from swatch shades; EPC2361 even draws Ciss in a different green than its
  own swatch). The lib version should compare hue, not raw RGB distance;
- per-color polylines from `page.get_drawings()`, clipped to the crop.

Identity result on all five: Ciss = flat top (dark green), Coss = steep
(dark red), Crss = near zero (light green) — matching Fab's "real crss near
0" on every sample.

## Lib integration requirements (after human verification)

1. Clip curves to the panel's OWN plot frame, not the crop box — EPC2361's
   crop overflows into Figure 6 and its dark-red Qoss points color-bind as
   Coss.
2. Resample polyline control points per x column (prototype density is
   control-point-level).
3. Coordinate with the in-flight `source_color_binding.py` (other agent's
   two-row legend binding for diode/transfer): capacitance needs a
   three-row variant; do not fork the concept silently.
4. Axis calibration on these panels is separately untrusted
   (`axis_calibration_untrusted` on fig901/904) — color identity does not fix
   axes; treat as its own gap.
5. EPC prints linear (fig 5a) AND log (fig 5b) capacitance panels; both
   crops exist (d901/d902, d904/d905) — decide which serves, or both with
   cross-checks.
