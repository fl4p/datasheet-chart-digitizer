# Automated Chart-Review Checklist

Instructions for an agent reviewing digitized MOSFET/diode datasheet charts (dsdig
extractions). Work top to bottom per item. The source-vs-extracted-point **overlay is the
gate** — guards and crosshair-centering alone do NOT prove fidelity.

---

## 0. Rule zero — fail-closed verdict discipline

- **When a check cannot evaluate its input, it returns `unverified`, never `OK`.**
  Absence of evidence is not absence of the problem.
- If the extractor self-reports **low/negative confidence score, `scale=FAIL`,
  `near_axis_top=True`, `unsupported_axis`, `binding_error`, or "overlay-review-required"**,
  the item **cannot be auto-GREEN** — escalate to deeper/human review with the concrete reason.
- A nominal `status=ok` never overrides a defect diagnostic. Any diagnostic containing
  `*_unresolved`, `unit_unresolved`, or an axis/plot-box warning makes the item UNVERIFIED
  until that exact condition is resolved or proven to be a false alarm against a known-good
  and a known-bad fixture.
- `axis_calibration_trusted=true` is invalid while any active `axis_position_error`,
  `axis_grid_error`, or `axis_ocr_error` is present. Earlier failed attempts may be retained
  separately as provenance, but they must not masquerade as active errors beside a trusted
  selected calibration.
- If the chart contains **intersecting curves** and the reviewer did not perform the 5×
  intersection-point inspection (§3), the item **cannot be auto-GREEN** — treat as
  `unverified` due to missing evidence.
- Every guard must have been **seen to fire** on a known-bad input. A guard never observed to
  fail is not a guard.
- Test the **far tail**, not just the near-miss case.

## 1. Panel & chart-type identification

- Confirm the **correct panel** was selected. A broad caption ("Gate Charge") can override a
  locally-correct panel ID — verify the **axes match the claimed chart type**. A body-diode
  I-V, a transfer curve, a gate-charge curve, and an RDS(on)-vs-T curve all look different.
- Confirm the panel is a **device-specific, data-bearing characteristic**, not a test-circuit
  schematic, definition diagram, or idealized example waveform. Captions such as “Gate Charge
  Test Circuit & Waveform” can include a generic VGS-vs-charge sketch with a Miller plateau;
  its chart-like axes do not make it measured extraction evidence. Reject such panels even when
  their quantity names resemble a supported chart.
- Confirm the figure title / conditions box (VDS, ID, VGS, Tj, f) is consistent with the
  claimed quantity.

## 2. Axes & scales

- **Log vs linear** classification correct on **each** axis.
- **Decade span on log axes:** verify the *number of decades*, not just log-vs-linear.
  Reading a 3-decade axis (10–10⁴ pF) as 1 decade (10–10² pF) is a silent ~100× error.
  Cross-check the decade span against expected value magnitude (see §6).
- **Superscript decades are semantic:** `10ⁿ` means `10^n`, not the concatenated decimal
  token `10n` (`10²` is 100, never 102; `10⁻²` is 0.01). Verify the consumed tick
  values as well as their pixel locations, and keep a true linear-axis value such as 102
  unchanged when no superscript glyph evidence exists.
- **Axis range** recognition (min/max) correct.
- **Dual / multiple y-axes:** some charts have two y-axes (e.g. VDS-left + VGS-right, or a
  bled-in Ct/trr axis). Verify the trace is bound to the **correct axis** — a Vpl read against
  the wrong axis can come out several × off. If a chart is a dual-axis type the extractor
  cannot bind, **fail-closed**.
- **Tick anchoring:** if labels sit *between* grid-lines, anchor ticks at label center; if
  labels sit *next to* grid-lines, use the grid-line position.
- **Units, not just values:** verify tick units (nC vs pC, V vs mV, A vs mA, °C sign),
  not only the numbers.
