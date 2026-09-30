# Benchmarks and reference data for testing dsdig and frontier models (September 2026)

Research date: 2026-09-29. This report follows up `docs/chart-digitisation-landscape-2026-09.md` (called "the landscape report" below). It does not repeat that report's paper reviews.

**The problem.** Our harness has 183 cases in `out/vlm-chart2table/cases.json`: 149 are capacitance charts, 144 have at least one log axis, and 181 are human-verified. Frontier models running Python reach about 0.3–0.4 % of span p95 on it. That is the noise floor of our human ground truth (GT), so the set can no longer separate the good models. We need test data whose GT error is well below 0.1 % of span.

**Conventions.**
- `[Bn]` is a source ID from the access log in section 9.
- "% span" means distance in axis-normalised coordinates (log10 on log axes) as a percentage of the axis span. This is the same unit our harness uses.
- **Direct** means I read it in a file or document. **Inference** marks my own reasoning.
- Findings from the two research sub-agents are marked `(r-datasets)` or `(r-semis)`. Where I re-checked one myself, the text says **re-verified**.

## Executive summary

1. **Datasheet-style GT better than 0.1 % of span exists, but not in the ML chart benchmarks.** It comes from vendors who publish the same numbers they plot:
   - **Infineon's Interactive Product Sheet (IPS) API.** It returns dense JSON for about 15 MOSFET diagram types.
     - On OptiMOS BSC010N04LS, the datasheet's Diagram 5 is the IPS curve sampled every 0.15 V. Vertices agree to ≤0.049 % span (r-semis). My own affine re-fit gives ≤0.09 % span, with the worst points at clipped curve ends; this is **re-verified**.
     - Diagram 11 (capacitances) agrees to ≤0.057 % span (r-semis).
     - CoolSiC agrees only to 0.6 % span on Coss/Crss [B10].
   - **Taiyo Yuden MLCC characteristics PDFs.** These are vector log–log charts. The PDF curve vertices match TY's independent web data at p95 0.0023 decade, 0.038 % span; I measured this myself [B3].
   - **Micrometals** datasheets are drawn from their printed fit formulas to ≤0.055 % span (r-semis) [B13].
   - **Nexperia** interactive-datasheet SVGs are the PDF paths themselves, at 0.006 % span coordinate resolution (r-semis) [B11].
2. **The public chart benchmarks cannot discriminate at 0.1–0.5 % on our chart types.**
   - The sets with exact GT have **no log axes**: AdobeSynth19, LineEX, PlotQA, FigureQA, ChartX, WB-ChartExtract, EvoChart (r-datasets counts, section 1).
   - The real-chart sets use **human GT** and are only about 3 % log: CHART-Info 2024 has 29 of 1,033 line charts with a log axis.
   - The two benchmarks with serious log content are **unreleased**: ChartZero (388 of 1,000 charts with a log axis) and RF-CDA (section 1.3).
3. **Code-as-GT sets supply a few hundred exact log charts.** Counts: CoSyn-400K 778 log line charts (ODC-BY), ChartMimic 29 of 1,800, Chart2Code-160K 339 log line-ish charts. The way to get datasheet-style difficulty in volume is still **our own generator** (section 3).
4. **PLECS thermal models are not datasheet GT.**
   - Wolfspeed is close (≤0.3 % of a 12 V span).
   - EPC, ROHM and TI do not match their datasheet curves (r-semis, section 2.2).
5. **For comparability, report FPC-NRMSE and a within-ε ladder computed in display space.** None of the published metrics handles log axes as defined. CHART-Info's metric works in pixel space for task 6a, which is the only log-neutral variant (section 4).

**Ranked recommendation** (details in section 6):
1. Infineon IPS paired with the vector datasheets.
2. Taiyo Yuden and Murata MLCC characteristics: charts plus vendor data, already in Fab's vendorpull store.
3. Our own datasheet-style generator: matplotlib plus augraphy, exact GT.
4. Micrometals and Magnetics core-loss charts, plus NIST XCOM, as formula- or table-drawn stress sets.
5. Nature "Source Data" packages that ship each panel as a matplotlib PDF plus CSV.

## 1. Question 1: datasets with numeric GT for line or curve charts

Sizes and licences come from the dataset card, the licence file or API metadata, as cited. Counting methods are summarised under the table (r-datasets); the detector scripts are in `/tmp/bench-research/`.

### 1.1 Sets with numeric GT and pixel geometry

