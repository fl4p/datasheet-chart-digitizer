# Annotation plugin failure isolation

**Status:** terminal agent-GREEN. No commit/push and `human_verified=false`.

## Defect

Representative ST MOSFETs such as STL260N4F7, STP270N8F7W, STW57N65M5,
STN3N45K3, and STU5N62K3 abort `dsdig annotate` when one RDS panel raises a
normal fail-closed refusal.  The process exits before writing the PDF or
manifest, so valid overlays from unrelated chart families are lost.  Gate
charge has the same uncontained per-panel exception shape.  Body-diode already
has an explicit per-panel fail-closed wrapper.

## Acceptance

- Catch RDS-current, RDS-temperature, and gate-charge failures at the smallest
  owned panel boundary available; serialize kind/page/diagram/error.
- Add a final family-level barrier for discovery/setup failures that cannot be
  attributed to a panel.  One plugin failure must never suppress other valid
  overlays, the annotated PDF, or `annotated_pdf_manifest.json`.
- Do not convert failures into accepted/review overlays and do not serialize a
  candidate physical scalar from a failed panel.
- The CLI may still exit non-zero when the manifest contains errors; the output
  PDF and manifest must already exist and be reproducible before that exit.
- Calibrate with the five ST reproductions plus clean multi-family negatives;
  freeze byte-repeat outputs, run `qpdf --check`, obtain independent review,
  and never set `human_verified`.

## Focused evidence

- STL260N4F7 run/repeat PDFs are byte-identical and qpdf-clean.
- Gate-charge p5d5 remains served and embedded; RDS-temperature p6d8 is an
  explicit per-panel refusal with no scalar, curve, or overlay artifact.
- The remaining four reported crash reproductions now also write byte-repeat,
  qpdf-clean PDFs and canonical byte-repeat manifests despite expected CLI
  exit 1: STP270N8F7W, STW57N65M5, STN3N45K3, and STU5N62K3.
- In every reproduction the exact RDS-temperature refusal remains an error or
  refused overlay, while unrelated gate-charge/capacitance outputs retain their
  original status and embedding decision. No refused RDS scalar is served.
- Independent review: patch GREEN at agent level, item remains safely
  unverified.
- Clean/control run-repeat PDFs and canonical manifests are byte-identical and
  qpdf-clean for TI CSD13201W10, Infineon IRF100B201, onsemi NDB5060L, and
  Toshiba TPHR8504PL1. Their existing accepted/review/refused decisions are
  preserved; the family barrier does not suppress peer overlays or promote an
  error.
- Independent terminal addendum review confirmed all four exact ST refusals,
  peer-overlay survival, four multi-vendor negatives, repeat artifacts, and
  qpdf integrity. Review JSON SHA-256:
  `ba128d545255c794179f705c376062e6e3424c8f6a55d8b12b4c30f5d4bb75c4`.
