---
name: dsdig data-bearing chart gate
description: Test-circuit schematics and idealized definition waveforms are not measured chart evidence, even when they resemble VGS-vs-Qg or another supported characteristic
metadata:
  node_type: memory
  type: project
---

The dsdig finder/reviewer must distinguish a device-specific measured characteristic
from a test-circuit schematic, definition diagram, or idealized example waveform.
Captions such as “Gate Charge Test Circuit & Waveform” often contain a generic
VGS-versus-charge sketch with a Miller plateau, but that sketch has no source conditions
or device-specific measured values and must not be digitized as a gate-charge curve.

The governing chart-review checklist §1 therefore requires positive evidence that a
selected panel is data-bearing, not merely that its axes or quantity names resemble a
supported chart. This rule prevents plausible but fabricated scalars such as Vpl from an
illustrative waveform.

Related: [[chart-review-checklist]], [[dsdig-trace-fidelity-visual-gate]].
