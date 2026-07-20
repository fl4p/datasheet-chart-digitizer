# AIMDQ75R004M2H random-PDF iteration

**Status:** in progress. No commit/push. Agents do not set `human_verified`.

## Frozen input

- Source: `infineon/AIMDQ75R004M2H.pdf`.
- SHA-256: `b95eb2c3a9ae529a84c78eac568899b3611614d09c5977ed1205fecb29b214c1`.
- Initial annotation exposed two independent defects on page 11:
  - Diagram 14, `Drain-source breakdown voltage`, was rejected despite a single valid
    full-span source curve;
  - Diagram 16, `Typ. Coss stored energy`, was routed into the three-trace capacitance
    digitizer and failed there.

## Breakdown fix contract

- Keep the exact-one full-span curve gate and the 50% visible-x-span gate.
- Clip only actual connected PDF source edges to the evidenced plot rectangle; never bridge
  disconnected fragments or relax the shared capacitance-vector heuristics.
- Densify a connected two-vertex source edge after clipping so the exported curve is not just
  two endpoints.
- A source edge that protrudes slightly past a vector-proven frame records that clipping as
  provenance; it is not mislabeled as a raster-frame warning.
- Table anchoring may fall back to the exact `Drain-source breakdown voltage` table-row label
  when PDF extraction drops the subscripted `V(BR)DSS` symbol, but still requires a structured
  dash-slot value. Narrative prose without value-column structure stays refused.

Expected target values: `Tj=-50..175 °C`, `VBR≈822.5..875.0 V`, `VBR(25 °C)≈840 V`,
slope `≈233 mV/K`, and table minimum `840 V` from page 6.

## Stored-energy classification contract

- `Coss`/output-capacitance energy charts remain detectable as `coss_energy` provenance.
- They must not be classified as `capacitances` or reach the Ciss/Coss/Crss digitizer.
- The neighboring Diagram 15 capacitance panel, its crop, points, and overlay must not move.

## Acceptance

1. Positive real-geometry fixture: one diagonal source edge protruding 2.2% beyond both frame
   sides clips to full coverage and becomes a dense trace.
2. Negative fixtures: short interior line, off-frame annotation, zero candidates, and two
   full-span components remain refused.
3. Run the authoritative full breakdown corpus A/B and inspect every trace/status/box delta.
4. Run the authoritative full finder A/B; only Coss-energy routing deltas are expected from the
   classifier slice, with zero movement in supported neighboring panels.
5. Freeze a deterministic annotated PDF, `qpdf --check`, and an independent checklist review
   of Diagram 14 at microscopic scale plus the page-11 panel routing.
6. Fab supplies the human verdict and any commit/push authorization.

## Frozen v3 progress

- Annotated PDF:
  `/private/tmp/dsdig-random-aimdq75r004m2h/v3/AIMDQ75R004M2H-with-digitized-curves.pdf`,
  SHA-256 `c7a7c70fe152c23de9537d9477cd5ae851855e71ade755459daeff76acd3e4be`.
  A separate rebuild is byte-identical and `qpdf --check` passes.
- Diagram 14 is `verified`, embedded, warning-free, and records
  `source_trace_clipped_to_verified_vector_frame=true`. It has 633 points over
  `-50.03..175.10 °C` and `822.55..874.91 V`; `VBR(25 °C)=839.96 V`, slope
  `233.0 mV/K`, and the page-6 minimum is `840 V` under matching conditions.
- Diagram 16 is `coss_energy`, not a capacitance digitizer input, and is unpainted. Diagram 15
  remains `capacitances` and embedded.
- Independent breakdown item/focused-patch review is GREEN (`human_verified=false`):
  `/private/tmp/dsdig-random-aimdq75r004m2h/v3/reviews/`
  `agent-breakdown-diagram14-001.codex-ee-hxy-agent-review.json`, SHA-256
  `ef45b34cc37000a4daf8ee98b03eb70cfa017e54b59984ab1e35ae3e2d8a3489`.
  Source-stroke distance is `0.207 px` median / `0.391 px` p95; tick round trips are below
  `0.001 px`. Finder/classification review and corpus gates remain open.
