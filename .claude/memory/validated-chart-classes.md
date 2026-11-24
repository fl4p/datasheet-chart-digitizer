---
name: validated-chart-classes
description: Which dsdig chart classes/commands have a recorded human (Fab) overlay approval, on what sources, when, in which commit, with scope limits; and which have none
metadata:
  type: project
---

Single list of dsdig chart classes and their **recorded** human approvals (state
2026-11-24, main c419b43). A class counts as validated only where Fab's approval is
written down; the "recorded in" column says where. Approval covers the sources named,
not the class in general: other vendors/layouts still need an overlay check.
Agent reviews never count (see [[viz-review-autopilot-contract]]).

## Validated (recorded human approval)

| command / class | category | validated on | approved | commit | recorded in | scope limits |
|---|---|---|---|---|---|---|
| `digitize-capacitance` (`mosfet_capacitance`) | MOSFET Ciss/Coss/Crss vs VDS | many vendors, vector + raster: 187 `human_verified` rows (Infineon 46, onsemi 36, NXP 29, TI 24, Toshiba 12 ...); EPC GaN colour-legend panels (10); onsemi NDB5060L | 2026-07-16 queue close; EPC 2026-07-29; NDB5060L 2026-07-20 | various; EPC `0aeef19`, `564f95d` | `dsdig-verify-backlog/MANIFEST*.jsonl`, `AUTOPILOT-2026-07-16.md`; [[dsdig-epc-color-capacitance]], [[dsdig-ndb5060l-human-green]] | AOD294A REWORK (Crss on the border below ~10 V); EPC source-hue overlays are presentation only; Toshiba TK100E10N1 raster C(V) overlay was still awaiting approval ([[dsdig-toshiba-raster-ocr]]) |
| `digitize-vpl` (`gate_charge_vpl`, `per_vdd`) | MOSFET gate charge, Miller plateau | ~700 `human_verified` rows (onsemi 442, AO 37, NXP 35, Diodes 34 ...); 15 human-checked Vpl reference samples (README); per-VDD/Qg-axis packets | packets 2026-10-05 (14/15), 10-12 (11/11), 10-13 (15/15), 10-17 (71/82) | merges `c78991e`, `271a0b5`, `7d3d79b`, `94409e0` | merge messages; backlog manifests | flagged in 10-17 packet (same on main): plot box one label short, multi-VDD charts serving one curve, detour at VDS crossings; RJK1001 Qg axis broken; points need `qg_axis_status == verified` |
| `digitize-rds-vgs` (`rdson_gate_voltage`) | MOSFET RDS(on) vs VGS | 42 frozen goldens (TI, IR, Vishay, Rohm raster, AOS, onsemi, LCSC/UMW ...), vector + raster | v3 2026-09-28, v4-v7 2026-09-30..10-04 | merges `337f358`..`993ec34`, `650ddb8` | `tests/fixtures/rds_vgs_golden/PROVENANCE.md`, `REBLESSED.json`; golden commits (`68b97e3`, `da6ba67`, `daa61ac`, `1fd3bda`, `965ead5`); merge `650ddb8` (packet rds-vgs-004 3/3) | visual check only, not every point; [[rds-vgs-chart-class]] (merge commits recorded there 2026-11-24) |
| `rdson_temperature` (via `annotate` / module; no CLI) | normalized RDS(on) vs Tj | TI 45 + onsemi 11 `human_verified` rows; batches 24/25 (46 charts) | 2026-07-16/17 | `630313c`, `ddc91ff` | [[viz-review-autopilot-closed-2026-07-16]]; backlog manifests | TI and onsemi layouts only |
| `rdson_current` (via `annotate` / module; no CLI) | RDS(on) vs I_D | TSM60NE200CIT, TSM110NB04DCR, TSM60NC196CI (packet rds-vgs-004, 3 charts) | 2026-10-13 | merge `650ddb8` | merge message + `e68e0f8` body | approval covers the 3 changed charts of a fix packet, not a class-wide sample |
| `diode_forward_voltage` (via `annotate`; no CLI) | MOSFET body diode IS vs VSD | Infineon IPP024N08NF2S, onsemi FDA032N08, Diodes DMTH83M2SPSWQ-13 (three strata); 108 `human_verified` rows (onsemi 94, Diodes 9, NXP 5) | 2026-07-16 | `acb7a2d`, `33562e8`, `b409eec` | [[dsdig-body-diode-forward-voltage]]; backlog manifests | extraction only, no diode fit |
| `digitize-transfer` (`transfer_characteristics`) | MOSFET ID(VGS, Tj) transfer | 25-chart packet: 21 green + 4 RED fixed; NXP 13 + onsemi 1 `human_verified` rows; Toshiba raster TPH1R306PL/TK100E10N1 | 2026-07-17; Toshiba 2026-11-21 | `865fba4`; merge `32aa388` | `865fba4` body; backlog manifests; merge `32aa388` | 4 re-fixed charts were awaiting human re-review in `865fba4`; tempco fit output needs human review; check temperature identity ([[transfer-temp-assignment-check]]) |
| `digitize-reverse-recovery` | diode Qrr/Irm/trr/S, 25/125 °C (AO layout) | AOT414 (vector, filled outlines); 14 AO `human_verified` rows | 2026-07-13 | `dedb19f` | [[rr-chart-digitizer-plugin]]; backlog manifest | AO layout only; S (softness) panels FAIL by design; AOT418L stroked grids unsupported |
| `digitize-breakdown-voltage` | V(BR)DSS vs Tj | Infineon IPP040N08NF2S/IPP024N08NF2S/IPP022N12NM6 (Diagram 15 layout), IPP040N06N (old numbered-caption layout); Toshiba raster (XPW4R10ANB, TPH1R306PL) | 2026-07-14 ("ok looks good", "overlay green"); raster 2026-11-21 | `69ed408` (+ CropTransform fix); raster merge `32aa388` | [[bv-chart-digitizer-plugin]]; merge `32aa388` | Infineon + Toshiba layouts only; raster path serves `unverified` per panel (the old "PENDING" line in the note is now a dated history line) |
| `digitize-threshold-voltage` (`threshold_voltage`) | Vth vs temperature | Toshiba raster figures (TPH1R306PL, TK100E10N1, TPCC8105, XPW4R10ANB per `tests/test_toshiba_raster_figure.py`) | 2026-11-21 ("all overlays pass") | merge `32aa388` (`292f9a6`) | merge message only; parts above are the ones its tests cover, the packet list is not recorded | only Toshiba raster seen; the module docstring records this approval (2026-11-24), every panel still serves `overlay-review-required` (as `digitize-transfer`), so nothing is auto-accepted |
| `digitize-core-loss` (`powder_core_loss`) | powder-core loss vs B at several f (scanned) | KDM KS184-125A (raster, no text layer) | 2026-09-06 ("green") | `2cbb26c` | `dsdig-verify-backlog/REVIEW-kdm-powder-core-loss-20260906.md`, `MANIFEST.claude-ee-992f.kdm.jsonl` | one vendor/sheet; curves are frequencies shared across permeabilities; the folded xy-engine version (Fab pass 2026-09-11) is on an unmerged branch ([[dsdig-xy-engine-port]]) |
| `digitize-mlcc` (`mlcc_vector`) | Taiyo Yuden MLCC characteristic charts (C-f, DC bias, abs(Z)/ESR, C-T, ripple) | 47 TY -T parts, vector (Excel-direct and PScript/Distiller), 6 overlay-reviewed | 2026-10-17 (batch approved, ~0.35 % span offset accepted) | `91db60f`, merge `bc9d3ac` | `91db60f` body; `dcdc-tools/out/reviews/mlcc-ty-pdf-review-20261017.md` | Taiyo Yuden layout only |
| `digitize-paper-lines` (`paper_line_marker`) | scientific-paper line+marker plots (MATLAB box-off axes) | Keil et al. 2016 Fig. 2c, vector PDF, MATLAB export, one paper | 2026-11-24 (overlay + axis calibration pass) | `6386165`, merge `ab4e76e` | merge message | one paper; best status `unverified`, never `ok` |

