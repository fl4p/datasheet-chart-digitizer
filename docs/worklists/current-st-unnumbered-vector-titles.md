# ST unnumbered short titles above vector plots

**Status:** focused patch agent-GREEN; shared-finder acceptance remains held on
the authoritative full-corpus A/B, human scale review, and baseline isolation of
one broader Toshiba bbox test failure. No commit/push and
`human_verified=false`.

## Defect

STD30NF06 page 4 contains a two-by-three matrix of charts whose short titles
are plain, unnumbered text above each plot. PDF line extraction merges the two
columns, so the normal numbered/typical-caption detector emits no candidates.
Four supported charts are consequently absent: transfer, static RDS(on), gate
charge, and capacitance. Output and transconductance are deliberately outside
the current digitizer scope.

Nearby older ST parts already detect their comparable captions, so this is a
narrow title-promotion failure rather than permission for a broad ST template
rule.

## Guarded design

- Run the fallback only on a page with no numbered titles, caption titles, or
  gate-charge axis-label candidates.
- Recover a short supported title only when its line is directly above an own
  vector plot frame. Split merged two-column lines using the frame's horizontal
  ownership, not a title phrase alone.
- Accept stroked rectangular or quadrilateral frames under the same size,
  aspect, and stroke-width gates; quadrilaterals must themselves be rectangular.
  The STD30 gate-charge plot uses this PDF primitive.
- Use a tighter bottom label pad only for these recovered titles so the bottom
  row retains its axis labels but excludes page numbering and vendor branding.
- Keep Output Characteristics and Transconductance unbound because no supported
  digitizer owns those families.

## Acceptance

- STD30NF06 gains exactly seven panels with source titles and own frames: four
  on page 4 (transfer, rds_on, gate_charge, capacitances) and three on page 5
  (rds_on temperature, body diode, breakdown voltage). The page-5 plots were
  also absent in the baseline because their frames use quadrilateral PDF
  primitives.
- No table, schematic, unsupported chart, or cross-column crop is added.
- STD20NF06L, STP60NF06, STP80NF55-06, and STW20NM60 remain byte-identical.
- Run candidate/repeat, inspect all new crops and overlays at tick/scale level,
  and perform the authoritative full-corpus finder A/B before shared-finder
  acceptance.

## Focused evidence

- Same-source causal baseline disables only the short-title fallback and
  quadrilateral-frame admission. STD30NF06 changes from 0 to exactly 7 panels,
  with no removals or peer movement.
- STD20NF06L, STP60NF06, STP80NF55-06, and STW20NM60 retain all 14 panel rows
  and crop bytes. Candidate/repeat complete-tree hashes are identical.
- All seven new crops own one source chart and its scale labels. Independent
  review correctly rejected v4 because the two page-4 bottom-row crops included
  the page footer. In v5 both crops exclude the footer/vendor logo, retain their
  axes, and have white bottom-five-row raster guards. The exact delta remains
  seven additions, zero removals, and zero movement across the 14 peer rows and
  crop bytes; candidate and repeat trees are byte-identical.
- Independent v5 review: focused patch GREEN, shared-finder acceptance HELD:
  `/private/tmp/dsdig-st-unnumbered-v5/reviews/agent-st-unnumbered-v5-001.codex-hxy-finder-review.json`
  (SHA-256
  `2754768bf337e3a141f7813986f4b7242adeb2d96ff596575307ddb901e3fe68`).
  The remaining broader test failure is
  `ToshibaRasterImagePanelTests::test_capacitance_caption_binds_to_embedded_image_rect`
  (`x0=340.542`, expected `308.421`); it is not attributed to this slice unless
  a same-source baseline proves otherwise.
- Extraction outcomes remain separately fail-closed and are not detector
  acceptance evidence.
