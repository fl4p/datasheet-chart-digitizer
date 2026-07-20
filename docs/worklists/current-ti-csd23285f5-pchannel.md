# TI CSD23285F5 P-channel chart coverage

## Scope

Random-loop fixture: `ti/CSD23285F5.pdf`. This is a P-channel counterpart to
the TI small-package layout. Its supported real charts are:

1. Figure 5-2 transfer characteristics;
2. Figure 5-4 gate charge;
3. Figure 5-5 capacitance;
4. Figure 5-8 normalized on-state resistance versus temperature; and
5. Figure 5-9 body-diode forward voltage.

Figures 5-1, 5-3, 5-6, 5-7, 5-10, and 5-11 are unsupported and must remain
unpainted.

## Baseline defects

- Transfer finds only two of three source curves because the cold branch is a
  genuine full-height but narrow-VGS run just below the fallback x-span gate.
- RDS(T) refuses because its legend parser accepts only unsigned `VGS` labels;
  the source prints `-1.8 V`, `-2.5 V`, and `-4.5 V`.
- Body diode extracts two curves but finds zero temperatures because the native
  text layer emits `25qC`/`125qC` rather than a degree glyph.
- All printed P-channel magnitude axes (`-VGS`, `-IDS`, `-VDS`, `-VSD`,
  `-ISD`) require explicit source-sign and magnitude-transform provenance.

## Constraints

- Follow `transfer-signed-semantics.md`: no silent sign loss. Every magnitude
  representation records the printed signed quantity and the transform.
- Do not infer a curve from P-channel shape alone. Preserve the same local
  panel, axis, legend, and full-span evidence required for N-channel charts.
- Lower a run-span threshold only with a full-height source-stroke guard and an
  exact expected-count/identity check; legend samples and grids remain refused.
- Temperature OCR variants are accepted only in owned legend context.
- Unsupported families stay unpainted and ambiguous signed evidence fails
  closed.

## Acceptance

- Exactly the five supported panels are detected. All five overlays own one
  source panel and follow every printed branch.
- Transfer recovers exactly three curves and binds `-55/25/125 °C` without
  branch switching through their high-current convergence.
- RDS(T) binds all three printed negative gate-voltage series while storing
  positive magnitudes only with explicit round-trip provenance.
- Body diode binds both temperatures and both printed magnitude axes.
- Gate-charge and capacitance keep their already-correct curves and gain the
  same explicit P-channel provenance without point movement.
- Add positive, unsigned-negative, and ambiguous-sign refusal fixtures. Freeze
  a same-environment repeat, run focused suites, `qpdf --check`, and obtain an
  independent agent review.
- Terminal landing remains blocked on authoritative affected-corpus A/B for
  each shared extractor path. Agents never set `human_verified`.