- **Signed quantities stay signed.** Preserve negative P-channel axes and values. If an
  extractor intentionally normalizes them to magnitudes, record that transformation
  explicitly in the values/provenance; never silently turn negative quantities positive.

## 3. Crosshair & local-region inspection (5× default; 8× when ambiguous)

- **MANDATORY axis-integrity inspection before GREEN:** verify the detected plot box matches
  the source's real grid rectangle (no over-extension into whitespace/neighbors and no clipped
  edge); verify both axes have resolved tick values and units; and verify every rendered tick
  crosshair is anchored on its own axis, with no floating, duplicated, or corner-snapped ticks.
  If any of these three checks was not performed, the item is UNVERIFIED rather than GREEN.
- **An unchanged primary value does NOT exempt the box check.** A plot-box edge change
  (`plot_right`/`plot_bottom`/`plot_left`/`plot_top`) with a correct, unchanged Vpl is *still*
  a box-integrity defect and must be inspected — never classify a box-edge change as "neutral"
  because the value did not move. This is the exact trap that let box-capture regressions pass:
  the served scalar stayed right while the box grew into a neighbor panel or dead whitespace.
- **Own-frame vs neighbor-panel discrimination.** The plot box must end at the panel's OWN
  printed frame. A genuine frame may extend up to one unlabeled interval past the last labeled
  tick — that is legitimate, not an overshoot. But the box must NEVER cross an inter-panel
  whitespace gap into a neighbor chart, nor run past its own solid frame line into blank margin.
  A right-side axis is *not* automatically a same-chart dual axis: a legitimate dual axis has
  gridlines continuous across the plot with no inter-panel gap, whereas a separate neighbor
  panel is set off by a whitespace gap and carries a distinct quantity/chart type (e.g. a
  `C(pF)` capacitance axis beside a `VGS` gate-charge plot). Confirm continuity before calling
  a right-edge expansion legitimate.
- Inspect **crosshairs** at 5× — the crosshair must center on the actual grid intersection,
  not a nearby pixel. Escalate to 8× only when dense log-grid lines, antialiasing, or a
  suspected 1–2 px offset remains ambiguous. Inspect both axis ends and at least one interior
  tick; on log axes, keep adjacent grid-lines visible so an off-by-one-line error cannot look
  plausible.
- Pair the visual check with a **programmatic exact-center assertion** for the rendered
  marker coordinates. Either check alone is insufficient.
- **The exact-center assertion must certify the mapping used for served data, not only the
  overlay renderer.** If the value-to-pixel / pixel-to-value calibration misses a consumed
  printed tick or grid intersection, moving just the drawn crosshair onto the observed tick is
  a mute-button fix: the review marker becomes plausible while the extracted values remain
  miscalibrated. Assert the served calibration at every consumed observed tick center, and
  re-fit or fail closed when it misses tolerance. On log grids, constrain observed-tick matching
  by the labeled major-tick sequence so a nearby minor gridline cannot be substituted.
- **Exact centering does not prove semantic tick identity.** Bind each consumed value to nearby
  label/fit evidence before using grid regularity to resolve ambiguity. Endpoint glyphs may be
  shifted inward for legibility, a log axis may terminate at a non-decade value, and the frame
  may legitimately extend beyond the last labeled tick. Do not force a mathematically uniform
  sequence by snapping such a label onto an interior minor line; verify the label-to-line
  association with adjacent gridlines visible, or fail closed.
- **Do not median-collapse conflicting duplicate tick evidence.** When OCR/text finds the same
  semantic tick value more than once on one axis, the candidate centers must agree within the
  asserted spatial tolerance before they are collapsed. Spatially distinct duplicates can be
  a neighboring label, annotation, or second panel; treating their median as a real tick
  fabricates both the calibration and its exact-center proof. Preserve one agreed center or
  fail closed on the axis conflict.
