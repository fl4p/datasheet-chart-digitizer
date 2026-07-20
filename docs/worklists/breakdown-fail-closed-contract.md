# Breakdown-voltage fail-closed output contract

**Status:** separate future serialization contract; pre-existing behavior does not block the
current HXY patch when the causal A/B proves it unchanged. Agents must not set `human_verified`.

## Defect

The breakdown digitizer can return `status=unverified` or `status=FAIL` while still serializing a
calibrated CSV, V(25 °C), slope, and curve points. A downstream consumer that ignores status can
therefore ingest a plausible value whose table anchor is missing or contradictory. This is the
same derived-value leak class already closed in the gate-charge and capacitance contracts.

## Required contract

- Refusal statuses expose no consumable calibrated curve or derived scalar.
- `physical_output_available=false` is explicit and authoritative.
- Refused CSV/curve fields are empty or omitted; V(25 °C), slope, and fit values are null.
- Diagnostic-only geometry and candidate values may be retained under clearly named diagnostic
  fields with the exact refusal reason; they must never masquerade as served output.
- A verified result remains byte-identical outside the new availability/provenance fields.

## Acceptance

Run a full authoritative breakdown corpus A/B. Expected movement is only currently unverified/FAIL
rows; verified rows and trace extraction bytes must not move. Assert scalar/curve nulling on every
refusal, conservation into explicit diagnostic-only fields, zero contract leaks, byte-repeat, and
dual independent review before Fab's landing decision.
