# FDPF190N15A capacitance trace-fidelity worklist

**Status:** v1 packet `8ef03035bc3d...` is withdrawn.  Replacement v2 packet
`8c1a6ad9217f...` is independently patch-GREEN in both agent lanes, but all
three recovered chart items remain UNVERIFIED pending exact tick-center
assertions; FDPF190/FDPF390 also retain a near-axis-top item gate.  The clipped
Qoss contract is handled separately by `cap-qoss-clip-contract.md`.  V2 is
integration-tested **on top of** cap-anchor parser v1, because
recovering a chart can expose a legacy condition-as-anchor reference that the parser fix is
responsible for refusing. Do not mix this trace fix into that parser packet, commit, push, or
set `human_verified`.

## 1. Defect

The frozen cap-anchor A/B exposes one raw-point/overlay delta on
`onsemi/FDPF190N15A`, page 4, diagram 5. Both sides correctly remain
`status=unverified`, `physical_output_available=false`, and emit no Qoss physical reference.
The parser change removes a bogus Ciss=25 pF anchor captured from `VDS=25 V`; conflicting
strongest table values 2020 and 2685 pF are explicitly refused.

The candidate trace is still source-unfaithful:

- Coss follows the source decline only to roughly 20 V, then snaps onto/rides the ~900 pF
  horizontal grid/source-neighbor region instead of continuing down the printed Coss curve
  toward ~100 pF at the right edge.
- Crss is nearly flat around 20 pF while the printed Crss continues declining toward ~10 pF.
- The retained initial Ciss/Coss shared span is plausible, but later separation must remain on
  distinct source strokes.

Review artifacts are frozen in
`cap-anchor-parser/v1/review/FDPF190N15A/`. The candidate is an honest safe refusal, so this gap
does not block the table-anchor parser; it does block any future physical C(V)/Qoss output for
this chart.

Read-only diagnosis in `DIAGNOSIS.md` isolates the upstream cause: production truncates the
plot at y=418 although the source's evidenced solid bottom frame is at y=513. The truncated box
makes the otherwise-correct vector Crss path appear only 77% wide, rejects vector extraction,
and sends the chart into a raster fallback dominated by black gridlines. With the evidenced
full box, the unchanged vector extractor returns source-faithful Ciss/Coss/Crss at ~96% span.

## 2. Required direction

1. Recover the plot's OWN closed frame using positive raster evidence: the solid bottom frame
   plus both side rails. Do not globally replace the median with the maximum line end, relax the
   vector 0.90 span gate, or accept a nearby internal gridline. If frame ownership remains
   ambiguous, keep the item refused.
2. Re-run the existing vector extractor on the corrected frame before changing raster tracking;
   the current vector paths are already source-faithful with the correct box.
3. Track each curve against its own source stroke through the shared low-VDS region and the later
   separation. A horizontal gridline or neighboring curve cannot become a bridge anchor.
4. Apply the mandatory 5× intersection/approach inspection and 5× border/gridline inspection.
5. Preserve the fail-closed contract until all three curves are source-faithful and the axis is
   trusted. No derived Qoss/Co(er)/Co(tr) scalar may leak while unverified.
6. Keep `capacitance_traces.py` below the project 1,500-line limit. The positive frame-evidence
   helper belongs in the reusable `capacitance_plot_box.py` module rather than enlarging the
   already-near-limit trace module.

## 3. Fixtures and acceptance

- Positive fixture: FDPF190N15A source crop, with Coss continuing monotonically down its own
  source stroke after ~20 V and Crss following the lower stroke to the tail. The corrected plot
  box must bind to y=513, not y=418.
- Known-bad fixtures: current candidate Coss horizontal ride and current baseline multi-span
  Ciss/Coss collapse; both guards must fire. Add a synthetic/real inner-horizontal-grid fixture
  where vertical strokes continue below the line, proving it cannot masquerade as the bottom
  frame.
- Negatives: the Class-C clean single-crossing charts (PSMN2R4, PSMN5R3, PSMN6R1, PSMNR70) must
  remain byte-identical or receive full microscopic re-review. Include a neighboring-panel and
  blank-whitespace negative so a deeper frame cannot over-expand.
- Because the trace code is shared, run the full frozen capacitance corpus under checklist §9.
  Inspect every points/overlay/identity/shared-span delta, not only FDPF190.
- The first frame-only 800-panel A/B changed exactly seven charts while preserving 455 results,
  345 stable errors, and all frozen inputs. Three charts recovered source-faithful vector traces:
  FDPF190N15A, FDP039N08B-F102, and FDPF390N15A. FDPF33N25T stayed honestly unverified. Three
  NCE charts (NCEP050N12D, NCEP065N10GU, NCEP60ND60G) stayed unverified/physical-output-false;
  they are separately finder-misbound to SOA panels. Their finder bug is not part of this patch,
  but the taller box must never make them serve physical output.
- The frame-only A/B is not sufficient for freeze: FDPF190 and FDP039 expose old bogus Ciss
  condition tokens (25 pF and 40 pF). The load-bearing A/B baseline is cap-anchor parser v1, and
  the combined candidate must keep those references null/refused while recovering only the
  source-faithful curve points.
- The corrected combined A/B satisfies that gate: baseline manifest `8833837f...`, candidate
  manifest `9cd981e4...` (byte-identical repeat), exactly three expected deltas and zero negative
  deltas. The three NCE wrong-panel negatives are byte-identical to the cap-anchor baseline after
  requiring side rails to start at the detected plot top; the outer crop border can no longer
  masquerade as a deeper frame.
- Acceptance requires trusted axes, source-faithful Ciss/Coss/Crss, no false shared spans, no
  contract leak, dual independent agent GREEN, and Fab's microscopic gate. Agents never set
  `human_verified`.

## 4. Terminal item gate

Patch-GREEN does not make the three recovered items consumable. Closure requires an artifact
that asserts every consumed tick crosshair is at the observed printed tick center; a microscopic
source-stroke completeness check resolving `near_axis_top` for FDPF190N15A and FDPF390N15A; and
the explicit clipped-Qoss refusal contract from `cap-qoss-clip-contract.md`. Fab must give the
terminal item verdict after those artifacts are frozen. Until then FDP039N08B-F102,
FDPF190N15A, and FDPF390N15A remain UNVERIFIED.