- **Do not serve an axis by extrapolating across multiple unconsumed endpoint intervals.** A
  printed frame may legitimately extend one unlabeled tick interval beyond the first or last
  consumed label, but a calibration whose evidenced tick sequence leaves two or more endpoint
  intervals unseen is incomplete. This commonly happens when `1K`/`10K` labels or early linear
  ticks were not parsed. Recover the missing semantic ticks or fail closed; exact interior
  centers do not make long endpoint extrapolation trusted.
- **A consumed tick outside the detected plot box is a box/axis-ownership failure**, even when
  its label and fitted marker agree with each other. Extend the box only when the tick belongs
  to the panel's own evidenced frame; otherwise reject the foreign tick/panel binding. Never
  hide the mismatch by clipping or relocating the marker to the box edge.
- **MANDATORY if intersecting curves exist:** Inspect **every curve intersection point**
  at 5× upscale before a GREEN verdict. Verify that each curve's extracted points stay on
  their own source stroke through the approach and intersection; the digitizer must not jump
  between crossing curves or snap onto the neighbor branch. Missing this inspection means
  UNVERIFIED, not GREEN.
- Inspect **curve-hits-border** points at 5×, escalating to 8× when ambiguous — the digitizer
  must stay on the curve without snapping to borders, grid-lines, or **annotation/label
  leader lines**.
- Inspect each curve at **both ends + a couple of interior points**, not only at
  intersections/borders.

## 4. Curve identity / series binding  *(the #1 real-defect source)*

For any multi-curve chart, verify *which curve is which*, not just that curves exist:

- **Ordering invariants** hold where they are physically universal: Ciss >= Crss and
  Coss >= Crss. Ciss and Coss may cross or coincide, especially in shared low-VDS regions,
  so their relative ordering must not assign identity by itself. The source legend is
  authoritative. Normalized RDS = 1.0 at the reference temperature; the hotter transfer
  curve turns on at **lower** Vgs (Vth falls with T).
- **Labels can be inverted or swapped** (temperature, Vgs). The physical ordering (e.g. which
  Vgs curve is steeper vs temperature) is often **non-monotonic**, so it cannot be inferred
  from a rule of thumb.
- **On any identity ambiguity, render the source datasheet PDF** and read the printed
  legend/colors:
  `pdftoppm -png -r 200 -f <page> -l <page> <part>.pdf <out>` — decisive.

## 5. Trace fidelity to ONE physical curve

- **No branch-switching** between multiple VDD/VDS curves — the trace follows *one* branch
  end-to-end. At every intersection, verify at 5× upscale (§3) that each curve stays on its
  own source stroke; a curve that snaps onto the neighbor branch is RED.
- **No spurious oscillation / sawtooth** and **no top-edge switchback** — the trace should
  trim at the first axis-top reach, not wander.
- **Single-valued according to the declared output contract.** Id(Vgs), Vgs(Qg), C(V), etc.
  are normally single-valued functions of their declared x-variable. A near-vertical or
  fold-back segment is RED only when it departs from the visible source stroke or violates
  that declared `y(x)` contract. Datasheet source curves may genuinely be near-vertical or
  artistically stylized; source-faithful geometry is not an extraction defect. Monotonicity
  does **not** catch a false vertical snap (it can remain monotonic-non-decreasing), so compare
  the segment directly against the source and check for grid-line, neighboring-curve, border,
  or **annotation leader-line** capture (the "Tj = 25 °C" pointer, etc.).
- **Feature-region shape fidelity.** The extraction must reproduce the *characteristic feature*
  — the near-threshold **knee** (transfer), the **Miller plateau** (gate charge), the low-V
  rise (C-V) — not straighten it, clip it, or cut the corner. Verify the trace hugs the source
  **through** the feature, where fidelity matters most; a linearized knee/plateau is a defect
  even when endpoints match.
- **Monotonic where physics demands it** (gate-charge Vgs(Qg) is monotonic; a notch/dip is an
  artifact). Check monotonicity on a **coarse grid**, not per-pixel — raster noise will
  false-alarm otherwise.
