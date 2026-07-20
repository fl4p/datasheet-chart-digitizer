# Current random-PDF run — SPD03N50C3ATMA1-HXY

**Status:** TERMINAL REJECTED on 2026-07-19. The SPD03 target itself has the requested exact-five
finder result and a deterministic annotated PDF, but the shared finder candidate is corpus-RED
and the target capacitance reference contract is still wrong. Per this worklist's bounded-loop
rule, the iteration stops here; the rejected patch must not land. No commit/push. Agents did not
set `human_verified`.

## Terminal verdict

- Authoritative `FINAL14` finder corpus:
  `/private/tmp/dsdig-spd03-ab/panels-final14/machine.json`, SHA-256
  `ac9dc491e558560da969eea676ac2e92a5982aa2efcefae75e903e78f45c93ef`;
  14,359/14,359 rows, 58,274 panels, zero hard errors.
- Against `FINAL13`: 158 added, 881 changed, 16 removed across 920 PDFs. Against `FINAL9`,
  the overlap aggregate reports zero new cross-kind pairs and 21 removed, but source review
  proved that aggregate insufficient: same-kind records still moved onto neighboring plots.
- Independent finder verdict: **RED / DO NOT LAND**, review
  `/private/tmp/dsdig-spd03-ab/final14-finder-review.codex-hxy.json`, SHA-256
  `9df568e44c4e4254aebf290ab94274f1a23f79208379f3d65e8e550a8529284e`.
  Blocking templates include Littelfuse body-diode, NCE transfer, ST body-diode, and Infineon
  body-diode wrong-panel moves; IRFP4137 loses a real measured breakdown chart; NXP generic
  gate-charge definition schematics remain mislabeled consumable; some ST additions are only
  caption-height strips.
- Independent target/corpus split verdict:
  `/private/tmp/dsdig-spd03-ab/final14-target-corpus-review.codex-hxy-transfer.json`, SHA-256
  `deef4a299d9eada6946143ed76e1f47b10a18bb70078c7887bba747c6c579a8e`;
  SPD03 exact-five overlays agent-GREEN, full corpus RED/HOLD. It independently confirms the
  SPW52N50C3 capacitance box regresses onto a breakdown plot and two ST body-diode results are
  title-strip-only.
- The target's transfer, body-diode, gate-charge, capacitance traces, and breakdown overlay are
  source-faithful under microscopic review. However, the capacitance result still emits the
  wrong table anchor `Ciss=25 pF` by consuming the `VDS=25 V` condition; the source table says
  `Ciss typ=582 pF`. Trace sub-verdict is GREEN, item/reference-contract verdict is RED until the
  separately reviewed cap-anchor parser is integrated or the bad anchor is nulled.
- The isolated capacitance validation tip `c6bf80e` has GREEN code/tests/mechanism but corpus
  acceptance is **HOLD** because its claimed 12-delta and 24-human-GREEN over-fire manifests were
  not frozen for independent review. Review:
  `/private/tmp/dsdig-cap-flat-guard/agent-cap-flat-guard-c6bf80e-review.json`, SHA-256
  `779e211e8e771d1b20590de6b273457f3b77f6b0c743f27e7d12d1b5b71af378`.
- No next random PDF is started automatically. The deferred HFDA seed remains deferred until Fab
  explicitly opens a new bounded iteration.

## Frozen input and requested outcome

- Seed: `3849628429`.
- Source PDF: `hxy/SPD03N50C3ATMA1-HXY.pdf`.
- Detect every currently supported data-bearing chart, digitize it, and render the extracted
  curves back inside the original PDF panel.
- Recover `Typical Transfer Characteristics`, preserve the source-faithful
  `Typical Capacitance vs. Drain to Source Voltage` curves, and reject source-proven generic
  test-circuit figures as data charts. The broader standalone definition-waveform corpus remains
  isolated in its own worklist.

## Candidate scope

The candidate may change only the bounded finder/classifier/caption-geometry, transfer, and
breakdown behaviors required by this run. It must preserve panel ownership: captions, axes, crops,
and curves belong to one printed chart and may not cross an inter-panel gap.

The expected HXY result is five panels:

1. transfer characteristics;
2. body-diode forward voltage;
3. gate charge;
4. capacitance versus VDS;
5. breakdown voltage versus temperature.

## Acceptance

1. Freeze source bytes and the exact PDF/page corpus before comparison.
2. Run the authoritative full finder corpus A/B under checklist §9.
3. Re-run affected transfer and breakdown digitizers on the same candidate crops; compare status,
   panel/crop provenance, axes, temperatures, curve points, and overlays.
4. Prove the TI capacitance-as-transfer fixtures fail closed and all HXY panels remain owned by
   their printed chart. The AGM012N10LL caption/neighbor-panel fixture is also an explicit
   current-run ownership gate. [Transfer panel-type ownership](transfer-panel-type-ownership.md)
   is a current-candidate gate, not a post-run feature.
5. Inspect every real delta under checklist §§0–7, including microscopic curve fidelity where
   curves approach or cross.
6. Freeze an exact hash-locked packet and obtain two fresh independent agent verdicts.
7. Fab reviews the annotated PDF. Only Fab can set `human_verified` or authorize commit/push.

The iteration ends when its packet is accepted or rejected. Unrelated pre-existing defects become
separate worklists; they do not expand this frozen run. This loop stops at that terminal verdict.
The recorded next seed remains deferred until Fab explicitly starts another bounded iteration.

## Terminal target-only artifact (demonstrates the requested PDF, not a landable finder patch)

- Annotated PDF: `/private/tmp/dsdig-random-spd03n50c3/final14-annotated/`
  `SPD03N50C3ATMA1-HXY-with-digitized-curves.pdf`.
- Deterministic PDF SHA-256: `e2d56df602b877f05592c219590e0387d12bfeaf9fcfdfaf77d4095a5887c6b1`
  (`no_new_id=True`, consecutive builds byte-identical, `qpdf --check` passes).
- Target packet: `/private/tmp/dsdig-random-spd03n50c3/final14-annotated/target-artifact.json`
  (SHA-256 `e11071f9cd5dd8957743f4c764406be941e95fb126edfa589ad06cc6bed240fe`).
- Target finder manifest:
  `/private/tmp/dsdig-random-spd03n50c3/final14-target/charts.json`, SHA-256
  `946a227f687d7d2f8a5a9c6ac5036a3b1e8bfac662d79825f397a9a10dfb5d8b`;
  exactly five supported panels.
- Transfer containment review: `/private/tmp/dsdig-spd03-ab/`
  `final9-transfer-review.agent.json`
  (SHA-256 `625627cd1af85ab17524ba0e24eccdf82e2b1642a3d7fa3a90620090b8d9231c`),
  patch `GREEN_RECOVERY_ONLY`; 28 RED trace artifacts remain non-consumable and separately
  tracked.
- Breakdown independent review: `/private/tmp/dsdig-spd03-ab/`
  `final4-breakdown-review.agent.json`, agent-GREEN, `human_verified=false`.
- Deferred deterministic seed (not automatically started): `3849628430` ->
  `hxy/HFDA28N50F.pdf`.
