---
name: dsdig body_diode chart-selection false alarm: DMTH83M2SPSWQ-13
description: viz-review batch 01 v2 item 15 was initially misread as a capacitance chart; reconciliation confirmed it is the correct body-diode I-V panel, with the adjacent Cj diagram visible only because of a too-wide crop
created: 2026-07-16T21:43:05.289Z
metadata:
  node_type: memory
  generator: opencode-claude-memory
  type: project
  originSessionId: ses_0931fa9eeffeqwtxR7LrtFcdU2
---

viz-review batch 01 v2 item 15 (DMTH83M2SPSWQ-13 body_diode) looked like a C_j vs V_SD capacitance chart at first glance because the overlay crop included the adjacent diagram 10 (C_j JUNCTION CAPACITANCE pF) on the right edge. Re-examination with 8x crop confirmed the green plot box encloses the correct Figure 9 body-diode panel: I_S SOURCE CURRENT (A) vs V_SD, six source-labeled temperatures (-55..175°C), curves rising through the forward-diode knee, cold curve rightmost and hot curve leftmost. point_columns=[vsd_v, current_a] matches the correct kind, and manifest status=ok is accurate. The only real issue is cosmetic: crop_box should be tightened to the green plot box so the neighbor capacitance diagram does not confuse reviewers.

**Why:** A wide crop can show an adjacent diagram's axes and curves outside the selected green plot box, creating a false chart-kind alarm. The overlay renderer does not clip the crop to the plot box, and the legend/axis labels of the neighbor panel can look like part of the extracted chart.

**How to apply:** When a body_diode overlay appears to show a mixed or wrong chart, check the green plot box first: if the black extraction points lie on rising I-V curves inside the box and the suspect axis is outside the box, the extraction is correct and the issue is crop width. Flag only if the extracted points themselves track the wrong curves or point_columns do not match the claimed kind. For the DMTH83M2SPSWQ-13 case, the correct action is to tighten the crop, not re-extract or gap the panel.
