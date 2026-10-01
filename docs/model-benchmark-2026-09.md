# Chart digitisation benchmark: dsdig and vision-language models (2026-09-30)

This compares how well models, and dsdig, read datasheet curves as numbers. The harness is local:
`out/vlm-chart2table/` (gitignored). The per-model details, logs and raw outputs are in
`out/vlm-chart2table/frontier/README.md`.

## Test sets

| Set | Charts / curves | Ground truth | GT precision |
|---|---|---|---|
| `cases.frozen37.json` | 37 / 85 | Human-verified dsdig output: transfer 19, capacitance 3, RDS(on)(Tj) 6, gate charge 6, core loss, Hornresp SPL, reverse leakage | ≈0.5–1 px, so ≈0.3 % of span is the floor |
| `cases.json` (capacitance) | 149 / 447 | Human-verified dsdig capacitance | as above |
| `cases_vendor.json` | 146 / 436 | **Vendor data**: Infineon IPS JSON (65, OptiMOS/StrongIRFET), Taiyo Yuden web data (51), Murata data (30). Each chart is admitted only if its PDF vertices match the vendor data within 0.1 % of span (Murata raster 0.35 %) | median 0.04 % (Murata 0.23 %) |
| `cases_rdsvgs.json` | 15 / 31 | The RDS(on)-vs-VGS goldens (Fab-verified through round 7), read from the rds-vgs fixtures | ≈0.5–1 px |
| `cases_synth_hard.json` | 60 / 174 | `tools/synth_charts` hard/adversarial tier, exact by construction | ≤0.0002 % (vector) |
| `cases_themes.json` | 37 / 82 | Vector drawing paths of 19 chart styles not covered before: Siemens 1999–2002, IR, IXYS, Philips/NXP, ST closed-outline, CoolMOS C3, CoolSiC, Vishay Si, Littelfuse SiC, EPC and more. Admitted only if every labelled tick lands on its rule within 0.5 px | tick residual median 0.12 px; ≈0.03 % of span (max 0.11 %) |

Infineon IPS covers 285 devices in total, and the benchmark uses 50. Transfer, gate charge, body
diode and Zth do not match their IPS data within 0.2–5 %, and CoolSiC Coss/Crss is off by
0.6 %, so those are not usable as GT.

## Method

- **Metric.** Per curve: the forward-distance p95 from each model point to the GT polyline, in
  axis-normalised units (log10 on log axes), as % of span. Model points outside the GT curve's
  x-range are dropped, because reviewed GT stops where the reviewer stopped; for example,
  sub-threshold 0 A is often missing from GT. The in-range filter hides wrong extensions, so
  `n_out` is reported alongside. Coverage p95 (GT→model) is also recorded.
- **Swap.** A curve counts as swapped when the bound column is *strictly* closer to another GT
  curve. Coincident curves (ties) are not swaps. Where curves coincide over most of the range,
  identity cannot be tested (4 of the rds-vgs charts).
- **Failures.** Unparseable, missing, timed-out, capped or disqualified charts count as failed
  curves, never as skipped.
- **Scorer checks.** On every set, a perfect answer (GT written as model CSV) scores 0 with 0
  swaps and 0 unmatched. A 1 %-shifted answer scores ≈1, and swapped labels are flagged.
- **Modes.**
  - *Image only*: the model sees `chart.png` and the prompt, with no tools.
  - *Python mode*: the model may run local Python (numpy, OpenCV, Pillow) on the image. It
    typically finds gridlines and traces curves column by column.

  Each case runs in an empty `/tmp` folder holding only `chart.png`.
- **Audit.** Every event log is scanned. Any tool call in image mode, or any reference to
  another path, the network or package installs in Python mode, is a violation, and the chart
  is disqualified. The audit was tested on planted violations. Runaway caps: 60 tool calls
  (200 for DeepSeek) or $0.75 per chart, and 30 min.

## Results

### Human-verified 37 charts (85 curves): image only vs Python

Curves within 1 % / 2 % of span.

| Model | Image ≤1 % | ≤2 % | Python ≤1 % | ≤2 % | Python s/chart |
|---|---:|---:|---:|---:|---:|
| Qwen3.8 Max | 28 | 50 | **75** | 82 | 152 |
| DeepSeek V4.1 Flash | 14 | 32 | **75** | 82 | 340 |
| Claude Fable 5.1 | 33 | 62 | 74 | 81 | 74 |
| GPT-6 Astra | **69** | 78 | 73 | 81 | 39 |
| Gemini 3.1 Pro | 30 | 66 | 73 | 79 | 232 |
| Claude Opus 5.5 | 47 | 72 | 71 | 81 | **17** |
| GPT-6 Sol | 48 | 75 | 70 | 79 | 83 |
| Kimi K3 | 16 | 44 | 69 | 78 | 330 |
| Gemini 3.8 Flash | 46 | 71 | 63 | 70 | 377 |
| GLM 5.3 Flash | 11 | 31 | 62 | 70 | 85 |
| Claude Sonnet 5.5 | 29 | 54 | 42 | 58 | 8 |
| chart2table-qwen3.5-4b (local) | 6 | 10 | — | — | — |
| Qwen3.5-4B (local) | 0 | 2 | — | — | — |

