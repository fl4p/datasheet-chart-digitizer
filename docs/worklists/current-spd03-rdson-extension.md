# SPD03 RDS(on) detector and digitizer extension

**Status:** IN PROGRESS. Target-only extension requested by Fab on 2026-07-19.
No commit/push. Agents do not set `human_verified`.

## Frozen input and baseline

- Source: `hxy/SPD03N50C3ATMA1-HXY.pdf`.
- Human-approved five-chart annotated baseline:
  `/private/tmp/dsdig-random-spd03n50c3/final14-annotated/`
  `SPD03N50C3ATMA1-HXY-with-digitized-curves.pdf`, SHA-256
  `e2d56df602b877f05592c219590e0387d12bfeaf9fcfdfaf77d4095a5887c6b1`.
- The rejected broad finder candidate recorded in
  [current-random-spd03-hxy.md](current-random-spd03-hxy.md) is not revived or made
  landable by this extension. Its five target overlays are target-only invariants.

## Requested charts

Add `rds_on` as a first-class detector output and digitize both source panels:

1. page 3, Figure 3, `On-resistance vs. Drain Current`:
   `ID` in A versus `RDS(on)` in mOhm, `VGS=10 V`;
2. page 4, Figure 8, `Normalized on Resistance vs. Junction Temperature`:
   junction temperature in degC versus normalized `RDS(on)`, `VGS=10 V`, `ID=2 A`.

Expected target finder result: exactly seven supported data-bearing panels (the
human-approved five plus these two `rds_on` panels). Figure 1 output characteristics and
Figures 9--12 remain unsupported and must not be admitted as `rds_on` through nearby text.

## Ownership and fail-closed contract

- Caption direction comes from the claimed chart's local x-axis evidence. Both SPD03 RDS
  captions lead their plots; an older direct RDS-temperature assumption that the plot is always
  above the caption is not acceptable.
- The selected crop must end at the panel's own printed frame and must not cross the column gap
  into transfer, body-diode, breakdown, SOA, or capacitance neighbors.
- `rds_on` requires an RDS title plus an evidenced current or junction-temperature x-axis.
- Axes must be linear, calibrated from consumed printed ticks, and within the existing residual
  threshold. Missing/ambiguous axes or trace identity fail closed.
- RDS-current must span most of the current axis and be nondecreasing within a small source-line
  tolerance. Normalized RDS-temperature retains its 25 degC unity, span, monotonicity, and local
  VGS-binding guards.
- Served curves record physical points, pixel points, axis provenance, panel/crop provenance,
  diagnostics, and exact overlay paths. An overlay alone is not proof of a valid extraction.

## Acceptance

1. Add title/classifier, caption-axis, and detector fixtures for both variants plus nearby
   unsupported negatives.
2. Run the direct RDS-temperature corpus tests and the new RDS-current tests.
3. Run the authoritative full finder corpus A/B on identical inputs. Review every real panel,
   crop, kind, title, and provenance delta under checklist section 9. The SPD03 additions are
   the named positives, not a claim that the generic detector has only two corpus deltas: every
   other evidenced RDS caption is an intended family expansion only after source-backed review.
4. Prove the previous five SPD03 panels' finder records and digitized curves are byte-identical.
5. Freeze microscopic overlays for Figures 3 and 8 and obtain two independent agent reviews.
6. Render all seven digitized curves inside a deterministic copy of the original PDF; verify a
   byte-identical rebuild and `qpdf --check`.
7. Fab reviews the new PDF. Only Fab can set `human_verified` or authorize commit/push.

## Frozen target progress

- Target finder now returns exactly the seven expected panels. Figure 3 binds below its
  caption using the split `I D (A)` axis evidence; Figure 8 binds below its caption using
  the split `T J` junction-temperature evidence.
- The Figure 3 and Figure 8 digitizations independently passed the mandatory 5x trace,
  monotonicity, tick-center, and own-frame visual review.
- Focused detector/digitizer/annotator regression: 180 passed, 8 skipped, 28 subtests passed.
- A first combined PDF was rejected because it regenerated the five previously approved
  overlays. The corrected additive build starts with the exact approved FINAL14 PDF and adds
  only the two RDS overlays:
  `/private/tmp/dsdig-random-spd03n50c3/final18-preserved/`
  `SPD03N50C3ATMA1-HXY-with-digitized-curves.pdf`, SHA-256
  `df80aaa9d942819c76ac0380d4270a98068fd0c1c6c12d4fc5788f7011fbd6ca`.
  An independent rebuild is byte-identical and `qpdf --check` passes.
- The first corpus prepass was stopped after it admitted 7,470 PDFs without requiring local
  RDS x-axis evidence. That set is rejected evidence. The revised prepass requires a unit-bearing
  `ID` or `TJ` axis, checkpoints its source/input hashes before rendering, and is running now.
- Full-corpus finder A/B and Fab's gate remain open. The additive PDF already has a complete
  independent agent-GREEN review; `human_verified` remains false.
