# Capacitance Coss source-support fail-close

Status: **scoped landing-GREEN; fresh full re-extraction held by unavailable Git-LFS source objects**

## Scope

Fail closed when a raster capacitance trace is smooth and numerically plausible
but is not seated on source ink, or when extracted `Ciss` and `Coss` share one
source stroke while a second continuous source branch is left unused. This is
the crossing/cliff defect class from the capacitance sweep; it does not attempt
to invent a replacement trace.

Primary positives:

- `STWA57N65M5` Figure 9: Coss takes a 25-column diagonal shortcut through a
  sharp source cliff; the served line is absent from the source for a material
  run.
- `STB80N20M5` Figure 7: on the current extractor, Ciss and Coss share the same
  upper source stroke for 223 columns while a distinct middle Ciss source branch
  is orphaned for 197 consecutive columns.

Source-faithful controls:

- `STL210N4LF7AG` Figure 7: a small crossing notch remains source-seated and has
  no material absent/orphan run.
- `FDMS86202ET120` Figure 8: genuine low-voltage Ciss/Coss convergence uses one
  source stroke and later separates; there is no orphaned source branch.

## Evidence and contract

Frozen packet: `/private/tmp/dsdig-cap-source-support-v1/`.

- STB source PDF is the preserved official binary
  `STB80N20M5.pdf_downloads/stb80n20m5.pdf`; the canonical library entry was an
  external 131-byte Git-LFS pointer during this slice.
- STB candidate/repeat manifest SHA-256:
  `a8e8d66360c8f5b240ca9831dc3550da734785ef4b8d1df975fe7b709a3e9d76`.
- STWA batch candidate/repeat manifest SHA-256:
  `a4c5222e05c223e8210aeb477589cdbcd001ed3d6b030e16183c111d14ec50f9`.
- FDMS control candidate/repeat manifest SHA-256:
  `d0dd422692e87b100683e50de5ec138abd8a596469f663b46895c2256388a9c0`.
- STL control candidate/repeat manifest SHA-256:
  `57a76c4eeefa49071305d7feae20569791ef3fccf70fa5dd44db8b68dfb334ba`.

The fail-close has two independent raster-only gates:

1. a served trace has a consecutive material run farther than 3 px from dark
   source ink, using a one-column x neighborhood; and
2. within a recorded shared Ciss/Coss span, a material continuous source center
   remains farther than 5 px from all three assigned traces.

Thresholds are width-scaled with conservative floors: at least 8 columns / 1.5%
of plot width for source absence, and at least 12 columns / 3% for an orphaned
branch. In the frozen 68-chart raster calibration set, the two corner-cut
positives measured 20 and 25 absent Coss columns while all other charts measured
at most one; source-faithful shared controls measured at most eight orphan
columns while defect cases measured 12--140.

Both gates only change review/serving status. Curve points and overlays remain
diagnostic evidence, while physical columns and Qoss scalars are withheld by the
existing fail-closed contract.

## Tests

Focused tests currently pass: 82 tests covering synthetic source-seated curves,
sharp-cliff shortcuts, orphaned shared branches, genuine shared source ink,
validation propagation, vector non-applicability, and the existing crossing
repair/trace validation suite.

The repository-wide test command reached 567 passed and 39 skipped, with 72
failures and 46 collection/setup errors. Those failures are PDF-dependent and
stop at 131--132-byte Git-LFS pointer files (`pymupdf.FileDataError: no objects
found` or `pdftotext` failure), before exercising the changed code. The exact
LFS object requested by the frozen harness returns HTTP 404 from the configured
remote, so this slice does not call that repository-wide run green.

## Corpus A/B

The exact fresh authoritative re-extraction is held at input validation: 564 of
the 586 canonical source paths in the frozen corpus manifest now contain
Git-LFS pointers rather than the PDF binaries, and the configured LFS remote
does not have the requested objects. The other 22 paths are transformed
derivatives, not substitutes for the missing canonical inputs. No generated PDF
was substituted for an authoritative input.

Because this slice only adds validation over unchanged raster points and crops,
the complete prior authoritative result was also checked post-extraction:

- 232 raster rows recomputed from their exact frozen crop and served points;
- 227 vector rows remained outside this raster-only gate;
- 60 rows gained a more specific source-support reason;
- 51 were already suspect for an independent reason;
- 9 moved from `pass` to `suspect`, producing 7 new physical withdrawals; and
- all 9 newly suspect source/overlay pairs were inspected and confirmed as real
  source-absent shortcuts or orphaned branches, with zero false positives.

Candidate and repeat canonical payloads were identical at SHA-256
`ae66e86ed98387c1e126cf7989d0e4c5da52eca33103c724be40dd195eccd10f`.
The full post-extraction report is
`/private/tmp/dsdig-cap-source-support-v1/full-post-extraction-ab.json`
(SHA-256
`cf029f4f199b41ba9e65e7dd89d71295280df30e9a46076d546800d4f8557f60`).
The independent recomputation recipe is frozen alongside the packet.

Independent review verdict:
`SCOPED_LANDING_GREEN_FRESH_FULL_REEXTRACTION_HELD`, with no code defect and
all four bounded items plus all nine corpus transitions passing source/overlay
review. The reviewer independently reproduced the 232-row digest exactly.
Review SHA-256:
`758bc3bd69dce706be20d6c42be43fd56decc235857fc9a5782c606aa57bbfbc`;
`human_verified=false`.

## Remaining gate

- Re-run the authoritative harness from source once the exact Git-LFS PDF
  objects are available. Until then, the full source-to-result gate is held and
  `human_verified` remains false.