## Not validated / unverified (no recorded human approval)

| command / class | state |
|---|---|
| `digitize-reverse-leakage` (`diode_reverse_leakage`) | Vishay 85885 checked only against an agent eyeball read (`d6b59bc`); overlay "written for human review", no approval found |
| `hornresp_spl` (Hornresp SPL screenshots, `c34a76c`) | module only, no CLI on main, no approval recorded |
| `digitize-xy` (generic XY engine) | unmerged branch `xy-engine`, review 5 = do not merge ([[dsdig-xy-engine-port]]) |
| thermal impedance Zth | human RED, deferred to issue #8 ([[dsdig-thermal-impedance-deferred]]) |
| gfs vs I_D | no digitizer, issue #9 ([[dsdig-forward-transconductance]]) |
| SOA | finder panel kind only (`036d9ec`), no digitizer |
| `find`, `annotate`, `export-coss-spice`, `export-coss-dslib` | tooling, not chart classes; their output inherits the class status above |

**Why:** Fab's rule: a new chart class needs human-verified overlays before batch use;
the evidence was spread over merge messages, notes and the verify backlog.
**How to apply:** before batch use, check the class row and whether the source matches
its validated scope. Add a row (with where the approval is recorded) when Fab approves
a new class or stratum; never add one from an agent review.