- **Full-span coverage:** does the trace cover the whole source curve or stop short?
  (Low-V truncation is common.)
- **Shared/coincidence zones:** where two curves genuinely coincide (e.g. Ciss≈Coss at low
  VDS), that region must be **marked as shared, not silently omitted**.
- **Raw-point fidelity:** compute review metrics from the original validated source-point
  sequence, before any duplicate removal, median collapse, optimizer resampling, smoothing,
  or fitting. Record both raw-source and unique/optimizer point counts. Report median and p95
  source-stroke distance normalized by stroke width or plot size; never let a deduplicated or
  fitted proxy hide divergent raw points.

## 6. Physical-plausibility & value-magnitude sanity

Check the *extracted numbers*, not just pixels:

- **The datasheet source is authoritative for extraction fidelity.** Physical plausibility is
  an alarm that triggers closer source inspection; it is not permission to alter or reject a
  source-faithful extraction merely because the printed curve is stylized or incompatible with
  a preferred physical model. In that case the extraction may be GREEN while the downstream
  fit/model must explicitly refuse or report limited applicability.

- **Value magnitude vs part class** — e.g. Ciss ~nF for a large MOSFET, not 90 pF. A magnitude
  mismatch is what exposes a wrong axis-decade calibration that pixel checks pass.
- **Reference/unity points:** normalized charts pass through their reference (RDS=1.0 at
  25 °C); Vpl in a physically sane band; expected sign of temperature coefficient.
- **Out-of-own-axis values must fail-closed.** A primary scalar that lands outside the chart's
  own plotted axis range (e.g. a Vpl above the VGS axis maximum, or an off-axis extrapolated
  value) must be refused — `status` not `ok`, value nulled — never served. An extractor that
  reports such a value with `status=ok` is a §0 fail-closed violation, even if a diagnostic
  guide line is still drawn on the overlay.
- **Table-derived scalars need value-column ownership.** Qoss, Co(er), Co(tr), Ciss, Coss,
  Crss, and similar references must come from an evidenced value/typ column or another
  explicitly recorded source cell — never from the first nearby number. Conditions such as
  `VDS=15 V`, `VGS=0 V`, `ID=...`, `f=1 MHz`, and temperature are not values. Prove ownership
  by source cell/token, not by numerical inequality: a true value may legitimately equal a
  condition number. If the parser proves it captured a condition but cannot recover a value,
  emit null plus a visible per-symbol refusal diagnostic; do not silently drop the evidence or
  serve the condition. Keep an independent graph-vs-table inconsistency check active after a
  parser fix — correcting the reference must not mute a genuine disagreement.
- **Cross-panel consistency:** compare the same quantity across charts only after confirming
  that operating conditions, definitions, sign conventions, and units match. Under matched
  conditions the values must agree within tolerance; otherwise preserve both condition-tagged
  results rather than false-REDing a legitimate dependence (e.g. Qrr changes with IF, di/dt,
  temperature, and gate drive).

## 7. Crop & provenance completeness

- **No neighbor-figure bleed:** no adjacent figure title or neighboring axis inside the frame
  (e.g. a Ct(pF), BVDSS, "Normalized", or "Figure N ..." from the next panel).
- **No clipping:** axis titles and edge ticks fully present, plot box complete.
- **Frozen review provenance:** bind every verdict to all of the following:
  - source PDF SHA-256;
  - extractor commit plus extractor-source/content SHA;
  - values JSON SHA-256 and overlay SHA-256;
  - page number, full source diagram string, source/panel bbox, and calibrated plot box.
  Any changed input or artifact invalidates the prior verdict unless its relevant content hash
  is byte-identical. Paths, timestamps, or filenames alone are not provenance.

## 8. Verdict

- Item-specific **GREEN** or **RED** with a **concrete defect** (curve, region, and what is
  wrong). RED anything that fails §0–§7 or that you cannot positively verify.
