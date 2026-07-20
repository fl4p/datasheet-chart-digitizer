# Onsemi frameless cover-prose false chart

Status: focused patch agent-GREEN; authoritative full finder-corpus A/B still
pending. `human_verified=false`; no commit or push.

## Defect

`onsemi/NVMYS2D3N06CTWG.pdf` page 1 marketing prose, “Low QG and Capacitance
to Minimize Driver Losses”, was emitted as diagram 951 `capacitances`. The
synthetic Qg-axis fallback created a crop despite zero owned frame, image,
raster-grid, or axis-tick evidence. Downstream capacitance extraction then
failed on the prose crop. Real page-4 capacitance diagram 7 and raster-grid
gate-charge diagram 951 are required positives.

## Bounded contract

- Apply the new evidence requirement only when an axis-label candidate would
  otherwise use a synthetic crop. Existing grid-bound panels remain unchanged.
- Accept a local grid region, vector frame, figure-sized image rectangle, at
  least three long parallel raster rules, or two numeric tick labels aligned
  on a plausible bottom/side axis edge.
- Reject aligned numbers in mid-panel marketing/specification prose; ordinary
  horizontal prose rows are not axis evidence.
- On the target PDF remove only page 1 diagram 951. Preserve page 4 diagram 7
  `capacitances` and page 4 diagram 951 `gate_charge` byte-for-byte.
- Run a bounded cover-heavy control set, then the authoritative finder corpus
  A/B before shared acceptance. Every lost real panel is RED.

## Current evidence

Focused semantic and real-PDF regressions pass. `find_charts.py` remains at the
1500-line limit; reusable evidence geometry lives in
`finder_caption_geometry.py`. Frozen packet:
`/private/tmp/dsdig-onsemi-cover-prose-v1`. Candidate and repeat remove exactly
the target page-1 diagram 951, preserve all five real target panels, and leave
the NDB5060L/FDA032N08 controls byte-identical. Independent review is patch
GREEN:
`reviews/agent-onsemi-cover-prose-v1-001.codex-hxy-review.json`
(SHA-256 `98fb3595a2c48daaf184e399624a53e229dda3a44fdde4beba1b452760868eef`).
The full finder-corpus gate remains mandatory before shared acceptance.
