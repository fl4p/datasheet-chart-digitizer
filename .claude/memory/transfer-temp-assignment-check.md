---
name: transfer-temp-assignment-check
description: How to verify temperature-curve assignment on MOSFET transfer (Id-Vgs) chart digitizations
metadata: 
  node_type: memory
  type: reference
  originSessionId: b8a11ad6-b8c5-4093-9f87-a79c119074e1
---

Verifying temperature identity on a **transfer characteristic** (Id vs Vgs, two temp curves) digitization — three independent cross-checks (use all; they must agree):

1. **Vth physics (universal):** the HOTTER curve MUST turn on at LOWER Vgs — Vth always decreases with temperature. So near threshold (small Id), interpolate Vgs at a fixed low Id: the higher-temperature curve has the lower Vgs. If the curve labeled "25°C" turns on before the hot curve, the labels are SWAPPED.
2. **Crossover / ZTC:** if the plotted range spans the zero-tempco point, the curves cross — hot is left (lower Vgs) at low Id, cold is left at high Id (mobility). Interpolate Vgs at low and high Id to see the reversal.
3. **Printed source labels:** the datasheet prints "175°C"/"25°C" next to each curve; read which physical curve each points to and compare to the digitizer's assignment. When ambiguous, **render the source PDF** (`pdftoppm -png -r 200 -f <page> -l <page> file.pdf out`) — decisive.

Batch-11 (viz-review autopilot) finding: dsdig transfer extraction had **inverted temperature labels on 4/6 NXP PSMN parts** (PSMN041/2R4/2R8/4R2) — the "25°C extracted" trace was actually the hot curve. GaN (GAN7R0) and PXN028 were correct. Inverted labels flip the sign of Vth(T)/transfer tempco downstream. The digitizer honestly self-flagged status=overlay-review-required, so it was not a false-pass. See [[viz-review-autopilot-contract]].
