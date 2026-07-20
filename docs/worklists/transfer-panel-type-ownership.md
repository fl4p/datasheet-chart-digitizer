# Transfer panel-type ownership

**Status:** current-candidate regression gate; no standalone production change is authorized by
this document. Agents must not set `human_verified`.

## Defect

TI CSD17301Q5A/CSD17302Q5A/CSD17303Q5A capacitance panels can resemble transfer plots closely
enough for the transfer digitizer to accept them. A plausible curve is still wrong when the source
panel measures capacitance rather than ID versus VGS.

`CSD17303Q5A.pdf` is not present in the current local datasheet corpus. Its verdict therefore
remains `UNVERIFIED`; absence is not a pass. The authoritative current-candidate gate exercises
the two present fixtures, and a future source-backed run must add CSD17303Q5A before claiming
three-part coverage.

## Required behavior

- Classification must use source-owned title/axis semantics, not curve shape alone.
- A transfer panel requires drain-current and gate-source-voltage evidence plus usable temperature
  identities. Missing or contradictory evidence fails closed.
- `C`, `Ciss`, `Coss`, `Crss`, or capacitance-axis evidence vetoes transfer acceptance unless the
  same printed panel independently contains a real transfer chart.
- A refusal emits no transfer temperatures, points, or derived scalar.

## Acceptance

Use the two locally present TI parts as known-bad fixtures and include multi-vendor real transfer
positives, including SPD03N50C3ATMA1-HXY. Keep the missing CSD17303Q5A explicitly `UNVERIFIED`.
Run the authoritative affected-corpus A/B. Any previously valid transfer result that moves
requires source-overlay review; any non-transfer chart accepted is RED.