- **Keep the item verdict separate from the patch-delta verdict.** A code change may be
  GREEN/non-blocking because it preserves or strengthens a fail-closed contract while the
  underlying chart extraction remains RED (for example, a wrong trace that stays
  `unverified`, emits no physical points, and leaks no scalar). Record both verdicts
  explicitly; never turn the chart GREEN merely because the patch did not make it worse.
- **Agent review NEVER sets `human_verified`.** GREEN means "no blocking defect found by the
  agent," still pending human verification.
- Map review outcomes to backlog state consistently:
  - trace, axis, panel-binding, or calibration defect → `gap`;
  - extraction sound but overlay/review evidence incomplete → `needs_annotation`;
  - complete, agent-GREEN overlay → reviewable/READY, never automatically human-GREEN;
  - missing or unevaluable evidence → `unverified`, with the missing evidence named rather
    than silently treating it as `gap` or `OK`.
- **Independent reviews stay independent.** Each reviewer inspects the exact artifacts before
  reading another reviewer's verdict. Only then compare results. Do not expose one verdict in
  the second reviewer's prompt.
- Write a per-item review-JSON record beside the packet with a one-line reason and the complete
  §7 provenance fields, or an immutable hash reference to a complete §7 provenance record.
  A review record containing only the PDF, values JSON, and overlay hashes is incomplete.

## 9. Shared-extractor change acceptance

These checks apply when shared, data-dependent extraction code changes and the change is
claimed regression-free across a corpus. A focused fixture packet proves the named fixes; it
does **not** bound collateral behavior.

- **Run the complete affected corpus with the authoritative production harness and selection
  contract.** Do not substitute a convenient candidate sort, a bounded sample, or a hand-built
  driver. Record the exact command, corpus-list SHA-256, extractor source SHA-256, dependency
  lock/environment identity, output-manifest SHA-256, row count, exception count, and
  `no_result` count.
- **Calibrate the production call path, not a stricter helper that production bypasses.** A
  helper-only test does not certify the extractor when the real finder still calls a legacy or
  parallel implementation. Exercise the public/production entry point on every load-bearing
  known-good and known-bad fixture, and remove or explicitly reconcile duplicated policies.
- **A skipped load-bearing corpus fixture is missing evidence, never a pass.** Acceptance commands
  must set the required corpus/data environment and report skips. If a real-PDF regression test
  is skipped because its source root is unset or its PDF is absent, the affected gate remains
  UNVERIFIED until that exact fixture runs.
- **Use a same-environment back-to-back A/B to establish causality:** same host, interpreter,
  virtual environment, dependency versions, DPI/OCR settings, and corpus. Put each source
  revision in an isolated detached worktree and pin `PYTHONPATH`/cwd to that worktree. Never
  `git stash` or swap bytes in a shared dirty worktree for an acceptance run. Run the two sides
  sequentially when OCR/render subprocesses have timeouts or compete for CPU/memory; concurrent
  A/B runs are valid only when their resource budgets are isolated and repeatability is shown.
  A correlated timeout/resource-pressure mode is environment drift, not causal code evidence.
