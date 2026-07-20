# Breakdown raster frame-warning provenance

Status: bounded diagnostic correction is independently agent-GREEN with scope
holds. Full authoritative breakdown-corpus A/B and human review remain held.
`human_verified=false`; no commit or push.

## Defect and scope

The generic raster warning treated any source curve touching the left or right
plot frame as potentially clipped. A V(BR)DSS-versus-temperature curve normally
spans its complete first-to-last labeled Tj range, so correct full-span traces
were warned even while their voltage values remained well inside the Y frame.

X-frame contact remains available only for the existing verified-vector-frame
provenance field. The raster warning now requires a sustained source run within
two pixels of the top or bottom Y frame, spanning at least six pixels or two
percent of plot width, with no greater than two-pixel X gaps. Extraction,
calibration, source points, served points, CSV, overlay, anchors, and the
one-unlabeled-X-interval withholding policy are unchanged.

## Frozen evidence

Packet: `/private/tmp/dsdig-breakdown-frame-warning-v1`.

- IPA60R125CFD7 p10d13 remains verified at 599.82 V at 25 °C, with all 651
  source points served over -49.8..149.8 °C and no warning. Its absolute
  540..690 V axis fully contains the source trace.
- STD14NM50NAG p6d11 retains 456 source points, serves 398 through the one
  unlabeled interval, withholds 58, and reports only that explicit withholding.
- AIMDQ75R004M2H retains its verified vector-frame provenance flag with 633
  source/served points and no raster warning.
- Synthetic controls distinguish an interior full-X diagonal, a single Y-frame
  contact, and a sustained horizontal Y-frame ride. Seventy-three focused
  breakdown tests pass.
- Candidate/repeat target PDF, overlay, and CSV are byte-identical; qpdf passes.

No corpus true Y-frame ride was found during sampling, so the warning-positive
control is deliberately synthetic and the full-corpus diagnostic A/B remains a
landing gate.

## Independent review

Review:
`/private/tmp/dsdig-breakdown-frame-warning-v1/reviews/codex-breakdown-frame-warning-independent-review.json`

- Verdict: `AGENT-GREEN_WITH_SCOPE_HOLDS`.
- SHA-256: `46dbe6b26acd676f59d59731a539c4f715cb930320d834f29b703ac69bfc2f94`.
- Diagnostic predicate, IPA numeric item, STD14 58-point withholding control,
  AIMDQ verified-vector provenance control, 73 focused tests, determinism, and
  qpdf validation are GREEN.
- Full breakdown-corpus A/B and human verification are held;
  `human_verified=false`.
