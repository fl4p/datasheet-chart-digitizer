---
name: dsdig-gate-collateral-env-drift
description: dsdig gate-charge OCR/raster (.r600) + axis-inferred extraction served-manifest reproduces host-to-host under SOLO runs; apparent "env drift" was concurrent-OCR-timeout load + stale stored baselines. Accept via sequential-solo same-host back-to-back A/B, never stored baselines
metadata:
  type: project
  originSessionId: b8a11ad6-b8c5-4093-9f87-a79c119074e1
---

Discovered 2026-07-17/18 while accepting the dsdig gate-charge **box-v3** change
(gate_charge.py 6f0c7bf4 on HEAD 38cd424). The digitizer is **deterministic within one
host** (identical across 3 repeats AND PYTHONHASHSEED 0..5). Apparent cross-host divergence
on the OCR/raster path (`.r600.pdf` + axis-inferred like HY0320) turned out NOT to be
intrinsic host/dep drift: **under SOLO runs (OMP_THREAD_LIMIT=1) the served 304-manifest
reproduces host-to-host byte-for-byte** — codex's solo gold baseline matched this session's
box-v3 `v3_machine.json` SHA a1c6f78ba8f9829f exactly. The real culprits were (a) a
**concurrent-OCR-timeout confound**: tesseract has a ~20s/page timeout, so running
baseline+candidate (or many PDFs) concurrently changes the internal review candidate/reason
for raster parts even though the served null/value contract is invariant; and (b) **stale
stored baselines** (`/private/tmp/gate-collateral-*`) generated under different/concurrent
conditions. (Env note: this host runs opencv 5.0.0 / numpy 2.4.6 / poppler 26.07 /
pymupdf 1.28.0 / tesseract 5.5.1; `uv.lock` UNTRACKED.)

**Consequence:** a v3-vs-stored-baseline diff conflated 7 env-drift parts (DI280N10TL.r600,
HY0320, 18N20-220/252.r600, RBA250N10CHPF, DI110N15PQ.r600 ×2) with the real code change —
the headline "DI280N10TL regression" and "HY0320 new value" were env drift, NOT box-v3.
The ONLY clean acceptance was a **same-host/same-venv back-to-back A/B** (detached worktree
@38cd424 vs v3 working tree, PYTHONPATH-pinned, never stash the shared dirty tree). That
isolated box-v3's true blast radius to **5 parts**: 3 intended right-edge overshoot fixes
(AGM056N10C/FDB120N10/2N7002K — box pulled back from past-last-tick overshoot into a neighbor
C(pF) panel to the own frame; curve_px+Vpl unchanged), a trivial XR10G04S +3px left, and a
PSMN102-200Y provenance improvement (diagram951 'Gate charge' → real diagram12). Zero
regressions → **agent-GREEN, human_verified NOT set**, pending Fab's overlay gate.

This is why CHART-REVIEW-CHECKLIST **§9** (sha f29f8e78→later 437a7ea9) mandates same-env
back-to-back, "stale/cross-env baseline is not causal evidence," AND that A/B sides run
**sequentially, not concurrently**, when OCR/render subprocesses have timeouts or contend
for CPU (concurrent-load behavior is a confound, not causal). The follow-up **vpl-range-v1**
change (e667696f) fail-closes the DI280 native/.gs out-of-axis Vpls (16.96/18.01 V, scraped
from the avalanche region of a box spanning avalanche + gate-charge + thermal panels) via a
centralized `_vpl_is_in_expected_range` [1,12] V band +
non-gate caption markers — a DISTINCT pre-existing bug, not box-v3. See
[[dsdig-collateral-acceptance-discipline]], [[dsdig-full-corpus-authoritative-harness]],
[[dsdig-toshiba-raster-ocr]] (DPI sensitivity — extend to host/dep sensitivity).
