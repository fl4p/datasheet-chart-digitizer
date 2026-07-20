# STL135 bbox-text process fallback

Status: superseding V2 mechanism independently
AGENT-GREEN-WITH-EVIDENCE-CORRECTION. Full corpus and human review remain
held. `human_verified=false`; no commit or push.

## Failure contract

A read-only audit reported that `pdftotext -bbox-layout` exited 134 on
STL135N8F7AG and aborted the whole PDF scan. The same command is not currently
reproducible on the same host: Poppler now exits zero and returns 273,584
bytes. This is therefore a defense-in-depth robustness change, not a claimed
currently reproducible STL file repair.

When bbox extraction raises, exits nonzero, returns empty output, yields
malformed XML, parses zero pages, or parses only wordless pages, the finder
falls back to PyMuPDF word boxes and stamps every page
`pymupdf_bbox_fallback`. The older per-corrupt-page substitution remains
distinct as `pymupdf_fallback`. If PyMuPDF also yields no words, the PDF
refuses with an explicit runtime error rather than silently returning no
panels.

## Frozen evidence

Packet: `/private/tmp/dsdig-stl135-pymupdf-fallback-v2`.

- V1 was independently RED because valid zero-page and all-wordless bbox XML
  bypassed fallback. V2 reproduces and fixes both cases; the V1 review is
  preserved in the V2 packet.
- Forced fallback emits exactly six panels: p4d951 capacitance; p6d5 transfer;
  p6d6 gate charge; p7d8 capacitance; p7d11 breakdown; and p7d12 body diode.
  These identities match the currently successful primary path, with maximum
  bbox delta 1.683 pt and one harmless title-spacing difference.
- Finder outputs and annotated PDFs are deterministic; both PDFs pass qpdf.
  The normal IRF6644 pdftotext control remains byte-identical.
- Seven focused tests and all 35 guard-class tests pass. The exact default
  finder suite reports 122 passed, one known unrelated IPI65R190CFD synthetic
  gate failure, five skipped, and 33 passing subtests. Optional-corpus fixture
  failures and the authoritative full corpus remain held.

Independent V2 review:
`/private/tmp/dsdig-stl135-pymupdf-fallback-v2/reviews/stl135-pymupdf-fallback-v2-independent-review.json`
(SHA-256
`5f4f78bf0d402e72fd0bfc425d26fa4e2bef66f335baeb67e39f1b0edc6410c9`).