The following are weak or incomplete; see the frontier README: GPT-6 Luna, Qwen3.8 27B, Kimi
K3 Fast, Step 3.7 Flash, MiniMax-M3, GLM-4.5V, Qwen3-VL 235B and Gemma 4 31B. Python mode is
what makes most models accurate. The top nine are within the GT noise on this set.

### All five sets, image only → Python

Each cell: curves ≤1 % of span, image only → Python mode. Failed, capped, timed-out and
disqualified charts count as failures.

| Model | Human-verified 37 (85) | Vendor data 146 (436) | RDS(on)-VGS 15 (31) | Synthetic hard 60 (174) | New themes 37 (82) |
|---|---:|---:|---:|---:|---:|
| **Claude Fable 5.1** | 33 → 74 | 231 → **436** | 22 → 30 | 10 → **126** | 47 → 80 |
| **GPT-6 Astra** | **69** → 73 | **377** → 426 | **30** → 31 | **43** → 91 | **71** → **82** |
| Claude Opus 5.5 | 47 → 71 | 338 → 390 | 26 → 29 | 18 → 86 | 66 → 80 |
| Qwen3.8 Max | 28 → 75 | 158 → 393 | 7 → 31 | 2 → 74 | 26 → **82** |
| DeepSeek V4.1 Flash | 14 → 75 | 40 → 402 | 9 → 29 | 0 → 25† | 7 → **82** |
| Gemini 3.1 Pro | 30 → 73 | 207 → 363 | 13 → 31 | 13 → 21 | 36 → 70 |

† DeepSeek's Python run on the hard synthetic set was still running (26/60 charts) when this was
written; unfinished charts count as failures.

Median per-curve p95 in Python mode (% of span):

| Model | Vendor data | RDS(on)-VGS | Synthetic hard | New themes |
|---|---:|---:|---:|---:|
| Fable 5.1 | 0.19 | 0.26 | 0.52 | 0.21 |
| Astra | 0.17 | 0.23 | 0.80 | 0.17 |
| Opus 5.5 | 0.27 | 0.23 | 0.86 | 0.28 |
| Qwen3.8 Max | 0.14 | 0.18 | 0.94 | 0.15 |
| DeepSeek V4.1 Flash | 0.13 | 0.21 | — | 0.14 |
| Gemini 3.1 Pro | 0.21 | 0.27 | 3.09 | 0.20 |

- **Astra is by far the best without tools** on every set. It is the model to ask when no code
  may run: identity questions, and a quick cross-check.
- **With Python, Fable is the most robust** (all 436 vendor curves; clearly ahead on the
  adversarial synthetic set). Astra is close, and ahead on the new styles.
- **Qwen3.8 Max and DeepSeek are the most precise when they finish** (median 0.13–0.15 %), but
  they time out or hit caps more often, and DeepSeek is slow (≈6 min per chart).
- **Gemini 3.1 Pro gains little from Python on the adversarial set.** It was also the model that
  tried package installs.
- Everyone but Astra depends on Python mode; image-only reading is 1–3 % of span for most.

### dsdig on the synthetic set (set1, 400 charts)

dsdig serves 50 of the 295 charts in the classes it supports, 1 of them wrong. Served
accuracy: capacitance p95 0.11 %, body diode 0.22 %, gate charge 0.31 %. dsdig serves less
than the models answer, but what it serves is at least as accurate as the best model and
carries a verification status. The models always answer, including when wrong. Details:
`out/synth-charts/set1/dsdig_baseline/REPORT.md`.

## Use

A frontier model in Python mode is a strong **cross-check** for dsdig, not a replacement. Run
it next to dsdig and send disagreements to review. On the 15 known-wrong capacitance greens,
Astra, Opus and Sonnet all landed on the real curves where the old greens failed. Model output
has no verification status, and label binding follows the printed arrow or physics
inconsistently (STK295N10F8AG).

## Incidents and caveats

- **Package install.** Gemini 3.1 Pro in Python mode ran
  `pip install pytesseract --break-system-packages`, which installed into Homebrew's Python
  (2026-09-30 09:22). It was removed, and the runner now blocks installs (`PIP_NO_INDEX`,
  `PIP_REQUIRE_VIRTUALENV`, `UV_OFFLINE`); tested on all three routes it used. The pi-based
  Python mode has no network sandbox; other network use is caught only by the audit. Affected
  charts are disqualified.
- **Out-of-folder reads.** Gemini 3.8 Flash read `~/dev/kb/INDEX.md` and pi's `AGENTS.md`, which
  pi's global instructions invite. Those 4 charts are disqualified.
- **Unavailable routes.** The Hugging Face monthly credits ran out during the run.
- **Listed but not served.** Fireworks returned 404 for Kimi K2.7, Kimi K2.6, Qwen3.7 Plus and
  DeepSeek V4 Flash Vision Exp, although they are listed.
- **Different harnesses.** GPT-6 Sol and Luna ran via pi, while Astra ran via codex.
- **Visible part numbers.** The part number is visible in the images. No model had network
  access to look it up, per the audit.
- **Fixed dsdig issue.** On 7 Infineon vendor-set charts, dsdig's `gridline_anchor` registered
  the y axis one grid pitch (~27 px) off. Fixed on 2026-10-01 (`fix/gridline-one-pitch`): 6
  now land within 0.13 px, and ISC058N04NM5 is refused (a curve merges with its 0 A rule).