| Dataset | Download (verified) | Licence | Size | GT type | Log-axis content (method, n) | Curves per chart, crossings | Raster/vector | Eval code | Offline in our harness |
|---|---|---|---|---|---|---|---|---|---|
| CHART-Info 2024 (UB-PMC, real PMC figures) | Dropbox `CHARTINFO_2024_Test.zip` / `_Train.zip`, linked from github.com/kdavila/CHART_Info_2024 | CC BY-NC-SA 4.0 (license.txt in zip) | test 804,357,086 B; train 1,873,148,458 B | **Human** annotation (ticks plus polylines) | Tick-fit detector over all test JSON: **29 of 1,033** line charts with task-6 GT (30 axes); no scale field | 1–14 series, mode 2 | JPG | metric6a/6b (adobe-research/CHART-Synthetic; LineFormer `metric6a.py`) | Yes (tick pixels plus polylines) |
| UB-PMC 2022 (ICPR 2022) | Dropbox `ICPR2022_CHARTINFO_UB_UNITEC_PMC_TEST_v2.1.zip` (chartinfo.github.io/toolsanddata.html) | CC BY-NC-SA 4.0 | test 782,063,690 B; train 1,065,394,038 B | Human | **9 of 399** line charts (12 axes) | 1–10 series | JPG | same | Yes; probably overlaps CHART-Info 2024 (inference) |
| AdobeSynth19 | github.com/adobe-research/CHART-Synthetic release v1.0 | CC BY-NC-ND 4.0 | images ≈11.35 GB; train JSON 493,594,325 B | **True** (matplotlib from CSV) | 41,881 train line charts: y linear 41,881/41,881, x categorical. **0 log** | 1–4 series | PNG | metric*.py | Yes; ND licence forbids derivatives |
| LineEX | Google Drive folder `1VX570JGCffPFfGybFyJsGRc4RMmmuNaz` | Apache-2.0 (repo; no data licence found) | test 988,531,668 B; train 19.99 GB | True (raw values plus pixel keypoints) | Affine fit over all 20,000 test charts: **0 log** | 2–6 curves | PNG | `modules/KP_detection/eval.py` | Yes |
| PlotQA | Drive ids in github.com/NiteshMethani/PlotQA `PlotQA_Dataset.md` | CC-BY-4.0 data, MIT code | test annotations 247.8 MB; train images 4.1 GB | True | 11,123 test line/dot_line: **0 log** | 1–4 series | PNG | repo | Yes |
| FigureQA | download.microsoft.com `figureqa-*-v1.tar.gz` | MSR Open Data (non-commercial) | 3.53 GB | True (Bokeh) | Sample of 1,000: all linear; generator has no log option | 2–7 curves | PNG | — | Yes |
| ChartQA | HF ahmed-masry/ChartQA | GPL-3.0 (card) | 875 MB | Source tables | 211 test line charts; **0 log** | 0–8 | PNG | — | Partial |
| DVQA | github.com/kushalkafle/DVQA_dataset | CC BY-NC 4.0 | — | True | **bar charts only** | — | PNG | — | n/a |
| Engauge Digitizer samples | github.com/akhuettel/engauge-digitizer `samples/` | GPL-2.0 | small | **True, analytic**, for the gnuplot samples (`samples/sources/gnuplot.script`); `test/*.csv_expected` files are Engauge's own outputs, not truth | `gnuplot_x_log_y_*` (6 files) plus `loglog.png`, `linlog.png`, `loglin.png`, `random_walk_log.png`, `gridlines_log.gif`. File list **re-verified** by `gh api` | ~3 curves | PNG/GIF, 2 PDFs | Engauge harness | Yes; tiny |
| figverify fixtures | github.com/Bowen-Sun-0728/figverify `tests/data/dataset` | MIT | tiny | True (Float32 npz) | includes `T2L_010_logx` | multi-series | **vector PDF** | `run_tests.py` | Yes. The paper's 268-figure audit set is not redistributed [B14 §10] |
| WebPlotDigitizer tests | automeris-io/WebPlotDigitizer `tests/` | AGPL-3.0 | — | Unit tests plus 4 project JSONs; **no image set with GT** | — | — | — | JS tests | No |

### 1.2 Code-as-GT sets

Each chart is a program, so it can be re-rendered to PNG or vector PDF, and matplotlib's `ax.transData` then gives the exact pixel↔data map. All rows are r-datasets.

| Dataset | URL | Licence | Size | Log content (regex over code, all files unless noted) | Notes |
|---|---|---|---|---|---|
| CoSyn-400K chart split | HF allenai/CoSyn-400K | ODC-BY-1.0 | 67.8 GB | **3,189 of 117,838 (2.7 %)**; 778 of 15,924 line; ≈333 log–log | data table plus code; generator allenai/pixmo-docs (Apache-2.0) |
| ChartMimic | HF ChartMimic/ChartMimic `dataset-iclr.tar.gz` (388,886,108 B) | Apache-2.0 | 1.28 GB | direct_1800: 29 of 1,800 (line 9 of 210) | 8 `loglog`, 13 `set_xscale`, 10 `set_yscale` |
| Chart2Code-160K | HF xxxllz/Chart2Code-160k | CC-BY-NC-4.0 | json 125 MB; images 6.95 GB | 622 of 163,186; 339 of 44,783 line-ish | — |
| ChartNet (IBM 2026) | HF ibm-granite/ChartNet | core_permissive CDLA-Permissive-2.0; other subsets restricted | ≈2.56 TB | 1 shard: 34 of 10,000 (4 of 740 line) | 6 libraries |
| Plot2Code | HF TencentARC/Plot2Code | Apache-2.0 | 148 MB | 7 of 368 | — |
| ReachQA | github.com/hewei2001/ReachQA | MIT (HF card) | 572 MB | test 3 of 500 | — |
| ECD-10k | HF ChartFoundation/ECD-10k-Images | MIT | 1.49 GB | 2 of 10,535 | — |
| CSU-JPG Chart2Code | HF CSU-JPG/Chart2Code | not stated | 1.71 GB | 8 of 350 (sample) | — |
| ChartX | HF InternScience/ChartX | Apache-2.0 | 457 MB | **0 of 1,152** | line: median 3, max 4 curves |
| EvoChart-Corpus | HF MuyeHuang/EvoChart-Corpus | GPL-3.0 | 6.38 GB | **0 of 69,902** ECharts configs | — |

### 1.3 Tables-only, QA-only, and unreleased sets