- **Freeze identical finder/index/crop inputs and exact source identity for both sides.** Key a
  chart by source-PDF path/hash plus crop path/**content SHA-256**, page, diagram, panel/crop bbox,
  and render settings—not merely by part text or a crop pathname. Part text can collide across
  native, `.gs`, `.cups`, or other transformed variants, and paths alone are not provenance. Keep
  the production finder part and table-binding semantics unchanged during the A/B; carry logical
  aliases as external provenance. Record alias collisions, input-table hashes, and a canonical
  crop-set manifest hash. **Assert every frozen input hash again when each A/B side consumes
  it and fail closed on any mismatch**; a hash that is merely recorded but never checked does
  not prove identical inputs. Regenerating finder crops independently per side or silently
  substituting a variant table introduces a second variable and invalidates the causal comparison.
- **A stale or cross-environment baseline is not causal evidence.** First reproduce the
  baseline revision in the current A/B environment. If a same-source rerun differs from the
  stored baseline, classify that difference as environment/baseline drift and keep it separate
  from the code-change delta; do not call it an improvement or regression caused by the patch.
- **Diff the full selected manifest, not only the primary scalar or two box edges.** Compare
  status, diagnostics/confidence, physical-output contract, scalar, raw curve, panel/page and
  source diagram, all crop/plot-box edges, ticks/units, and provenance. Every changed field and
  every `no_result`/exception requires an item-specific verdict. A delta that leaves Vpl (or
  another primary value) unchanged is still a delta.
- **Inspect every real A/B delta under §0–§7.** In particular, every plot-box or tick change
  gets the §3 own-frame/neighbor-panel check, and every panel/crop/provenance change gets the §1
  and §7 source-identity check. Acceptance requires zero unexplained regressions; agent GREEN
  still does not set `human_verified`.
- **Re-audit downstream consumers of changed reference evidence.** A table/reference-parser
  change can alter curve assignment, identity, or crossing geometry even when it does not edit
  the trace extractor. When anchors or reference residuals feed curve selection, compare the
  selected identity, raw per-curve points, shared spans, and every intersection artifact—not
  only the corrected scalar. Mandatory crossing charts require the §3 microscopic gate; if a
  prior human-verified crossing artifact moves, human re-verification is required.

---

# Overlay-generator requirements (the review artifact)

The overlay is what the reviewer gates on, so it must make every defect class above
**visible**. Baseline: draw the axis overlay with a crosshair at each detected tick value,
draw the tick values for review, and draw the digitized curves on top of the chart. Add:

- **Keep the source stroke visible under the extraction.** Draw the extracted curve as
  **discrete sample points (markers), not only a smoothed line**, and/or thin/semi-transparent
  — a thick opaque trace *hides* a floating or mis-tracking extraction (a trace that sits a
  fraction above the true curve is invisible if it covers it). The reviewer must be able to
  see the extracted points land **on** the source stroke.
- **Label each extracted curve with its bound identity** at the curve end (Vgs=…, temperature,
  Ciss/Coss/Crss, etc.), and **echo the source legend** on the frame so identity and the
  printed legend can be compared directly (this is what exposes swapped/inverted labels).
- **Use an extraction color distinct from the source colors** (e.g. source black/red →
  extraction blue/magenta) so extraction vs source is never ambiguous.
- **Draw derived marker lines**: the Vpl plateau line, the reference-unity line (e.g. RDS=1 at
  25 °C), crossover markers — so placement is checkable at a glance.
- **Print the key scalars + confidence flags as a caption on the image**: Vpl (or the primary
  value), score, `trace_source`, `status`, axis model (**log/linear + decade span**),
  unity@ref, monotonic flag, and any warnings. This surfaces the fail-closed signals (§0)
  right on the artifact.
- **Show the axis calibration explicitly**: draw the fitted plot-box rectangle and, for
  **dual-axis charts, both y-axes** with which axis each curve is bound to.
- **Tick values small and INSIDE the axes** — do NOT overprint the source's own tick labels
  (overprinting produces doubled/illegible labels and hides misregistration).
- **Content-aware crop around the complete chart:** include the plot plus its own axis titles,
  edge ticks, legend, caption, and conditions box — never crop to only the bare plot rectangle.
  Prefer cropping away neighbor content. Masking can conceal a wrong panel box, so permit it
  only outside the calibrated panel, record the masked region explicitly, and leave all panel
  evidence visible.
- **Mark trace start/stop endpoints vs the source curve extent** (to expose truncated span),
  and render **shared/coincidence zones distinctly** (e.g. alternating dashes + a
  "Ciss=Coss shared" label) rather than dropping one curve.
- Keep 5×/8× crosshair-inspection crops as a **separate internal artifact**, not on the
  human-facing overlay — keep the human overlay clean.