| Item | Status and what it contains | Source |
|---|---|---|
| ChartBench (MIT, 23.7 GB) | Tables only; no pixel map; log not checkable | r-datasets |
| WB-ChartExtract (CC-BY-4.0, 69 MB) | 1,000 charts, 252 line; tables only; 0 log; generator has no log option | r-datasets |
| ExChart-Bench (285 MB, repo GPL-3.0; data licence not stated) | **Human** "manually extracted values" | r-datasets |
| SpecVQA (CC-BY-NC-4.0) | QA only. **The curve-point GT used in the paper is not released** | r-datasets |
| CharXiv, ArxivQA, SciCap, MMC, ChartQAPro, ChartLlama, ChartGalaxy | QA, captions or infographics; no numeric curve GT (cards and file lists; only CharXiv's schema was inspected) | r-datasets |
| **ChartZero** (arXiv 2605.05820, 2026) | 1,000 real PDF charts; human CVAT masks. **Axis scales: lin–lin 612, lin–log 173, log–lin 126, log–log 89**; 157 with scan/print artefacts; up to 11 series. **Not released** (no GitHub or HF hit) | **re-verified**: text layer of [B9] l.415–416 |
| **RF-CDA** (SSRN 7084283, 2026) | 29,280 RF curve images; 5,856-image held-out benchmark with human references. **No public release found.** §A.6 and "Availability" say only that a sanitised subset "will be prioritized for release" [B15 l.2229–2230, l.3355–3368]. Search record in section 8 | re-verified |
| Chart2CSV | Reproducibility statement points to anonymous.4open.science `figure-to-data-2FB5` and `figure-to-data-code-C4EB` [local PDF l.488–490]. Both now show **"The repository is expired"** (checked 2026-09-29). OpenReview forum re-read through Fab's Chrome: withdrawn 2026-01-04, CC BY 4.0, no new data link [B16] | re-verified |
| DigitizerBench (LinePilot) | No data URL in the arXiv text, the abs page or GitHub. Linear axes only | r-datasets; bounded search |
| ChartRecover (figshare 10.6084/m9.figshare.32070000, CC BY 4.0, 713 MB) | Derived from UB-PMC 2022 | r-datasets |
| Starrydata2 (`all_curves.csv.gz`, 43.7 MB) | Human-digitised curves keyed by DOI; **no figure images** | r-datasets |
| LG dataset (2307.02065) | Pixel segmentation, not numeric values | r-datasets |

**How the counts were made (r-datasets).**
- **Code sets:** the regex matches `set_[xy]scale('log'|'symlog')`, `loglog(`, `semilog[xy](`, `type='log'`, `LogLocator` and similar.
- **Annotation sets:** tick pixel position is fit against value and against log10(value). An axis counts as log when R² on log10 > 0.999 and R² on the linear value < 0.98. This is a lower bound, because labels like "10^-3" that do not parse are skipped. Three CHART-Info hits were spot-checked and all were real log axes.

## 2. Question 2: real-world charts with known TRUE data

### 2.1 Summary table

| Source | Families / scope | What is published | Chart ↔ data agreement (measured) | Chart format | Terms | Evidence |
|---|---|---|---|---|---|---|
| **Infineon IPS** `ips.infineon.com/api/feature?...` (no login) | CoolSiC, GaN (incl. ex-GaN Systems), StrongIRFET, OptiMOS 3/5/6/7/8 | JSON for ~15 diagram types: output, transfer, RDS(on)(ID, Tj), Vth(Tj), gfs, Ciss/Coss/Crss, body diode, Qg, Eoss/Qoss, SOA, Zth, avalanche (e.g. 8 curves × 799 points). The IPS guideline says these are simulations from Infineon's SPICE server [B10 guideline PDF] | **OptiMOS BSC010N04LS Rev 2.5:** Diagram 5 ≤0.049 % span (r-semis). My own fit uses one common scale for all 8 paths (−0.5428 pt/A, offset 377.57 pt) and gives max residual ≤0.35 A of 400 A = **0.09 % span** (re-verified). Diagram 11 capacitances ≤0.057 %. **CoolSiC IMBG65R048M1H:** Ciss ≤0.09 %, Coss/Crss only ≤0.6 %; output-chars API returned 504 | vector PDF | "download, reproduce … solely for informational and non-commercial or personal use" (infineon.com/legal/usage-terms, r-semis) | [B10] |
| **Nexperia interactive datasheet** (`api.nexperia.com/interactive/v1/chapters/<MPN>` plus per-figure SVG) | PSMN/BUK MOSFETs (only PSMN1R0-40YLD tested) | SVG polylines equal to the PDF paths after a pure translation; tick labels are SVG text; `"graphs":[]`, so there is no separate numeric series | Coordinates at 0.01 pt ≈ **0.006 % span**. Decoded Ciss/Coss/Crss at 20 V: 8851/1881/381.9 pF vs table 8845/1878/382 pF | vector | not verified | [B11] (r-semis) |
| **Taiyo Yuden TY-COMPAS** characteristics PDF plus web data | MLCC (16,479 parts in Fab's vendorpull store) | Per-part 1-page PDF with 5 charts (C–f, |Z|/ESR–f log–log, DC bias, temperature, ripple); curve values as amCharts data (5 significant digits) | **Z and ESR path vertices vs web data: p50 0.0013/0.0009 decade, p95 0.0023/0.0019 decade = 0.038/0.032 % of the 6-decade span.** Decade gridlines fit a line to ≤0.045 pt | **vector** (Excel-style; dense minor log grid; crossing Z/ESR; legend; markers) | PDF says "The data is reference only"; no licence stated | [B3] direct |
| **Murata pimapi** (public JSON API plus "Electrical Characteristics Data" PDF) | MLCC (24,475 parts in the store); the same API serves other categories (e.g. NFM) | JSON curves with declared units; 1-page PDF with 5 charts (|Z|/ESR–f log–log, DC bias, AC voltage, temperature, ripple temperature rise) | |Z| and ESR JSON over the raster: 399 points each, bias −0.38/−0.42 px, **p50 0.46 px**, p95 1.55 px (vertical residual, inflated on steep segments). Frame 8 × 7 decades in 392 × 291 px, i.e. 1 px = 0.34 % of y span | **raster** (440 × 320 image per chart, ≈160 dpi at print size) | not verified | [B1, B2] direct |
| **Micrometals** mix-26 datasheet (auto-generated) | iron powder | Printed formulas: core loss P = f/(a/B³+b/B^2.3+c/B^1.65)+dB²f²; %µ vs H | Core loss max 1.66 mdex over 3000 mdex (**0.055 %**); DC bias max 0.052 %-points over 120 (**0.04 %**) | vector | not stated | [B13] (r-semis) |
| **Magnetics Inc** 2025 Powder Core Catalog | Kool Mu, MPP, High Flux, XFlux, Edge | P = aB^b f^c tables (printed pp. 109–111); %µ = 1/(a+bH^c) (p. 63) | Core loss: straight segments, slope 1.987–2.004 vs b = 1.988, implied f within 0.5 % (**≈0.13 % span**). DC bias: 0.3 % rms, 0.9 % max: **not exact** | vector | not stated | [B12] (r-semis) |
| CSC / Chang Sung 2015 catalogue | powder cores | Printed formulas | **Not compared** (tick labels are outlined glyphs) | vector | not stated; copy is distributor-hosted | r-semis |
| **NIST XCOM** (SRD 8) | photon cross-sections, any element | Table (4 significant figures) plus graph of the same run | Si photoelectric: 79 points, bias −0.27 px, **p50 0.29 px, p95 1.01 px (0.18 % of y span)** | raster 742 × 882; log–log, 23 y decades, 7 dashed/dotted crossing curves, K-edge | © U.S. Sec. of Commerce, "All rights reserved"; DOI 10.18434/T48G6X | [B4] direct |
| **Nature journals "Source Data"** (example s41467-025-66210-z) | per paper | Zip per figure panel: **matplotlib PDF + PNG + CSV** (e.g. Fig. 1b log-y Id–Vg transfer curve, matplotlib 3.9.0) | Not measured here. figverify reports a median relative error of 0.09 % against deposited data across renderers [B14 §7] | vector PDF (panel) | CC BY-NC-ND 4.0 (this paper) | [B5] direct |
| Planck 2018 TT spectrum; NOAA Mauna Loa CO₂ | cosmology, climate | figure plus data release | figverify: ~10⁻⁹ (markers), ~5×10⁻⁴ of range (polyline) [B14 §7] | vector | — | second-hand via [B14] |
| **Hornresp** (build 6050-260928) | loudspeaker simulation | SPL and Ze can be exported as text, with log-spaced frequencies from 10 Hz to 20 kHz (third-party manual [B6] l.1415–1421; not tested) | exact by construction (inference) | raster screenshots | freeware, "All rights reserved" [B7] | [B6, B7] |

### 2.2 Semiconductors: can datasheet curves be regenerated from published data?

- **Yes, exactly, for Infineon IPS families that pass a per-part check.** For OptiMOS the datasheet diagram *is* the IPS output (≤0.1 % span). This does not hold everywhere: CoolSiC Coss/Crss differ by 0.6 %. **Gate every part by comparing its datasheet vertices with IPS before using it as GT** (my recommendation).
- **Yes for Nexperia, but only geometrically.** The SVG is the chart, so it gives exact raster GT but is not independent of the PDF vector data.
- **No for PLECS thermal models** (r-semis):

  | Vendor | Result |
  |---|---|
  | Wolfspeed C3M0065100K | ≤0.3 % of a 12 V span vs Fig. 2: close, not exact |
  | EPC2367 | Rdson(150 °C) 1.911 vs 1.810; Eoss(50 V) 4.18 µJ vs 1.07 µJ implied |
  | ROHM SCT4036KR | 0.769 V vs 0.718 V at 19.7 A |
  | TI LMG342x | 7-point exactly linear table (synthetic) |

  Not obtained: onsemi (login), Toshiba (licence click-wrap), Navitas/GeneSiC (see section 7).
- **Vector datasheets are their own GT.** r-semis surveyed 4 random PDFs per vendor in `~/dev/pv/pwr-mosfet-lib/datasheets` (heuristic, not per chart):
  - **Vector:** Nexperia, Infineon, onsemi, ST, TI, Vishay, Renesas, Panjit, AOS, Diodes, MCC, Littelfuse.
  - **Raster:** NCE, KIA, Siliup, Anhi, Minos, Wuxi, AGM, some ROHM and Toshiba.

  TY shows what vector-derived GT costs in precision: gridline-calibrated vertices match independent numbers to 0.04 % span. That is roughly 10× better than our human GT (direct, [B3]).

## 3. Question 3: synthetic generators with exact GT

| Generator / tool | Licence | Log axes | Dense grid / minor ticks | Crossing curves | In-plot labels | Thin strokes | Raster degradation | Source |
|---|---|---|---|---|---|---|---|---|
| FigureQA (Bokeh fork) | MIT | no (grep) | — | yes (2–7 curves) | no (legend) | — | no | r-datasets |
| WB-ChartExtract generator (mpl/seaborn/plotly/bokeh) | Apache-2.0 | no | randomised grid, linestyle, linewidth | yes | no | varies | no | r-datasets |
| CoSyn / allenai pixmo-docs (LLM-written code) | Apache-2.0 | 2.7 % of charts | uncontrolled | yes | sometimes | uncontrolled | no | r-datasets |
| Code-as-GT sets (ChartMimic, Plot2Code, Chart2Code-160K, ECD, ChartNet, EvoChart, ChartX) | per row above | few | uncontrolled | yes | rare | — | no | r-datasets |
| AdobeSynth19, PlotQA, DVQA, LineEX, DigitizerBench, ChartZero | — | **generator not shipped** | — | — | — | — | — | r-datasets |
| augraphy | MIT | — | — | — | — | — | JPEG, dithering, bad photocopy, fax, noise, rescale, geometric warp, moiré | r-datasets |
| genalog (Microsoft), albumentations | MIT | — | — | — | — | — | yes | r-datasets |
| NIST XCOM form (`_scripts/xcom3.py`) | NIST terms | log–log only | sparse ticks, no grid | 7 per element, dashed/dotted | legend | thin | server raster only | direct [B4] |

**No shipped generator produces datasheet style.** By that I mean:
- log–log over 3–8 decades with 2–9 minor gridlines;
- 3–8 crossing curves labelled in the plot;
- 0.3–0.8 pt strokes;
- raster degradation (72–150 dpi, JPEG q 30–80, blur, scan noise).

The cheapest route (inference, agreed by r-datasets) is our own matplotlib generator:
- **Curve shapes:** physical rather than random, e.g. Coss(V) ∝ (1+V/φ)^−m plus a knee, Zth ladders, Steinmetz loss. Seed the styles from CoSyn/ChartMimic log code and from TY/Murata/Infineon layouts.
- **Outputs:** both a vector PDF and an augraphy-degraded PNG, with GT from the data arrays and `ax.transData`.

## 4. Question 4: metrics to report for comparability

| Metric | Exact definition (source, locator) | Tolerance / threshold | Handles log axes? | Use for dsdig |
|---|---|---|---|---|
| **FPC-NRMSE** (DigitizerBench) | Per matched line ℓ: interpolate prediction and GT without extrapolation at one position per horizontal plot pixel over the shared x-range. e_iℓ = sqrt(mean((ŷ−y)²)) / (y_max − y_min). Hungarian assignment on min(e,1) + (1−C), with coverage C = K/W. T_i = 1 only if the line count is correct, every match is finite, has ≥20 pairs and C ≥ 0.8. F_i = mean e if T_i = 1 and ē < 1, else **1**; F = mean over images [B17 §4.3 Eq. 1] | "trusted usability" TU = share with T_i = 1 | **No**: data units, and the paper is linear-only (§5.3) | **Adopt**, computed in display space (log10 on log axes) |
| **CHART-Info continuous (6a/6b)** | Recall = (1/(u_M−u_1)) Σ (1 − Error_i) · Interval_i, with Error = min(1, \|v_i − I(P,u_i)\| / \|v_i\|) or the ε form min(1, \|·\|/(\|v_i\|+ε)), ε = GT range / 100. Precision = Recall(G,P). Series matched by bipartite assignment; F-score [B18 §1.1 Eq. 1–5] | relative to \|y\| | Relative-to-\|y\| error is scale-dependent. "Also a good metric … in pixel space for task 6a" [B18 §1.1], and **the pixel-space form is log-neutral** | Report 6a in pixel space for literature comparison |
| **RNSS** (DePlot) | D(p,t) = min(1, ‖p−t‖/‖t‖); minimal-cost matching X; RNSS = 1 − ΣX·D / max(N,M) [B19 §3.1 Eq. 1] | none | No | Do not use ("gives credit to very high relative errors", ibid.) |
| **RMSF1** (RMS) | Entry similarity (1 − NL_τ(keys))(1 − D_θ(values)), D_θ = min(1, ‖p−t‖/‖t‖) with distances above θ set to 1; precision and recall over the matching; F1; transpose-invariant [B19 §3.1 Eq. 2–3]. WB-ChartExtract uses τ = 0.5, θ = 0.1 [B20 App. A] | θ = 10 % | No | Too coarse (10 %) |
| **SCRM / AP@strict, slight, high** (ChartX, OneChart) | Triplet matching; for "normal" charts (incl. line): strict J_thr = 0, e_thr = 0; slight J_thr = 2, e_thr = 0.05; high J_thr = 5, e_thr = 0.1 [B8 App. B.2 Eq. A.1; B21 Eq. 6] | 0/5/10 % | No | Too coarse |
| **Adaptive MAPE** (ExChart) | mean \|v̂ − v\| / V_max × 100 %, capped [B22 §5.4 Eq. 2] | none | No (V_max in data units) | — |
| **SpecVQA score** | Score = 1 − d / max_{x,y∈C_true∪C_rec} ‖x−y‖², with d the Chamfer (mean squared nearest-neighbour both ways), Hausdorff or Wasserstein distance; Hungarian matching of lines [B23 §3.3, Eq. 2] | none | Depends on the coordinate frame (unspecified) | Chamfer in display space is a good *symmetric* shape metric |
| **Within-ε** (RF-CDA) | Fraction of points within ε = 1/3/5 % of max(W,H) (landscape report [S35 §4.1]) | 1/3/5 % | pixel frame, so yes (inference) | Adopt the ladder at **0.1/0.25/0.5/1 %** |
| **dsdig harness** | forward p50/p95 (prediction → GT polyline), coverage p95 (GT → prediction), swap flag, in axis-normalised units with log10 on log axes (`out/vlm-chart2table/score.py` header) | — | **Yes** | Keep as primary |

**Recommendation.** Keep the harness metric as primary. Add, all in display space:
- FPC-NRMSE with unit loss on failure;
- symmetric Chamfer;
- the within-ε ladder at 0.1/0.25/0.5/1 %.

Report CHART-Info 6a in pixel space for literature comparison. Every published metric above measures error in data units, relative to |y|, or against a threshold of 5 % or more, so none resolves 0.1 % differences on log axes as defined.

## 5. Question 5: which sources can discriminate at 0.1–0.5 % of span?

The test: a source can discriminate at level L only if (a) its GT error is well below L, and (b) the chart itself resolves L.

| Source | GT error (own) | Chart resolution | Discriminates at 0.1–0.5 %? |
|---|---|---|---|
| Infineon IPS + vector datasheet (gated per part) | ≤0.05–0.09 % span (OptiMOS) | vector; render at any dpi | **Yes, down to ~0.1 %** |
| Taiyo Yuden PDF + web data | p95 0.038 % span (independent numbers) | vector | **Yes, down to ~0.1 %** |
| Nexperia SVG | 0.006 % (coordinate grid) | vector | Yes, for raster/VLM tests; not independent of the PDF |
| Micrometals formula charts | ≤0.055 % | vector | Yes |
| Magnetics core loss / DC bias | ≈0.13 % / 0.3 % rms | vector | Core loss marginal at 0.25 %+; DC bias no |
| Murata JSON + raster | numbers exact; rendering p50 0.46 px ≈ 0.16 % span | raster, 1 px = 0.34 % span | **0.3–0.5 % only** (raster-limited) |
| NIST XCOM | table 4 sig. fig. (≈10⁻⁴ decade); rendering p95 1 px = 0.18 % | raster | 0.3–0.5 % |
| Nature source-data matplotlib PDFs | exact CSV (as deposited); vector | vector | Yes, if the CSV is exactly what was plotted (check per panel) |
| Code-as-GT sets, own generator | exact (Float32) | any | Yes (synthetic only) |
| Engauge gnuplot samples, figverify fixtures | exact | small raster / vector | Yes, but too few (≈10 log charts) |
| CHART-Info 2024 / UB-PMC | human annotation; error not reported (likely ≥0.5 %, by analogy with expert WPD at 0.50 %, landscape [S2]; inference) | JPG | **No** |
| AdobeSynth19, LineEX, PlotQA, FigureQA, ChartX, WB-ChartExtract | exact | raster | Precise enough, but **0 log axes**, so they do not test our case |
| ExChart-Bench, RF-CDA, ChartZero, Starrydata | human | — | No (or unreleased) |
| PLECS models | 0.3 % (Wolfspeed) to >5 % | — | No |

## 6. Ranked recommendation: what to integrate first

| Rank | Source | Integration effort | What it tests that our set does not |
|---|---|---|---|
| 1 | **Infineon IPS + vector datasheets** (OptiMOS first; CoolSiC/GaN only after the per-part gate) | **Medium.** Per part: capture the IPS feature XHRs, map features to diagram numbers, gate by vertex-vs-IPS ≤0.1 %, render the pages at 100–200 dpi plus degradations. Mind the terms (non-commercial/personal) | GT ~10× tighter than ours on **exactly our chart classes** (output, transfer, log C–V, Qg, RDS(on)(Tj), diode, log–log Zth, SOA); 8+ curves per panel; independent of our annotators |
| 2 | **Taiyo Yuden (vector) and Murata (raster) MLCC characteristics** | **Low.** Curve data for 16,479 TY and 24,475 Murata parts is already in Fab's vendorpull store (`~/dev/pv/ee/dcdc-tools/vendorpull/store`, zstd with `.zdict`). Only the PDFs are needed (plain GET; the `compact_store` step deleted them) plus axis ranges. Scripts in `out/benchmark-data/_scripts/` | Log–log over 6–8 decades, dense minor grids, Z/ESR crossing near SRF, legends, markers, Excel and Murata renderers, **low-resolution raster (41 px/decade)**, huge volume. TY GT is independent of the PDF geometry |
| 3 | **Own datasheet-style generator** (matplotlib, augraphy) | **Medium.** ~300–500 lines; physics-shaped curves; vector plus degraded raster | Controlled difficulty: in-plot labels instead of legends, 0.3 pt strokes, dashed curves, crossings, misprinted or odd tick formats ("10²"), JPEG/scan damage. Nothing published does this |
| 4 | **Micrometals / Magnetics core loss + NIST XCOM** | **Low.** Formulas and tables are printed; XCOM script exists | Powder-core class (only 1 case in our set); log–log with extreme decade counts, dashed/dotted crossings, a discontinuity (K-edge) |
| 5 | **Nature Source Data panels** (matplotlib PDF + CSV) | **Medium–high.** Per-paper curation (search Nature Comms / Electronics for log-scale device plots with source data) | Real-world, out-of-domain scientific styles (log-y transfer curves, hysteresis, markers) with deposited data |

Not recommended for discrimination: CHART-Info/UB-PMC (human GT, 3 % log), and the linear-only synthetic sets. They remain useful as a coarse regression check against published numbers such as LineFormer's 6b scores.

## 7. Blocked or unverified items, with the highest rung reached

| Item | What depends on it | Highest rung and result | Needs from Fab |
|---|---|---|---|
| onsemi Self-Service PLECS Model Generator, https://www.onsemi.com/sspmg | whether onsemi PLECS matches its datasheets | Anonymous headless (r-semis) and Fab's Chrome (2026-09-29): **MyON SAML2 SSO login page**; Fab's profile is not logged in | Log in to MyON in his Chrome, or waive |
| Toshiba PLECS library zip, https://toshiba.semicon-storage.com/info/docgetzip.jsp?did=701&displang=en | same, Toshiba | rung 4: click-wrap "Agreement on Simulation Model"; its §2 limits use to "functional evaluation of the Product" and forbids analysing the data (r-semis). **Not accepted on Fab's behalf** | Decide whether to accept, or waive |
| **Wolfspeed click-wrap (disclosure)** | Wolfspeed row | r-semis **clicked "I Accept"** on Wolfspeed's model terms (personal non-commercial use incl. evaluation; no redistribution) to download `Wolfspeed_All_PLECS_Device_Models.zip`. No challenge was solved | Confirm or ask me to delete the zip |
| Navitas / GeneSiC PLECS | fourth PLECS data point | navitassemi.com: anonymous curl 537 B shell; headless and **Fab's Chrome with a visibility override** render an empty body (2 MB HTML, 0 text, 0 links). genesicsemi.com: **HTTP 500** twice. Wayback CDX shows per-part `…/sic-mosfet/<MPN>/<MPN>_PLECS.zip` URLs in 2022–2023, stored as 301 redirects only. Automated rungs are exhausted; the 5xx is unresolved | Open navitassemi.com and point me to the PLECS downloads, or waive |
| ST curve data / PLECS | whether ST publishes curve data | Product page SCTW35N65G2V: anonymous rungs 1–6 failed (r-semis). **Resolved through Fab's Chrome**: the page renders (part OBSOLETE), and only the SPICE tutorial UM1575 is listed. The simulators page lists STPOWER Studio (electro-thermal simulator, not tested). ST is absent from Plexim's vendor list [B24] | None (STPOWER Studio is untested, as a lead) |
| RF-CDA database release | whether RF-CDA can be used | Not found; search record in section 8. The ResearchGate page is a blank shell in Fab's Chrome | Ask the authors (Hangzhou Dianzi University) if wanted |
| ChartZero, DigitizerBench data | — | not released or not linked (bounded search) | — |
| Infineon CoolSiC IMBG65R048M1H output characteristics | CoolSiC gating | IPS API 504 (not retried) (r-semis) | — (retry later) |
| CSC formula-vs-chart | powder-core option | not compared (outlined glyphs) | — |
| Hornresp export as GT | SPL class | export documented only by a third-party manual; not run (no Windows/Wine here) | Run one export on Windows |
| Licences of Nexperia SVG, Magnetics, Micrometals, Murata, TY | reuse | not stated in the files or not checked | — |

## 8. Search record (sections 1–2 absence claims)

- **Seed set (mined before searching):**
  - the landscape report and its references;
  - `~/dev/kb/INDEX.md`, which led to `spice/mlcc-vendor-curves-can-be-their-own-model-output.md` and in turn to vendorpull;
  - `access/infineon-cdn-ua-block.md` and `access/st-pdf-needs-accept-encoding.md`;
  - dsdig `hornresp_spl.py`, `powder_core_loss.py` (r-semis) and `out/vlm-chart2table/`.
- **RF-CDA release (2026-09-29):**
  - GitHub repository search: "RF-CDA" (3 unrelated hits), "RF characteristic curve digitization" (0), "rf curve digitization" (0).
  - HF dataset search: "RF-CDA" and "rf curve" (0).
  - Zenodo API: `"RF-CDA"` (0), and characteristic curve AND RF AND digitization (0).
  - Serper: 3 queries on the title and author names.
  - Pages checked through Fab's Chrome: SSRN abstract page ("All rights reserved"; no data link); SciProfiles for Keqiang Yue (lists the preprint; no dataset or social links); Google Scholar for Yaqi Wang (not listed); ResearchGate (blank).
  - **No release found.** Confidence medium, limited by the Chinese-language web and by ResearchGate not rendering.
- **Log-axis absence in the synthetic sets:** counted over full files or stated samples (section 1). High confidence for the files counted.
- **Challenge searches:**
  - "Infineon OptiMOS datasheet diagrams generated from simulation" (r-semis): no refutation.
  - For "public benchmarks lack log axes", the refuting evidence found was ChartZero (unreleased), which is reported.

## 9. Source access log

Columns: identifier · evidence state · attempts · route · validation · identity · load-bearing.
- Rows marked (r-*) come from the sub-agents' logs, summarised here.
- The complete per-URL logs are in `/tmp/bench-research/child-r-datasets.md` and `/tmp/bench-research/child-r-semis.md`, and their download manifests are in `out/benchmark-data/{datasets,vendors}/MANIFEST.tsv`.

| ID | Identifier | State | Attempts | Route | Valid. | Identity | LB |
|---|---|---|---|---|---|---|---|
| B1 | https://pimapi.murata.com/public/api/pim/v1/characteristics/search/technical?partNum=GRM188R71H104KA93%23&productCategoryId=ceramicCapacitorSMD&displayLanguage=en&languageRegion=en-global | inspected | curl Chrome UA, 200, %PDF 165,707 B | raw + pixels | validated | anonymous | yes |
| B2 | pimapi `products/search` + `characteristics/characteristics` (via vendorpull `murata.py`) | inspected | urllib POST 200 JSON | raw | validated | anonymous | yes |
| B3 | https://ds.yuden.co.jp/TYCOMPAS/or/download?pn=MCASG21GSB7105KTNA01&fileType=Datasheet; web data from the vendorpull store (`store/ty/MCASG21GSB7105KTNA01.tar.zst`) | inspected | curl 200 %PDF 139,800 B; store decoded with `.zdict` | raw vector | validated | anonymous | yes |
| B4 | https://physics.nist.gov/cgi-bin/Xcom/xcom3_1 (Si) + graph PNG; https://physics.nist.gov/PhysRefData/Xcom/Text/XCOM.html | inspected | headless form submit 200; first graph URL expired (temporary), re-run with in-session fetch 200 PNG | DOM + pixels | validated | anonymous | yes |
| B5 | https://www.nature.com/articles/s41467-025-66210-z, doi:10.1038/s41467-025-66210-z; MOESM3 zip | inspected (Fig. 1b, licence) | headless 200; curl zip 200 6.0 MB; PDF 200 | raw | validated | anonymous | yes |
| B6 | https://www.petoindominique.fr/pdf/hornresp_manual.pdf | partial (export sections) | curl 200 %PDF 5.96 MB | raw text | validated | anonymous | no |
| B7 | http://www.hornresp.net/ → https://www.hardware-test.de/mcbean/ | inspected | https://www.hornresp.net ERR_CONNECTION_REFUSED (URL I composed); search found the http URL; frameset → headless 200 | DOM | validated | anonymous | no |
| B8 | arXiv:2402.12185 (ChartX) | partial (§3.4, App. B.2) | curl 200 %PDF | raw | validated | anonymous | yes |
| B9 | arXiv:2605.05820v1 (ChartZero) | partial (§4.1, Table 2) | curl 200 %PDF (r-datasets and parent) | raw | validated | anonymous | yes |
| B10 | Infineon IPS app and `/api/feature`; BSC010N04LS and IMBG65R048M1H datasheets; IPS guideline; usage terms (r-semis) | inspected | rung 4 + XHR capture; 504 on one feature; PDFs by in-page fetch. Parent re-computed IPS interpolation and the datasheet vertex fit | raw JSON + vector | validated | anonymous | yes |
| B11 | Nexperia PSMN1R0-40YLD interactive datasheet (`api.nexperia.com`) + PDF (r-semis) | inspected | rung 4 + response capture; PDF rung 1 | raw SVG + vector | validated | anonymous | yes |
| B12 | Magnetics Powder Core Catalog (mag-inc.com, `?ext=.pdf`) (r-semis) | inspected (pp. 48, 63, 66, 109–111) | curl 200; sha equals Fab's local copy | raw + vector | validated | anonymous | yes |
| B13 | Micrometals mix-26 datasheet (S3) (r-semis) | inspected | rung 4 page; rung 1 PDF | raw + vector | validated | anonymous | yes |
| B14 | arXiv:2606.31345v1 (figverify), local `out/chart-digitisation-refs/` | partial (§7, §8, §10, availability) | local file | raw | validated | local | yes |
| B15 | SSRN 7084283 (RF-CDA), local copy (byte-identical to the copy Fab supplied, per coordinator) | partial (§A.6, Availability) | anonymous rungs 1–5: Cloudflare Turnstile (r-datasets). Fab's Chrome 2026-09-29: abstract page loaded (title, "All rights reserved") | raw / DOM | validated | local + real profile, gate AUTHORIZED 2026-09-29T21:11:39Z | yes |
| B16 | OpenReview b0B6JQF8Xj (Chart2CSV); anonymous.4open.science repos | inspected | anonymous rungs 1–5: Turnstile (r-datasets). **Fab's Chrome: forum loaded** (withdrawn 2026-01-04, CC BY 4.0). Repos: headless "The repository is expired" | DOM | validated | real profile (gate as B15) / anonymous | yes |
| B17 | arXiv:2609.19377 (LinePilot/DigitizerBench), local | partial (§4.1–4.3, Table 3) | local file | raw | validated | local | yes |
| B18 | CHART-Info metric.pdf, local `chartinfo_metric.pdf` | inspected (§1.1–1.3) | local file | raw | validated | local | yes |
| B19 | arXiv:2212.10505 (DePlot), local | partial (§3.1) | local file | raw | validated | local | yes |
| B20 | arXiv:2605.27298 (WB-ChartExtract), local | partial (App. A) | local file | raw | validated | local | yes |
| B21 | arXiv:2404.09987 (OneChart), local | partial (Eq. 6) | local file | raw | validated | local | no |
| B22 | arXiv:2606.29808 (ExChart), local | partial (§5.4) | local file | raw | validated | local | no |
| B23 | arXiv:2604.28039 (SpecVQA), local | partial (§3.3, Eq. 2) | local file | raw | validated | local | no |
| B24 | https://www.plexim.com/download/thermal_models | inspected (vendor list) | headless 200 | DOM | validated | anonymous | no |
| B25 | https://www.st.com/en/power-transistors/sctw35n65g2v.html; …/support/resources/calculators.html; …/simulators.html | partial | anonymous rungs 1–6 failed (r-semis); **Fab's Chrome, entered via the ST homepage: all rendered** | DOM | validated | real profile (gate as B15) | no |
| B26 | https://www.onsemi.com/ → /sspmg | metadata (login wall) | r-semis rung 4; Fab's Chrome: MyON SSO login | DOM | validated | real profile | no |
| B27 | https://navitassemi.com/ ; https://genesicsemi.com/ ; Wayback CDX genesicsemi.com | not obtained (PLECS files) | curl shell 537 B; headless empty; Fab's Chrome ×3 (incl. visibility override via `browser_run_code_unsafe`) empty body; genesicsemi HTTP 500 ×2 (20 s apart); CDX lists 2022–23 PLECS zip URLs as 301 records | DOM / raw | validated (empty render) | anonymous + real profile | no |
| B28 | SciProfiles 1431666; Google Scholar ExJ16joAAAAJ; ResearchGate 408625622 | inspected / inspected / not obtained (blank) | SciProfiles anonymous headless 403 "Access Denied" → Fab's Chrome loaded; Scholar and ResearchGate via Fab's Chrome | DOM | validated | real profile | no |
| B29 | GitHub / HF / Zenodo APIs and Serper queries (RF-CDA, GeneSiC, Hornresp, Nature source data, Coilcraft) | discovery | API JSON | raw | validated | anonymous | no |
| B30 | r-datasets rows: HF API records and files, CHART-Info/UB-PMC zips (range reads, CRC OK), AdobeSynth19 JSON, LineEX Drive, PlotQA, FigureQA, DVQA, Engauge, WPD, figverify, ExChart, ChartRecover, Starrydata, augraphy etc. | as in the child log | rungs 1–4; two headed scratch-profile runs | raw / DOM | validated | anonymous | yes |
| B31 | r-semis rows: Wolfspeed (click-wrap accepted), EPC, ROHM, TI, CSC, Microchip (rung 5 OK), Toshiba (click-wrap not accepted), local datasheet survey | as in the child log | rungs 1–5 | raw / DOM | validated | anonymous | yes |

**Browser lifecycle.**
- **Standalone Playwright:** `chromium_headless_shell-1208` in launch mode, closed in `finally`. Headed runs in the children used `tempfile` profiles, which were deleted afterwards (children's logs).
- **Real-profile route:** 5 MCP sessions through `browser-profile-gate.sh serve` (gate AUTHORIZED 2026-09-29T21:11:39Z). Each session: tabs listed 1, closed 1, held 0, failed 0; server exit 0. No handoff is pending and no served endpoint is left running.
- **Token:** the token was passed only as a process environment variable. It is not in any file I wrote, and helper output redacts `token=`.
- **Processes I did not start:** an `@playwright/mcp --extension` process from another session (`mcp_ext.py /tmp/parent-batch-3.tsv` under a `bench-psu-ext` lock) was running. It is not mine and I left it alone.

**Delegation record.**
- **Children:** `r-datasets` and `r-semis`, both general-purpose agents that loaded the online-research skill. Both completed successfully with full reports and access rows.
- **Steering:** message #1 was posted to r-datasets (Chart2CSV/RF-CDA escalations resolved from local texts). It was acked 1/1. `steer.py status`: 2 expected, 2 polled. The parent's posted-id list is {1}, which matches.
- **Re-verified by the parent:**
  - ChartZero Table 2 counts;
  - the Engauge sample list;
  - the anonymous-repo expiry;
  - the Infineon IPS interpolation and the datasheet vertex affine fit;
  - the RF-CDA release statement.

  Other child figures are second-hand, as marked.
- **Harness limit:** the harness exposes no per-agent turn limit, so delegation was unbounded.

**Archive.**
- Everything is in `out/benchmark-data/` (gitignored via `out/`, checked with `git check-ignore`), 481 MB in total. Every downloaded dataset is under 200 MB, and no multi-GB set was downloaded.
- Checksums: `SHA256SUMS` covers every file (5,842 lines).
- Per-item URL and licence: `MANIFEST.tsv` at the top level (my items) and in `datasets/` and `vendors/`.
- Licensed vendor and CC BY-NC-ND material is for local evaluation only and must not be redistributed or committed.
