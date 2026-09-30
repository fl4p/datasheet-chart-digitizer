# Chart and plot digitisation: state of the art as of September 2026, for dsdig

Research date: 2026-09-29. Scope: papers, benchmarks, open-source tools and ML models that turn chart images or PDF vector charts into numbers, judged against dsdig's inputs (MOSFET, diode and magnetics datasheet charts, mostly vector, some raster or scanned) and its target (about 1 px or 0.5 % of axis span).

Conventions. `[Sn]` is a source ID from the source access log (section 11). Every number carries its source and locator. "Self-reported" means a model card, vendor page, or the authors' own benchmark. "Inference" marks my reasoning, not the source's words. The sources were read through their text layer (`pdftotext`) or the rendered DOM. I did not digitise any figure.

## Executive summary

1. **Nothing published beats dsdig's target on dsdig's inputs.** The best published accuracies by route:
   - Vector extraction: median relative error about 0.09 % on published figures [S1 §8] and 0.11 % mean [S2 Table 2].
   - Expert-driven raster tool: WebPlotDigitizer at 300 dpi, 0.50 % mean [S2 Table 2].
   - Automatic raster on MOSFET datasheets: 1.61 % of y-range [S26 Table III].
   - VLMs on line charts: 4.35 to 6.57 % of Vmax [S12 Table 4]. One self-run exception: Gemini-3 put 88 % of points within 1 % of chart size on RF curves [S35 Table 2].
2. **ADOPT: calibrate on gridlines and tick strokes, never on label-glyph centres.**
   - Three systems do this: ChartDetective uses a nearby gridline instead of the tick ("slightly more accurate") [S2 §5.5]. PowerBrain matches OCR'd tick numbers to detected grid lines [S26 Alg. 2]. LinePilot snaps clicks to stroke centres [S49 §3.1].
   - The most-cited baselines do the opposite. CHART-Info 2024 [S17 §4.4] and figverify [S1 App. C] both use label bounding boxes.
3. **ADOPT: figverify's axis gate** [S1 App. C]:
   - Fit both a linear and a log map per axis, keep the one with the lower residual, and refuse the axis if that residual exceeds a few points or fewer than two ticks parse.
   - Fuse "10"+"k" into 10^k only when the exponent is set smaller *and* raised. That rule would address dsdig's "102" bug (inference).
4. **ADOPT: a re-render certificate.** Re-plot the extracted curves and diff them against the source, as an automatic gate before human review. Precedents: figverify stage 3 [S1 §5–6], KM-GPT [S39], and RF-CDA's closed loop [S35].
5. **EVALUATE: figverify** (MIT, PyMuPDF "generic" backend) on about 10 dsdig vector panels, as an independent cross-check.
   - Its bit-exact mode is matplotlib-only. On other renderers it degrades to "calibration-limited" [S1 Table 1].
   - It does not claim to separate crossing curves.
6. **EVALUATE (already running): LineFormer, as a raster second opinion only.**
   - It scores 88.25 task-6b on real PMC charts [S3 Table 1].
   - A later paper reports it "very poor" on black-and-white gridded charts, with many false positives [S30 §4.5].
   - A battery fine-tune card reports +62 % over-detection before tuning (self-reported) [S43].
   - Its metric is error relative to |y|, capped at 1 [S4], so it cannot be compared with a px or span target.
7. **IGNORE chart-to-table VLMs as the numeric extractor** (DePlot, MatCha, UniChart, ChartGemma, OneChart, TinyChart, ChartMoE, PP-Chart2Table, granite chart2csv).
   - Their benchmarks score at 5–10 % tolerance [S13, S14, S15, S11].
   - None of the benchmarks I checked evaluates log axes (section 5.3).
   - On line charts from an independent benchmark: TinyChart scores 18.32 RMSF1 and Gemini 2.5 Pro 87.68, at 10 % tolerance [S11 Table 3].
8. **IGNORE:**
   - WebPlotDigitizer v5 AI Assist: closed source, GPT-4o backend, and it does not place calibration points [S5].
   - Engauge, g3data, DataThief: manual, and orphaned, unmaintained or retired [S8, S47, S46].
9. **The vector-PDF literature is thin but not empty.** Four systems read a chart's own vector drawing instructions: [S2, interactive], [S1], [S40, Kaplan–Meier only] and [S34, a 2-page poster]. [S10] samples user-traced SVG paths. The poster found that over 70 % of 40,000 CS-paper figures were embedded as vector graphics. Its path clustering scored 91 %/90 % precision/recall on colour graphs but only about 60 % on black-and-white ones [S34 §1, §4]. Datasheets are mostly monochrome, so the B/W case is the relevant one (inference).
   - None targets datasheets.
   - None claims automatic series identity through crossings for arbitrary renderers.
   - The one MOSFET-datasheet system decoded the PDF vector data only to build ground truth, and extracted from raster [S26 §IV.B].
10. **Validation gap and fix.**
    - No paper reports calibration error against gridlines as a metric. None handles misprinted tick labels beyond consistency rejection [S49 §3.1, S1 App. C].
    - Adopt LinePilot's FPC-NRMSE [S49 Eq. 1]: Hungarian matching, span normalisation, unit loss on failure. Compute it in display space (decades on log axes) so dsdig's numbers become comparable.

## 1. Read this before comparing any number to dsdig

The field uses at least seven incompatible error definitions (section 7). They differ in denominator, and some cap or threshold the error. Two consequences follow for dsdig.

- **Scores relative to |y| (CHART-Info 6b, RMS, RNSS)** penalise a 1 % error near y≈0 enormously and a 1 % of span error near the top hardly at all [S4, S13 §3.1]. On log axes, which make up most of dsdig's capacitance and leakage work, none of these reduces to "decades of error".
- **Thresholded scores (RMS θ=10 %, ChartQA 5 % relaxed accuracy, OneChart AP@5 %/10 %, RF-CDA ε=1/3/5 %)** say nothing below the threshold [S13, S14, S15 Eq. 6, S35 §4.1]. A model scoring 0.9 at 10 % tolerance can still be off by 5 % everywhere.
- The closest to dsdig's "fraction of axis span" are:
  - PowerBrain's `|ŷ−y|/(max y−min y)` [S26 Eq. 8];
  - ExChart's `|v̂−v|/Vmax` [S12 Eq. 2];
  - Chart2CSV's range-normalised MASE [S52 §4];
  - LinePilot's NRMSE over range [S49 Eq. 1].
- All four normalise in data units. On a log axis, "% of range" in data units is dominated by the top decade (inference). None of these four papers evaluates log axes: grep finds no log-axis text in [S12] or [S49], and [S49 §5.3] states linear axes only. PowerBrain's text does not mention log axes (grep).

## 2. Pipeline research: axes, legends, curve separation, crossings

### 2.1 Comparison

| Work | Year | What it does | Reported accuracy, and on what | Licence / code | Maintained? | Relevance to dsdig |
|---|---|---|---|---|---|---|
| ChartOCR [S23] | 2021 (WACV) | Keypoint detection (CornerNet) with rule-based assembly for bar, line and pie | Line task-6b on UB-PMC22 is 72.9, as re-measured by LineFormer [S3 Table 1] | BSD-3 (soap117/DeepRule); last push 2023-07 [S57] | No | Low: keypoints misalign at crossings [S3 §3.1] |
| LineEX [S22] | 2023 (WACV) | ViT keypoints; lines grouped by matching legend patches | 6b 47.03 on UB-PMC22, ignoring legend-less charts [S3 Table 1 note d]; FPC-NRMSE 0.997 on DigitizerBench [S49 Table 3]; 5.57 % mean error on 170 MOSFET figures [S26 Table III] | Apache-2.0; pushed 2025-07 [S57] | Dormant | Low: needs a legend; datasheets often use in-plot labels |
| LineFormer [S3] | 2023 (ICDAR) | Mask2Former-style line **instance segmentation** (Swin-T), then masks sampled at δx; ground-truth masks 3 px thick | 6a/6b: AdobeSynth19 97.51/97.02; UB-PMC22 93.1/88.25; LineEX data 99.20/97.57 [S3 Table 1] | **No licence file** (GitHub API reports none); pushed 2025-11 [S57] | Low activity | Medium, as a raster second opinion (section 6). The missing licence blocks redistribution (inference) |
| ChartDete / CACHED [S53] | 2023 (ICDAR) | Context-aware detection of 18 chart element classes, including tick labels; no plot elements | Detection mAP on refined PMC [S53] | MIT [S57] | Low | Low: calibration rides on label boxes |
| ChartDETR [S21] | 2023 | DETR-style multi-shape keypoint-set detector | Line score 0.975 vs ChartOCR, better by 6.2 % [S21, partial read]; Adobe-synthetic F1 0.98 vs 0.71 | Not checked | ? | Low |
| Line Graphics (LG) dataset [S20] | 2023 (ICDAR) | 520 images with pixel labels for 10 fine categories, including spines | SegNeXt 67.56 % mIoU; spine categories hardest [S20] | Code on GitHub (moured) | ? | Low; possibly training data for spine and frame detection |
| Yang, He, Zhang (Graphical Models) [S30] | 2025 | Mamba-enhanced mask-guided curve instance segmentation, YOLOv9 elements, custom LSTM chart OCR | 6b-style (pixel): PMC 91.03 vs LineFormer 87.70; AdobeSynth19 99.17 vs 83.10 [Table 1]. **Log, exponential and percentage axes filtered out of the axis-unit evaluation** [§4.7] | Data on request | New | Medium: documents LineFormer failing on B/W grid charts [§4.5] and OCR misreads (0.5→5, 5.6→56) [§3] |
| AI-ChartParser [S31] | 2025 (CGF) | Multi-task element, pivot-point and curve detection; "Interval-Mean Space-Numerical Mapping" | Paper not obtained. Independent measurement: 0.1387 of points within 1 % on RF curves [S35 Table 2] | Repo ywking/ChartParser has no licence [S57] | ? | Low |
| KM-GPT [S39] | 2025 | GPT-5 plus OCR for labels; K-medoids pixel clustering; greedy path tracing; **k-NN assignment with confidence for entangled curves**; re-plot validation | Median absolute error 0.005 (95 % CI 0–0.034) on survival probability, synthetic [S39 Results] | Web app listed at km-gpt.wse.jhu.edu (search listing, not checked) | ? | Medium: confidence-scored assignment at crossings; re-plot gate |
| LinePilot + DigitizerBench [S49] | 2026-09 | Colour runs per column, then a **dynamic-programming path** that rewards support and thickness and penalises jumps and gaps. Three calibration modes; "enhanced" snaps each click to the stroke centre | FPC-NRMSE, automatic: LinePilot-OCR 0.672, ChartOCR 0.953, LineEX 0.997. Human-guided: LinePilot-enhanced 0.081, PlotDigitizer Pro 0.097, WPD 0.164, Engauge 0.790 [Table 3]. Synthetic, **linear axes only** [§5.3] | Supplementary material on GitHub [S49 refs]; licence not checked | New | **High** for calibration and metric ideas |
| SALSA [S50] | 2026-09 | Literature assistant; chart route = ChartDete calibration → LineFormer series, plus GUI correction | No digitisation accuracy found (grep) | ? | New | Low: confirms ChartDete+LineFormer as the de-facto open raster stack |
| RF-CDA [S35] | 2026-07 (preprint) | Agent for RF characteristic curves: scenario routing, pixel-level extraction, **reconstruction-based verification** | Held-out set of 5,856 images. Fraction of points within 1 % of max(W,H): RF-CDA 0.9419, Gemini-3 0.8800, GPT-5 0.4824, Qwen-3-VL 0.3368 [Table 2]. **Self-run; references are human-annotated** | SSRN; "no reuse allowed" | New | **High** as a domain analogue (RF datasheet curves); self-run |

### 2.2 How the literature treats dsdig's known failures

| dsdig failure (measured this week) | Closest published treatment |
|---|---|
| Calibration from label-glyph centres (up to 12 px, ≈0.035 decade on ST layouts) | **Same practice in the baselines.** The CHART-Info 2024 Task-4 baseline puts ticks at label-box centres plus a detected axis corner, because "tick marks are not visible or do not correspond" [S17 §4.4]. figverify calibrates from label bounding boxes [S1 App. C]. **The opposite, which is what dsdig needs:** ChartDetective prefers a nearby gridline [S2 §5.5]; PowerBrain matches tick numbers to detected grid lines [S26 Alg. 2]; LinePilot-enhanced snaps to the stroke centre, keeps the click if no stroke is found, and rejects large moves [S49 §3.1]; VEC-KM finds axes from line geometry [S40]. |
| Tracer follows the wrong curve after a crossing (Ciss/Coss swap) | Raster: instance masks [S3], DP path with jump penalty [S49], confidence-scored k-NN [S39], predicted-position point linking [S26 Alg. 1]. Vector: series identity comes from the drawing stream, as path objects [S2, S40] or per-series blocks [S1 §6]. I found no paper that evaluates crossings specifically on log–log charts. |
| Tracer latches onto the frame baseline (Crss) | VEC-KM classifies axis lines by length and position before choosing curves [S40]. The JCDL poster treats horizontal or vertical black/grey paths spanning at least 70 % of the figure as axes or gridlines (the four nearest the boundary are the axes) and lines crossing the axes as ticks, before any curve clustering [S34 §4]. LineFormer's false positives on gridded B/W charts [S30 §4.5] and the battery card's over-detection "from text annotations, legends, and axis labels" [S43, self-reported] show that segmentation does not solve this by itself. |
| Touching curves merge | PowerBrain reports two close overlapping lines both recovered where LineEX missed one [S26 §IV.B.2, qualitative]. LineFormer's premise is exactly this case [S3 §3]. |
| Plot box stops at a legend or includes a neighbour panel | figverify segments panels from drawn frame rectangles, with greedy de-overlap [S1 App. C]. Plot2Spectra refines the plot box with an edge-based loss [S37]. |
| "10²" parsed as "102" | figverify fuses a base "10" with an exponent only when the exponent span is **smaller and raised above the baseline**, so neighbouring "10" and "15" never become 10¹⁵ [S1 App. C]. Documented OCR misreads elsewhere: "20"→"2" in 3 of 170 MOSFET figures [S26 §IV.B.2]; 0.5→5 and 5.6→56 [S30 §3]. |
| Dense log grids, dual y-axes, misprinted ticks | CHART-Info **ignores secondary (right and top) axes** in its tick metric [S17 §4.4]. Dual axes are future work in [S30 §5]. For misprints, LinePilot-OCR rejects inconsistent readings "when the remaining ticks support a common scale" [S49 §3.1]; figverify refuses panels whose fit residual exceeds a few points [S1 App. C]. I found nothing that detects and *corrects* a misprinted label (bounded absence, section 10). |

## 3. Vector-PDF chart extraction (dsdig's primary path)

### 3.1 Comparison

| Work | Year | What it does | Accuracy and data | Licence | Maintained? | Relevance |
|---|---|---|---|---|---|---|
| **figverify** [S1] | 2026-06 | Parses the PDF content stream directly, following graphics state through form XObjects; segments series by stream blocks; calibrates from tick labels; certifies by re-rendering | Calibration dominates error, "10⁻³ to 10⁻⁴ of the axis range" [§4.3]. Median relative error about 0.09 % on published figures with deposited data; MATLAB 0.12 %, OriginLab 0.25 % [§8, Table 1]. PDF→SVG conversion "discards roughly ten to fourteen bits" [§2, §8] | MIT; 3 commits; pushed 2026-06-24 [S57] | New, single author | **High**: independent cross-check and certificate design. Weak on dsdig's hard parts (label-box calibration, no crossing logic claimed) |
| **ChartDetective** [S2] | 2023 (CHI) | Interactive: the user drags vector shapes onto roles (series, axis, legend); PDF.js; uses **gridline position over tick position** | Mean relative error 0.11 % (vector) vs WPD at 300 dpi 0.50 % (raster), n = 42 charts; in-the-wild subset 0.13 % vs 0.68 % [Table 2]. Excel charts are internally approximate (0.24 %) [§7.4] | Code availability not stated in the text I read | Research prototype | Medium: supports gridline-seated calibration and path-as-series |
| **RESOLVE-IPD / VEC-KM** [S40] | 2025–26 | Kaplan–Meier curves from journal SVG or native PDF paths. The top-K longest connected paths are the curves; axes are lines whose length matches the curve extent; censor marks are short segments | "up to six decimal places"; RMSE 0 on simulated plots [§ results] | GitHub JackZhao0312/RESOLVE-IPD | New | Medium: geometry-based frame and axis classification (the Crss-baseline issue) |
| Curve separation for line graphs [S34] | 2016 (JCDL poster) | PDF page → SVG via Inkscape; paths flattened into atomic commands with transforms resolved; axes and gridlines picked by geometry; colour graphs clustered by colour; B/W graphs use marker-shape groups plus style clustering | Visual evaluation of 200 colour and 200 B/W graphs from CiteSeerX: colour precision/recall 91/90 %, **B/W about 60 %**; 5–8 s per figure [§4]. Over 70 % of 40,000 figures were vector [§1] | Code: github sagnik/linegraph-curve-separation | No | Medium: the monochrome-vector case is dsdig's case, and it was the weak spot |
| svgdigitizer [S10] | active | The **user** traces curves as splines in a prepared SVG; supports skewed axes; `paginate` renders PDF pages to SVG | none | GPL-3.0; pushed 2026-09-18 [S57] | Yes | Low (manual) |
| PowerBrain ground truth [S26] | 2024 | Decodes datasheet PDFs' embedded vector data **as ground truth**, while the tool itself extracts from raster | — | — | — | Shows that vector data exist in many MOSFET datasheets and that a datasheet group still did not use them for extraction |
| pdfplumber discussion #1079 [S45] | — | Practitioner reply: axes and points extractable with heuristics, but curve control points were not extracted ("pdfminer problem – may have been fixed") | — | MIT (pdfplumber) | — | Caution if dsdig ever relies on pdfminer for Bézier paths |

### 3.2 Is the vector literature thin? Yes, with the search record

Bounded-absence statement (§3 of the research contract):

- **Indexes searched on 2026-09-29:** Google Scholar via Serper; Google web via Serper; GitHub repository search; arXiv API; OpenAlex.
- **Queries:**
  - "extracting data from vector PDF charts drawing paths curves"
  - "chart reverse engineering vector graphics PDF SVG data extraction"
  - "digitize plot from PDF vector graphics pymupdf get_drawings curve extraction"
  - "automatic extraction of data from vector graphics charts in PDF documents drawing operators"
  - "datasheet vector graphics curve extraction PDF path semiconductor"
  - "extract line chart data from PDF vector paths without rasterization automatic series"
  - GitHub: "vector chart pdf extract data", "datasheet curve digitize", "pdf plot data extraction vector paths", "svg chart data extraction", "chart digitizer pdf pymupdf". **Each returned zero repositories**, except "datasheet chart digitizer", which returned only fl4p/datasheet-chart-digitizer.
- **Citation chaining:** followed [S1] §2 and [S2] §2.
- **Found:** [S1], [S2], [S40], [S34], [S10], plus SynthIPD (cited by [S40]; not read) and Futrelle's 2000s "Graphics recognition in PDF documents" (diagram classification, not data extraction; not read).
- **Not found:** any vector-path extractor evaluated on semiconductor datasheets, or any that claims automatic series disambiguation through crossings on non-matplotlib renderers.
- **Confidence in that absence:** medium. It is limited by non-English literature and by paywalled datasheet papers I could not read ([S28], [S29], [S32]); their abstracts do not mention vector parsing.

## 4. Open-source and free tools

| Tool | Licence | Activity (GitHub API, 2026-09-29) | Automatic mode | Log axes | Crossing curves | Relevance |
|---|---|---|---|---|---|---|
| WebPlotDigitizer v4 [S6, S7] | AGPL-3.0 | pushed 2026-07-27; last commit 2025-08-26 | Colour-mask algorithms: averaging window, X-step (with and without spline), template matching, blob [S6] | Yes, as a calibration option [S6] | No series identity: the averaging window works on one colour mask, averaging vertical blobs per column and then merging within dx/dy [S7 `averagingWindowCore.js`]. So same-colour crossing curves are not separated (inference from code) | Reference baseline; 0.50 % expert error at 300 dpi [S2] |
| WebPlotDigitizer v5 [S5] | Closed ("future developments … closed source") | 2024-05 post | "AI Assist" (GPT-4o plus custom models) detects calibration type, axis labels and datasets, but "does not make an attempt to pin point … the calibration points" | — | — | Ignore |
| Engauge Digitizer [S8] | GPL-2.0 (fork) | Original author deleted repo and site; akhuettel fork v12.9 (2025-06-23, Qt6, beta) | Line and point extraction, grid removal | Yes (log fixes in release history) | Not documented | Ignore; DigitizerBench scored it 0.790 FPC-NRMSE human-guided [S49 Table 3] |
| PlotDigitizer (dilawar, pip) [S9] | GPL-3.0 | pushed 2026-08 | Batch CLI; 3–4 calibration points given as pixels | Not in README | Single trajectory | Ignore |
| PlotDigitizer.com / Pro [S48] | Commercial | — | Autotrace: cluster, points, curves, bar, edge, centroid, skeleton | log10, ln, date, reciprocal [vendor page] | — | Best human-guided score in DigitizerBench (0.097) [S49] |
| g3data [S47] | GPL-2.0 | last push 2018-12 | Manual | — | — | Ignore |
| DataThief III [S46] | Now free, retired | "DataThief is retired" | Tracing | linear, log, polar, user-defined | — | Ignore |
| Plot2Spectra [S37] | GPL-3.0 (MaterialEyes/Plot2Spec) | pushed 2026-04 | Automatic: detector for the plot box, OCR on **x** ticks only, segmentation plus optical-flow tracing | Not claimed | Handles "significant overlap" (qualitative) | Low; RMSE 0.018 on [0,1]-normalised data; fails on sharp peaks |
| SurvdigitizeR [S38] | NOASSERTION | pushed 2025-12 | Automatic KM: lightness threshold, colour separation, overlap repair | Linear (survival) | Colour-based | Validation methodology only |
| PlotRedox (Rust) | MIT | pushed 2026-05 | Mask-assisted axis-line and tick detection; linear and log | Yes | — | Low |
| NathanaelRea/Plot-Digitizer | GPL-3.0 | — | User traces Bézier curves in Inkscape; the tool samples them | — | — | Low |
| extract-chart-data-grounded (agent skill) | "other" | 2026-07 | Checklist skill: calibration JSON, grounding overlay, reconstructed plot | — | — | Its rule "do not use it when vector source data … is available" matches dsdig's ordering |

## 5. ML models and benchmarks, 2022–2026

### 5.1 Models

| Model | Year | What it does | Numeric fidelity I could source | Licence | Relevance |
|---|---|---|---|---|---|
| DePlot [S13], MatCha [S14] | 2022–23 | Pix2Struct-based plot-to-table and derendering pretraining | On WB-ChartExtract (dense, unlabelled), DePlot RMSF1 is 23.06 [S11 Table 1] | Apache-2.0 (google-research/pix2struct) | Ignore |
| UniChart, ChartGemma | 2023–24 | Chart pretraining and instruction tuning for QA and summaries | ChartGemma scores 37.1 on IBM's internal Chart2CSV judge (self-reported by IBM) [S41] | MIT / GPL-3.0 [S57] | Ignore |
| OneChart [S15] | 2024 | 0.2B; an auxiliary `<Chart>` token and number decoder give a **confidence score** | ChartX line charts (no value labels) AP@strict/5 %/10 %: 49.30/59.79/65.25 [Table 2] | Apache-2.0 | Ignore as extractor; the self-confidence idea is interesting |
| TinyChart | 2024 | 3B, program-of-thought, token merging | WB-ChartExtract line charts: 18.32 RMSF1 at 10 % tolerance [S11 Table 3]. Over 20 runs, 99.4 % of charts gave at least one differing extraction [S11 §1] | Apache-2.0 (mPLUG-DocOwl) | Ignore |
| ChartMoE | 2024–25 (ICLR) | Mixture-of-experts connector aligned through chart-to-table | No independent numeric extraction figure found | No licence (DataArcTech/ChartMoE) | Ignore |
| ChartReader | 2023 | Transformer component detection plus derendering | — | — | Ignore |
| ChartCoder, ChartMimic-style chart-to-code | 2025 | Chart to matplotlib code | Scored on code and appearance, not values | No licence [S57] | Ignore |
| PP-Chart2Table [S42] | 2025 | 0.58B chart parser in PaddleOCR | 80.60 on an **internal** 1,801-sample set, "no plan for public release" (self-reported) | Apache-2.0 | Ignore |
| granite-vision-3.3-2b-chart2csv [S41] | 2025–26 | Granite Vision 2B fine-tuned for CSV; Docling integration | 60.1 on an internal benchmark **scored by an LLM judge** (self-reported) | Apache-2.0 | Ignore |
| ExChart-7B [S12] | 2026 (CHI) | Qwen2.5-VL-7B trained first on coordinate perception, then chart-to-table | Adaptive MAPE (÷Vmax), line charts: 7.93 %; overall 4.87 % [Table 4] | CC BY-NC-ND paper | Ignore; the authors say "the model alone cannot serve as a dependable extractor" [§6] |
| Frontier VLMs (Gemini, GPT, Claude) | — | General VLMs | Line charts, Adaptive MAPE: Gemini 2.5 Flash 4.35 %, GPT-4.1 6.43 % [S12 Table 4]. WB-ChartExtract line RMSF1 at 10 %: Gemini 2.5 Pro 87.68, GPT-5.1 38.53 [S11 Table 3]. RF curves within 1 %: Gemini-3 0.88, GPT-5 0.48 [S35 Table 2] (self-run) | API | See 5.2 |
| Local chart2table-qwen3.5-4b and Qwen3.5-4B | 2026 | — | Already measured by Fab: 15–19 % median error on transfer charts | — | Not re-reported |

### 5.2 Benchmarks that measure numeric fidelity

| Benchmark | Year | Data | Metric and tolerance | Best line-chart result | Log axes? |
|---|---|---|---|---|---|
| CHART-Info (ICDAR 2019, ICPR 2020 and 2022, dataset 2024) [S17, S3, S4] | 2019–24 | AdobeSynth19 (matplotlib), UB-PMC (real) | 6a/6b: capped relative-to-\|y\| error, interval-weighted, bipartite match, F1 [S4] | Competition results: ICDAR 2019 received **no Task 6 submissions** [S56 §III.F]. ICPR 2020, UB PMC line charts, Task 6b (ground-truth axes and legend given): best combined score 0.698; Task 7 end-to-end line data score 0.626; Adobe Synth line 0.987 [S55 Tables 9–10]. ICPR 2022: the only 6b entry was box plots [S69]. Later papers: LineFormer UB-PMC22 6b 88.25 [S3]; [S30] 91.03 | Axis scale type (linear/log/other) is annotated in the data [S55 §2], but no line-chart score is broken down by it |
| ICDAR 2023 CHART-Info competition [S18] | 2023 | — | — | **Cancelled** ("Apr 13, 2023 Competition is cancelled") | — |
| ICDAR 2025 [S19] | 2025 | — | — | **No chart competition** among the 9 listed (closed list, whole page read) | — |
| ChartQA / PlotQA relaxed accuracy [S14] | 2022– | QA | exact match within 5 % | saturated (TinyChart 95.20 RMSF1 on ChartQA) [S11 Table 1] | — |
| RNSS, RMS [S13] | 2022 | tables | RNSS gives "credit to very high relative errors" [§3.1]; RMS with θ = 10 % in [S11 §4.2] | — | — |
| WB-ChartExtract [S11] | 2026 | 1,000 synthetic charts from World Bank data, 4 libraries, no value labels | RMSF1, θ = 10 % | Gemini 2.5 Pro 87.68 (line) | No |
| ExChart-Bench [S12] | 2026 | real and synthetic, 33,757 values | Adaptive MAPE = mean \|v̂−v\|/Vmax, capped at 100 % | Gemini 2.5 Flash 4.35 % | No (grep: 0 hits) |
| Chart2CSV [S52] | 2025, withdrawn from ICLR 2026 | 812 charts, 5 domains (275 plots, per reviewer SFjA's summary) | plot precision = 1 − min(\|ŷ−y\|/range, 1), unpaired = 1 | Claude 3.5 Sonnet 0.51; GPT-5 0.42 (rebuttal) | not stated |
| SpecVQA [S16] | 2026 | 620 spectra figures | curve task scored as 1 − distance/point-cloud diameter (Chamfer etc.) | Gemini 2.5 Flash 0.8953 Chamfer score [Table 3] | — |
| DigitizerBench [S49] | 2026-09 | 2,980 synthetic images, orthogonal factor design | FPC-NRMSE (÷ range, Hungarian, unit loss) | automatic 0.672; human-guided 0.081 | **Linear only** |
| ParseBench charts [S44] | 2026 | enterprise pages | up to 10 spot points per chart; 1 % tolerance for axis-read values "since pixel-perfect reading is unrealistic" | "Only four providers exceed 50 %" (vendor) | — |
| RF-CDA benchmark [S35] | 2026-07 | 5,856 RF curve images, human references | fraction within ε = 1/3/5 % of max(W,H) | RF-CDA 0.9419 strict; Gemini-3 0.8800 (self-run) | Mentions log scales as a difficulty; no separate log result found |

### 5.3 What precision do the best systems reach on dense line charts without data labels?

- **Frontier VLMs, independent benchmarks:** about 4–6 % of Vmax on line charts [S12 Table 4], and 88 % RMSF1 at 10 % tolerance [S11 Table 3]. ExChart's pilot shows the data-label effect directly: Gemini 2.5 Flash MAPE <2 % with labels versus >7 % without [S12 §3, Table 2].
- **The one sub-percent claim is self-run:** Gemini-3 put 88 % of points within 1 % of chart size on RF curves [S35 Table 2]. Its references are human-annotated, whose own error is likely 0.5 % or more by [S2]'s WPD figure (inference). So "within 1 %" cannot resolve dsdig's 0.5 % target.
- **Log axes:** I found no benchmark that evaluates VLM digitisation on log axes (grep of [S11], [S12], [S16], [S49] text layers; [S30] explicitly excludes them).
- **Refutation search run:** "vision language model chart digitization sub-percent accuracy line chart points within 1%". It surfaced no benchmark with a sub-percent result other than [S35].

## 6. Domain-specific digitisation

| Work | Domain | Method | Accuracy (what, where) | Access | Relevance |
|---|---|---|---|---|---|
| **PowerBrain** (Tian et al., KU Leuven) [S26, S27] | MOSFET datasheets | CenterNet page and element detection; Tesseract; **tick numbers matched to detected grid lines**; morphological closing removes thin grid; skeletonise; colour clustering; point linking with predicted position | 170 datasheet figures, ground truth from **decoded PDF vector data**. Mean error 1.61 % of GT y-range; 149 of 170 below 1 %, 157 of 170 below 2 %. LineEX: 5.57 % [Table III]. Failure mode: OCR "20"→"2" (3 of 170) | Author copy on Lirias; tool at powerbrain.ai | **Closest prior art.** Log-axis handling not mentioned (grep) |
| SPECTRUM [S28] | IGBT datasheets | detection, instance segmentation, transformer | Abstract claims robustness to "logarithmic scales, overlapping elements"; no numbers in the abstract | IEEE, not entitled | Unknown; claims log handling |
| EDocCurve (HEMT) [S25] | GaN HEMT datasheets | YOLO tick labels plus OCR; Hough axes (intersection = origin); segmentation for curves; label→nearest-curve binding; linear/log affine | **No quantitative digitisation accuracy reported** (qualitative Fig. 9) | arXiv | Low |
| SiC MOSFET digitalisation [S32] | SiC datasheets | Python reader plus plot extraction | Abstract: waveforms "identical … to manual methods"; no numbers | Springer, not entitled | Unknown |
| Datasheet-driven device modelling [S29] | QSPICE model generator | digitisation → parameter mapping | "qualitative validation … using overlays" (abstract) | IEEE, not entitled | Low |
| RF-CDA [S35] | RF characteristic curves | see §2 | 0.9419 within 1 % (self-run) | SSRN | High analogue |
| LineFormer battery fine-tune [S43] | battery charge/discharge | LineFormer fine-tuned on 62 train and 19 validation images | 6b 0.6835 → 0.7836; over-detection +62.3 % → +9.6 % (**self-reported**; card says Mask R-CNN/ResNet-50, which differs from the paper's Swin-T) | HF, Apache-2.0 | Shows the pre-trained model's over-detection on real science figures |
| Cu-Cr-X (Auto-FDE) [S51] | materials, alloys | HSV whitening, axis segmentation, key-point strategy | Record-level P/R/F1 vs a double-WPD reference; e.g. Tuple_all F1 74.0 % (first column set) | Sci Data, OA | Low; numeric tolerance for "correct" not located |
| Starrydata2 [S36] | thermoelectrics | human-in-the-loop by design, with WPD and StarryDigitizer | — | OA | Confirms the field keeps humans in the loop |
| Plot2Spectra [S37] | XANES and Raman spectra | see §4 | RMSE 0.018 (normalised) | OA | Low |
| Kaplan–Meier (SurvdigitizeR, KM-GPT, VEC-KM) [S38, S39, S40] | clinical survival curves | colour/threshold; GPT-5 + k-NN; vector | RMSE 0.012 auto vs 0.014 manual [S38]; median AE 0.005 [S39]; RMSE 0 (vector) [S40] | OA | Best validation practice (section 7) |

## 7. Validation methodology

| Practice | Where | What it catches | What it misses |
|---|---|---|---|
| Per-point error relative to \|y\|, capped, interval-weighted, bipartite matched (CHART-Info 6a/6b) | Official definition [S68 §1.1 Eq. 1–5, with ε = GT range/100]; implementations [S4], [S30 Eq. 11–13]; series matched by Hungarian assignment on name plus data distance [S55 §3.6] | Missing and extra series (6b penalises false positives) | Scale-dependent; meaningless near y = 0; not in display space |
| Range-normalised error with Hungarian matching and unit loss on failure (FPC-NRMSE) | [S49 Eq. 1] | Failures stay in the average; coverage ≥ 0.8 is required for "trusted usability" | Linear axes only; data-unit range |
| Error divided by GT y-range, with vector-decoded ground truth | [S26 Eq. 8] | Uses the PDF's own vector data as truth, which dsdig can do for free | Data-unit range on log axes |
| Fraction of points within ε of chart size | [S35 §4.1] | Tolerance ladder 1/3/5 % | References are human-clicked |
| Mean relative error against generator data, expert vs tool | [S2 §7] | Separates vector from raster ceilings | n = 42 |
| RMSE on survival probability plus Bland–Altman vs manual | [S38] | Agreement with a human baseline | Linear 0–1 axis only |
| **Re-render certificate** | [S1 §5–6], [S39], [S35] | Wrong series, missing segments, "topological inconsistencies"; figverify gives a bit-level proof for matplotlib | Certificate is renderer-specific; a calibration error re-renders consistently (inference) |
| Tick-position scoring (CHART-Info Task 4) | [S17 §4.4] | Full credit ≤ 1 % of image diagonal, zero ≥ 2 % | About 16 px of full-credit slack on a 1280×960 image: at least ten times coarser than dsdig's 1 px target; ignores secondary axes |

**Does anyone check calibration against gridlines, or handle misprinted axes?**
- Gridlines are *used* for calibration by [S2 §5.5] and [S26 Alg. 2].
- No paper I read *reports* a gridline-residual metric.
- figverify reports its fit residual as a gate [S1 App. C].
- LinePilot-OCR rejects inconsistent tick readings [S49 §3.1].
- No work detects a misprinted but self-consistent label. The search record is in §10.

## 8. Recommendations for dsdig (adopt / evaluate / ignore)

**Adopt:**
1. **Stroke-seated calibration with a residual gate.**
   - Seat every tick value on the nearest gridline or tick-mark path of the matching orientation. Fall back to the label centre only when no stroke exists, and record that fallback [S2, S26, S49].
   - Fit linear and log models and keep the lower residual [S1 App. C].
   - Use leave-one-out residuals to flag a single misprinted label, extending [S49 §3.1] rejection (inference: this goes beyond anything published).
   - Recombine superscripts only when the exponent glyph is smaller and raised [S1 App. C].
   - Report the residual in px as a fail-closed field.
2. **Re-render certificate.**
   - For vector panels: map extracted points back through the calibration and compare them with the source path vertices. Any systematic offset is a calibration error; any vertex on a different path object is an identity error (inference, adapting [S1]).
   - For raster panels: overlay-diff, as in [S35] and [S39].
3. **A comparable metric.** Add FPC-NRMSE [S49] computed in display space (decades on log axes) to the regression harness. Use PDF-vector ground truth where available [S26], so dsdig's accuracy can be stated against the literature.

**Evaluate:**
4. **figverify** generic backend on about 10 dsdig vector panels, including Ciss/Coss crossings and a log–log panel, as an independent cross-check. MIT-licensed [S1, S57].
5. **LineFormer**, already running, restricted to raster/scanned panels and to crossing disambiguation. Expect false positives on B/W gridded charts [S30 §4.5]. Its lack of a licence is a redistribution issue [S57].
6. **Path identity at crossings.** Where the PDF draws each curve as its own path, carry path-object identity through the crossing instead of re-tracing geometrically; VEC-KM's "top-K longest connected paths" is the simplest form [S40]. Whether ST and Infineon datasheets draw one path per curve is **not established** here; it needs a local check on the corpus (inference).

**Ignore:** chart-to-table VLMs as extractors (§5), WPD v5 AI Assist, Engauge, g3data, DataThief (§4).

## 9. Claims I could not verify, with the highest access rung reached

| Item | What depends on it | Highest rung reached and result |
|---|---|---|
| ICPR 2020 CHART-Info competition paper, doi 10.1007/978-3-030-68793-9_27 | Competition results in §5.2 | **Resolved: obtained.** par.nsf.gov times out from this network (curl, headless browser, and Fab's Chrome all failed). Springer through Fab's Chrome: "preview of subscription content", and the PDF endpoint returned 204. Unpaywall, OpenAlex and Semantic Scholar list it as closed; Scholar "all 3 versions" lists only the nsf.gov PDF. **The Wayback capture of the NSF PAR deposit (2024-04-12) returned the full PDF [S55].** The official metric document was found on the competition site [S68]. |
| ICDAR 2019 CHART-Info competition paper, doi 10.1109/ICDAR.2019.00203 | §5.2 | **Resolved: obtained** from the Wayback capture of the NSF PAR deposit (2025-12-06) [S56]. IEEE Xplore through Fab's Chrome showed "Purchase Details". Scholar "all 5 versions" lists only nsf.gov. |
| Curve Separation for Line Graphs (JCDL 2016), doi 10.1145/2910896.2925469 | §3 | **Resolved: obtained.** Full PDF from the ACM DL through Fab's Chrome [S34]. Unpaywall and Semantic Scholar list it as closed; the CiteSeerX search page now redirects to a Wayback 404. |
| SPECTRUM (IGBT), Jinyu Wang, Haoning Jiang, Jiachen Wang, Rui Chen, Chijie Zhuang, Jian Song; EExPolytech 2025; doi 10.1109/EExPolytech66949.2025.11252000 | Whether an IGBT-datasheet system handles log axes, and how accurately | **Truly paywalled; no open copy found.** Tried: IEEE through Fab's Chrome (not entitled); Unpaywall, OpenAlex and Semantic Scholar (closed); Scholar through Fab's Chrome (no PDF version); author-name web searches (no self-hosted copy); ResearchGate 398056955 through Fab's Chrome (blank 1.7 kB shell, consistent with ResearchGate's network restriction in kb `access/researchgate-restriction…`). **Fab would need to:** buy or borrow access through IEEE, open ResearchGate from another network, or ask the authors (Tsinghua EE, Zhuang group). |
| AI-ChartParser, Wenjin Yang, Jie He, Xiaotong Zhang, Haiyan Gong; CGF 44(6) e70146 (2025); doi 10.1111/cgf.70146 | Its own accuracy numbers (an independent RF-CDA number is used instead) | **Truly paywalled; no open copy.** Tried: Wiley through Fab's Chrome (abstract only; the `pdfdirect` URL returned 403). The free correction notice cgf.70306 changes the acknowledgements only. Unpaywall, OpenAlex and Semantic Scholar list it as closed; the Scholar query found no PDF version; the GitHub repo ywking/ChartParser has code and weights but no paper; ResearchGate 393054222 returned a blank 1.7 kB shell. **Fab would need to:** get Wiley access, open ResearchGate from another network, or email the USTB authors. |
| Datasheet Digitalisation Automation for SiC MOSFETs, Shengping Yu … Bing Ji; LNEE 1408 (2025); doi 10.1007/978-981-96-4710-1_25 | Datasheet prior art | **Truly paywalled; no open copy.** Tried: Springer through Fab's Chrome (preview; the PDF endpoint returned 204); Unpaywall and OpenAlex (closed; not in Semantic Scholar); Scholar "all 2 versions" (no PDF); the University of Leicester Figshare repository (not found); ResearchGate 391207955 (blank shell). **Fab would need to:** buy the chapter, get Springer access, or ask Bing Ji (University of Leicester). |
| Datasheet-Driven Automated Device Modeling, Siddharth Mohan, Mike Engelhardt, Tim McCune, Jeff Strang (Qorvo); SMACD 2026; doi 10.1109/SMACD70206.2026.11647759 | Datasheet prior art (the abstract already says the validation is qualitative only) | **Truly paywalled; no open copy.** Tried: IEEE through Fab's Chrome (not entitled; the iel8 PDF URL returned an HTML page); Unpaywall, OpenAlex and Semantic Scholar (closed); Scholar (no result with a PDF); the Sedemos blog summary has the abstract and author list only. **Fab would need to:** get IEEE access or ask Qorvo's QSPICE team. |
| ChartDETR line score of 0.975 | §2 table | Read only partially (the text-layer line was garbled across columns); treat as unverified |
| Cu-Cr-X "correct record" tolerance | §6 | Full text read by grep; the tolerance definition was not located |
| Whether ChartDetective's code is public | §3 | Not stated in the text I read; the GitHub search was not specific enough |
| nutrientdocs chart-parsing leaderboard | Fab's own measurement | Not re-read (out of scope by instruction) |
| SynthIPD (arXiv 2509.16466), Futrelle "Graphics recognition in PDF" | Vector prior art | Not fetched: found in search, named only as leads |

## 10. Search record and provenance

- **Seed set (§2.3).** The dsdig workspace (README, AGENTS.md, `.claude/memory`) cites no external literature apart from LineFormer: a local clone at `/Users/fab/dev/pv/LineFormer`, commit 209883b. `~/dev/kb/INDEX.md` has no chart-digitisation note.
- **Search surfaces.** Serper (Google web and Scholar); GitHub search and API; arXiv API; OpenAlex API; HAL API.
- **Queries.**
  - The query families in §2–6, including those listed in §3.2.
  - "ICDAR 2025 competition chart data extraction"; "ICPR 2022 CHART-Infographics competition results"; "LineFormer fine-tuned battery …"; "SurvdigitizeR …"; "KM-GPT …"; "ExChart-Bench …"; "Chart2CSV openreview …"; "SpecVQA …"; "granite-vision chart2csv"; "PP-Chart2Table".
  - "semiconductor datasheet curve extraction deep learning MOSFET chart digitization"; "IGBT datasheet curve digitization automatic".
- **Challenge searches, one per conclusion cluster:**
  - (a) "Vision-language models do not reach 1 % on dense line charts." Query: "vision language model chart digitization sub-percent accuracy line chart points within 1%". Result: the only counter-evidence is RF-CDA's self-run Gemini-3 figure [S35], reported above.
  - (b) "The vector literature is thin." Queries: the refutation-phrased set in §3.2. It surfaced [S34], [S40] and SynthIPD in addition to [S1] and [S2]. The conclusion was softened to "thin but not empty".
  - (c) "LineFormer is weak on B/W gridded charts." Query: "LineFormer fails grayscale grid lines datasheet charts". No counter-evidence; the only source is [S30] (same-field competitor, so possibly biased).
  - (d) "No benchmark evaluates log axes." Query: "chart data extraction log scale axis evaluation benchmark". No benchmark found. [S30] excludes log axes explicitly. [S35] names log scales as a difficulty but reports no separate result.
- **Absence claims:**
  - §3.2 (vector, medium confidence).
  - "No work detects and corrects a misprinted tick label": searched within all read sources plus the Scholar queries above; low-to-medium confidence, because I ran no query dedicated to misprinted labels.
  - "No log-axis evaluation in benchmarks": a grep of the text layers of [S11], [S12], [S16], [S49] and [S3]. Medium confidence; figures and tables were not read as images.
- **Access notes.**
  - The real-profile route (Fab's Chrome via the Playwright extension; gate `AUTHORIZED` at 2026-09-29T09:12:34Z) was used for: OpenReview, IEEE, Springer, Wiley, ScienceDirect, ACM, SSRN, T&F, RSC and NSF.
  - The token was passed only as a process environment variable. A grep of the working directory and the MCP output directory found no copy of it.
  - OpenReview served a challenge page once (on the second visit). A later same-profile visit with a 45 s dwell loaded normally and the PDF downloaded.
  - Retrieval help was requested (in the final message) rather than waited for. Drafting proceeded with the gaps in §9 open.
  - Second pass (2026-09-29, on the coordinator's instruction): open-copy checks for every unresolved item (Unpaywall, OpenAlex, Semantic Scholar, Google Scholar "all versions" through Fab's Chrome, author and institutional repositories, the competition site, and the Wayback Machine for the unreachable NSF host). This resolved S34, S55 and S56 and added S68–S69. Four papers remain paywalled with no open copy (§9).
- **Archive.** Downloaded PDFs are in `/Users/fab/dev/pv/ee/datasheet-chart-digitizer/out/chart-digitisation-refs/` (gitignored via `out/`), with `SHA256SUMS`. Filenames: arXiv IDs, or short names such as `jestpe2024.pdf`, `icpr2020.pdf`, `icdar2019.pdf`, `chartinfo_metric.pdf`, `jcdl2016_curve_separation.pdf`, `rfcda_ssrn_7084283.pdf`, `chart2csv_openreview_b0B6JQF8Xj.pdf`. Licensed copies (ACM, SSRN "no reuse") are for local reading only and must not be committed or redistributed. Sources read as HTML (ScienceDirect, T&F, RSC, model cards) have no PDF copy there.

## 11. Source access log

Columns: ID · identifier (URL/DOI, version) · evidence state · attempt history · route · validation · identity · load-bearing.

| ID | Identifier | State | Attempts | Route | Valid. | Identity | LB |
|---|---|---|---|---|---|---|---|
| S1 | arXiv:2606.31345v1 (Sun & Xiao, figverify), https://arxiv.org/abs/2606.31345 | inspected (§1–8, App. C) | curl PDF 200, %PDF, first page matches | raw text | validated (title/author on p.1) | anonymous | yes |
| S2 | ChartDetective, CHI 2023, doi 10.1145/3544548.3581113; hal-04017638v1 | inspected (§3, §5.5, §7, §8) | curl HAL → Anubis page; headless browser, in-page fetch → PDF 3.17 MB | raw text | validated | anonymous | yes |
| S3 | arXiv:2305.01837v1 (LineFormer) | inspected | curl PDF | raw text | validated | anonymous | yes |
| S4 | LineFormer repo `metric6a.py`, commit 209883b (local clone /Users/fab/dev/pv/LineFormer) | inspected (lines 118–167) | local read | raw text | validated | local | yes |
| S5 | https://automeris.io/posts/version_5/ (2024-05-14) | inspected | headless 200 | raw text (DOM) | validated | anonymous | yes |
| S6 | https://automeris.io/docs/digitize/ | inspected | headless 200 | DOM | validated | anonymous | no |
| S7 | github automeris-io/WebPlotDigitizer `javascript/core/curve_detection/averagingWindowCore.js` (master, 2026-09-29) | inspected | gh api | raw text | validated | anonymous | no |
| S8 | https://akhuettel.github.io/engauge-digitizer/ | inspected | headless 200 | DOM | validated | anonymous | no |
| S9 | https://github.com/dilawar/PlotDigitizer | partial (README) | headless 200 | DOM | validated | anonymous | no |
| S10 | https://github.com/echemdb/svgdigitizer | partial (README) | headless 200 | DOM | validated | anonymous | no |
| S11 | arXiv:2605.27298v1 (Berkane et al.) | inspected (§1–4) | curl PDF | raw text | validated | anonymous | yes |
| S12 | arXiv:2606.29808v1 = CHI'26 doi 10.1145/3772318.3790721 (ExChart) | inspected (§3–7, Tables 2, 4) | curl PDF | raw text | validated | anonymous | yes |
| S13 | arXiv:2212.10505 (DePlot) | partial (§3.1, Table 1) | curl PDF | raw text | validated | anonymous | yes |
| S14 | arXiv:2212.09662 (MatCha) | partial (metrics para) | curl PDF | raw text | validated | anonymous | no |
| S15 | arXiv:2404.09987 (OneChart) | partial (Eq. 6, Table 2) | curl PDF | raw text | validated (column mapping by header order; table extraction interleaved) | anonymous | no |
| S16 | arXiv:2604.28039v1 (SpecVQA) | partial (§5, Tables 2–3) | curl PDF | raw text | validated | anonymous | no |
| S17 | CHART-Info 2024, Davila et al., doi 10.1007/978-3-031-78495-8_19; author copy https://cdn.iiit.ac.in/cdn/cvit.iiit.ac.in/images/ConferencePapers/2024/chart_info.pdf | inspected (§4.4, 4.6, 4.7) | curl 200 %PDF | raw text | validated | anonymous | yes |
| S18 | https://chartinfo.github.io/ (ICDAR 2023) | inspected (news) | headless 200 | DOM | validated | anonymous | no |
| S19 | https://www.icdar2025.com/program/competitions | inspected (full list) | headless 200 | DOM | validated | anonymous | no |
| S20 | arXiv:2307.02065 (LG dataset) | metadata+abstract | curl PDF | raw text | validated | anonymous | no |
| S21 | arXiv:2308.07743 (ChartDETR) | partial | curl PDF | raw text | unvalidated extraction for line-score row (columns garbled) — not load-bearing | anonymous | no |
| S22 | LineEX, WACV 2023, openaccess.thecvf.com PDF | partial | curl 200 | raw text | validated | anonymous | no |
| S23 | ChartOCR, WACV 2021, openaccess.thecvf.com PDF | metadata | curl 200 | raw text | validated | anonymous | no |
| S24 | ChartRecover, Commun. Eng. 5:147 (2026), doi 10.1038/s44172-026-00691-8 | partial (abstract, intro) | curl 200 | raw text | validated | anonymous | no |
| S25 | arXiv:2507.21430 (HEMT EDocCurve) | partial (§III.A, §IV.B) | curl PDF | raw text | validated | anonymous | no |
| S26 | PowerBrain, IEEE JESTPE 12(6):5648–5660 (2024), doi 10.1109/JESTPE.2024.3456592; author copy https://lirias.kuleuven.be/retrieve/754123ad-5aa9-40c2-92e2-d11ce2b7134c | inspected (§III, §IV.B, Table III) | real-profile IEEE page: abstract (not entitled); OpenAlex OA → Lirias curl 200 %PDF | raw text | validated | real profile (IEEE); anonymous (Lirias) | yes |
| S27 | Tian et al., IPEC-Himeji 2022, doi 10.23919/IPEC-Himeji2022-ECCE53331.2022.9806859; Lirias copy | metadata+abstract | real-profile IEEE; Lirias curl 200 | raw text | validated | real profile / anonymous | no |
| S28 | SPECTRUM, doi 10.1109/EExPolytech66949.2025.11252000 | metadata only (abstract) | real-profile IEEE (not entitled); OpenAlex not OA | DOM | validated | real profile — gate AUTHORIZED 2026-09-29T09:12:34Z | no (no claim rests on it beyond abstract wording) |
| S29 | SMACD 2026, doi 10.1109/SMACD70206.2026.11647759 | metadata only | real-profile IEEE | DOM | validated | real profile | no |
| S30 | Yang, He, Zhang, Graphical Models (2025), doi 10.1016/j.gmod.2025.101259 | inspected (§1, 3, 4.4–4.7) | headless anon: ScienceDirect error page (CPE00001) and 403; real-profile ScienceDirect: full text (OA, CC) | DOM | validated | real profile | yes |
| S31 | AI-ChartParser, CGF 44(6) e70146, doi 10.1111/cgf.70146 | metadata only | real-profile Wiley: homepage showed Cloudflare "Performing security verification" (non-interactive; nothing to solve), and the next navigation in the same session loaded the article page; full text not entitled | DOM | validated | real profile | no |
| S32 | Yu et al., LNEE 1408:215–221, doi 10.1007/978-981-96-4710-1_25 | metadata only | real-profile Springer: preview | DOM | validated | real profile | no |
| S33 | Zhou & Lu, ATDE 20:781–786, doi 10.3233/ATDE220079 | partial | curl PDF URL → HTML viewer; headless click "Download PDF" → %PDF | raw text | validated | anonymous | no |
| S34 | Ray Choudhury, Wang, Giles, JCDL 2016 pp. 277–278, doi 10.1145/2910896.2925469; file `jcdl2016_curve_separation.pdf` | inspected (full, 2 pp) | real-profile ACM landing page (1st pass); Unpaywall/S2 closed; CiteSeerX search → Wayback 404; real-profile in-page fetch of dl.acm.org/doi/pdf → %PDF 857 kB | raw text | validated (title/authors p.1) | real profile | no |
| S35 | RF-CDA, SSRN 7084283 (posted 2026-07-09, preprint), doi 10.2139/ssrn.7084283 | inspected (§1, §4.1–4.2, Table 2) | real-profile SSRN abstract page; download link read from DOM; in-page fetch → %PDF 28 MB | raw text | validated (title page) | real profile | yes |
| S36 | Starrydata2, STAM Methods 5(1) 2025, doi 10.1080/27660400.2025.2506976 | partial | real-profile T&F full text | DOM | validated | real profile | no |
| S37 | Plot2Spectra, Digital Discovery 2022, d1dd00036e | partial | real-profile RSC (Cloudflare on homepage, article HTML loaded) | DOM | validated | real profile | no |
| S38 | SurvdigitizeR, BMC Med Res Methodol 24:147 (2024), doi 10.1186/s12874-024-02273-8 | partial (abstract, methods, results) | curl 200 %PDF | raw text | validated | anonymous | no |
| S39 | arXiv:2509.18141v1 (KM-GPT) | partial (methods, results) | curl PDF | raw text | validated | anonymous | no |
| S40 | arXiv:2511.01785v2 (RESOLVE-IPD) | partial (VEC-KM, results) | curl PDF | raw text | validated | anonymous | yes |
| S41 | https://huggingface.co/ibm-granite/granite-vision-3.3-2b-chart2csv-preview | inspected (self-reported card) | headless 200 | DOM | validated | anonymous | no |
| S42 | https://huggingface.co/PaddlePaddle/PP-Chart2Table ; PaddleOCR docs chart_parsing.html | inspected (self-reported) | headless 200 | DOM | validated | anonymous | no |
| S43 | https://huggingface.co/t29mato/lineformer-battery-finetuned | inspected (self-reported) | headless 200 | DOM | validated | anonymous | no |
| S44 | https://www.llamaindex.ai/blog/parsebench | partial (charts section) | headless 200 | DOM | validated | anonymous | no |
| S45 | https://github.com/jsvine/pdfplumber/discussions/1079 | inspected | headless 200 | DOM | validated | anonymous | no |
| S46 | https://www.datathief.org/ | inspected | headless 200 | DOM | validated | anonymous | no |
| S47 | https://github.com/pn2200/g3data | partial | headless 200 + gh api | DOM | validated | anonymous | no |
| S48 | https://plotdigitizer.com/ | partial (vendor) | headless 200 | DOM | validated | anonymous | no |
| S49 | arXiv:2609.19377v1 (LinePilot, 2026-09-16) | inspected (§1–6) | curl PDF | raw text | validated | anonymous | yes |
| S50 | arXiv:2609.22210 (SALSA) | partial (§2.3.3) | curl PDF | raw text | validated | anonymous | no |
| S51 | Sci Data 12:2023 (2025), doi 10.1038/s41597-025-06295-9 | partial | curl 200 | raw text | validated | anonymous | no |
| S52 | Chart2CSV, OpenReview b0B6JQF8Xj (ICLR 2026 submission 14065, withdrawn) | inspected (forum, abstract, reviews; PDF §4 metrics) | headless: 403; headed scratch Chrome: 403; OpenReview API: ChallengeRequiredError; real-profile forum: loaded; 2nd visit: challenge redirect; 3rd real-profile visit with 45 s dwell loaded; in-page PDF fetch 200 %PDF | DOM + raw text | validated | real profile | yes |
| S53 | arXiv:2305.04151v2 (ChartDete/CACHED) | metadata+abstract | curl PDF | raw text | validated | anonymous | no |
| S54 | https://research.adobe.com/publication/icpr-2020-competition-on-harvesting-raw-tables-from-infographics/ | metadata only | real profile 200 | DOM | validated | real profile | no |
| S55 | ICPR 2020 CHART-Info paper, Davila et al., doi 10.1007/978-3-030-68793-9_27; file `icpr2020.pdf` | inspected (§2–6, Tables 9–10) | curl par.nsf.gov ×2 TCP timeout; headless navigation timeout; real-profile par.nsf.gov "can't be reached"; real-profile Springer: preview, PDF endpoint 204; Unpaywall/OpenAlex/S2 closed; Scholar all-versions: nsf.gov only; archive.org availability API 429 (honoured, not retried); **Wayback `web/20240412163038id_/https://par.nsf.gov/servlets/purl/10292332` → %PDF 1.77 MB, title page matches** | raw text | validated (title/author p.1) | anonymous (Wayback) | yes |
| S56 | ICDAR 2019 CHART-Info paper, doi 10.1109/ICDAR.2019.00203; file `icdar2019.pdf` | partial (§III.D–F) | curl par.nsf.gov TCP timeout; real-profile IEEE: Purchase Details; Scholar all 5 versions: nsf.gov only; Wayback 2024 capture = 404; **Wayback CDX → `web/20251206122922id_/…/10188725` → %PDF 1.04 MB, title matches** | raw text | validated | anonymous (Wayback) | no |
| S57 | GitHub REST API repo metadata (licence, pushed_at), queried 2026-09-29 | inspected | gh api | raw text | validated | anonymous | no |
| S58–S64 | arXiv 2305.14761 (UniChart), 2407.04172 (ChartGemma), 2404.16635 (TinyChart), 2409.03277 (ChartMoE), 2304.02173 (ChartReader), 2501.06598 (ChartCoder), 2211.14362 (Chart-RCNN) | metadata+abstract | curl PDF, identity checked | raw text | validated | anonymous | no |
| S68 | CHART-Info official metric document, https://chartinfo.github.io/metrics/metric.pdf (linked from index_2020.html); file `chartinfo_metric.pdf` | inspected (§1.1) | link found in rendered DOM of index_2020; curl 200 %PDF | raw text | validated | anonymous | yes |
| S69 | https://chartinfo.github.io/leaderboards_2020.html and leaderboards_2022.html | inspected (per-task text) | headless 200; hidden tabs read via textContent | DOM | validated | anonymous | no |
| S70 | Open-access lookups: Unpaywall v2, OpenAlex, Semantic Scholar graph API, Google Scholar (real profile) for S28, S29, S31, S32, S34, S55, S56 | inspected | API JSON; Scholar through Fab's Chrome (no CAPTCHA) | raw text / DOM | validated | anonymous / real profile | no |
| S71 | ResearchGate 393054222, 391207955, 398056955 | not obtained (blank 1.7 kB shell) | real-profile, once each; no retries per kb note | route: none | n/a | real profile | no |
| S72 | Wiley correction doi 10.1111/cgf.70306 (free) | inspected | real profile | DOM | validated | real profile | no |
| S65 | arXiv 2512.01017 (ChartAnchor), 2602.10880 (Chart Specification), 2504.09764 (Socratic Chart) | metadata only (downloaded, identity checked, not read) | curl PDF | raw text | validated | anonymous | no |
| S66 | arXiv 2606.00065 (ComProScanner figures) | partial; judged out of scope (composition extraction, not curve digitising) | curl PDF | raw text | validated | anonymous | no |
| S67 | Ahbane BSc thesis, BME 2025, https://malradhi.github.io/pdf/students/abdelhamid/Abdelhamid_BSc_thesis_is2025.pdf | partial; out of scope (it generates datasheet diagrams, it does not digitise them) | curl 200 | raw text | validated | anonymous | no |

Browser lifecycle:
- Every standalone Playwright browser was launched in launch mode (it owns its browser) with `headless-shell-1208`. The headed-Chrome attempt used `channel=chrome`. Each was closed in a `finally` block. No persistent profiles were created.
- The real-profile route ran 13 MCP sessions. Each ended with tabs listed 1, closed 1, held 0, failed 0, and a server exit of 0. No tab and no handoff are left open.

## 12. References (retrieved 2026-09-29)

- [S1] B. Sun, C. Xiao, "Automated High-Precision Extraction and Forensic Verification of Data-Bearing Vector Figures", arXiv:2606.31345v1 (2026). https://arxiv.org/abs/2606.31345 ; code https://github.com/Bowen-Sun-0728/figverify
- [S2] D. Masson, S. Malacria, D. Vogel, E. Lank, G. Casiez, "ChartDetective: Easy and Accurate Interactive Data Extraction from Complex Vector Charts", CHI 2023, doi:10.1145/3544548.3581113. https://hal.science/hal-04017638v1
- [S3] J. Lal, A. Mitkari, M. Bhosale, D. Doermann, "LineFormer: Rethinking Line Chart Data Extraction as Instance Segmentation", ICDAR 2023; arXiv:2305.01837. https://github.com/TheJaeLal/LineFormer
- [S4] LineFormer `metric6a.py`, commit 209883b583c2184475ea0b2624d5a75dd48a9b1e.
- [S5] A. Rohatgi, "WebPlotDigitizer Version 5" (2024-05-14). https://automeris.io/posts/version_5/
- [S6] WebPlotDigitizer docs, "Digitize Charts". https://automeris.io/docs/digitize/
- [S7] https://github.com/automeris-io/WebPlotDigitizer (AGPL-3.0)
- [S8] Engauge Digitizer (fork site). https://akhuettel.github.io/engauge-digitizer/
- [S9] https://github.com/dilawar/PlotDigitizer
- [S10] https://github.com/echemdb/svgdigitizer
- [S11] T. Berkane, Q. Wang, M. S. Majumder, "Self-Ensembling Vision-Language Models for Chart Data Extraction", arXiv:2605.27298v1 (2026). https://arxiv.org/abs/2605.27298
- [S12] Y. He et al., "Making Multimodal LLMs Reliable Chart Data Extractors: A Benchmark and Training Framework", CHI '26, doi:10.1145/3772318.3790721; arXiv:2606.29808. https://exchart.github.io/
- [S13] F. Liu et al., "DePlot", arXiv:2212.10505. [S14] F. Liu et al., "MatCha", arXiv:2212.09662.
- [S15] J. Chen et al., "OneChart", ACM MM 2024, arXiv:2404.09987.
- [S16] J. Shen et al., "SpecVQA", arXiv:2604.28039v1.
- [S17] K. Davila et al., "CHART-Info 2024: A Dataset for Chart Analysis and Recognition", ICPR 2024, doi:10.1007/978-3-031-78495-8_19. https://cdn.iiit.ac.in/cdn/cvit.iiit.ac.in/images/ConferencePapers/2024/chart_info.pdf
- [S18] https://chartinfo.github.io/ ; [S19] https://www.icdar2025.com/program/competitions
- [S20] O. Moured et al., arXiv:2307.02065. [S21] W. Xue et al., "ChartDETR", arXiv:2308.07743.
- [S22] Shivasankaran V P, M. Y. Hassan, M. Singh, "LineEX", WACV 2023. [S23] J. Luo et al., "ChartOCR", WACV 2021.
- [S24] Y. Yuan et al., "A deep learning framework for scientific chart data extraction and reconstruction", Commun. Eng. 5:147 (2026), doi:10.1038/s44172-026-00691-8.
- [S25] Y. Peng, J. Zhong, Y. Zhang, H. C. Chen, arXiv:2507.21430.
- [S26] F. Tian, Q. Sui et al., "Automated Extraction of Data From MOSFET Datasheets for Power Converter Design Automation", IEEE JESTPE 12(6):5648–5660 (2024), doi:10.1109/JESTPE.2024.3456592.
- [S27] F. Tian, D. Bernal Cobaleda, W. Martinez, IPEC-Himeji 2022, doi:10.23919/IPEC-Himeji2022-ECCE53331.2022.9806859.
- [S28] doi:10.1109/EExPolytech66949.2025.11252000. [S29] doi:10.1109/SMACD70206.2026.11647759.
- [S30] W. Yang, J. He, X. Zhang, "Efficient extraction of experimental data from line charts using advanced machine learning techniques", Graphical Models (2025), doi:10.1016/j.gmod.2025.101259.
- [S31] W. Yang, J. He, X. Zhang, H. Gong, "AI-ChartParser", CGF 44(6) e70146 (2025), doi:10.1111/cgf.70146.
- [S32] S. Yu et al., doi:10.1007/978-981-96-4710-1_25. [S33] S. Zhou, J. Lu, doi:10.3233/ATDE220079.
- [S34] S. Ray Choudhury, S. Wang, C. L. Giles, JCDL 2016, doi:10.1145/2910896.2925469.
- [S35] D. Zheng et al., "RF-CDA", SSRN 7084283 (2026), doi:10.2139/ssrn.7084283.
- [S36] doi:10.1080/27660400.2025.2506976. [S37] W. Jiang et al., "Plot2Spectra", Digital Discovery 2022, https://pubs.rsc.org/en/content/articlehtml/2022/dd/d1dd00036e
- [S38] doi:10.1186/s12874-024-02273-8. [S39] arXiv:2509.18141. [S40] arXiv:2511.01785.
- [S41]–[S48] URLs as given in the access log.
- [S49] F. Ma et al., "LinePilot Digitizer", arXiv:2609.19377v1 (2026).
- [S50] arXiv:2609.22210. [S51] doi:10.1038/s41597-025-06295-9.
- [S52] C. Hu, L. Zhang, Y. Lim, A. Wadhwani, D. Kang, "Chart2CSV", OpenReview https://openreview.net/forum?id=b0B6JQF8Xj
- [S53] P. Yan, S. Ahmed, D. Doermann, arXiv:2305.04151v2.
- [S55] K. Davila, C. Tensmeyer, S. Shekhar, H. Singh, S. Setlur, V. Govindaraju, "ICPR 2020 – Competition on Harvesting Raw Tables from Infographics", ICPR 2020 Workshops, LNCS, doi:10.1007/978-3-030-68793-9_27; copy: https://web.archive.org/web/20240412163038/https://par.nsf.gov/servlets/purl/10292332
- [S56] K. Davila et al., "ICDAR 2019 Competition on Harvesting Raw Tables from Infographics (CHART-Infographics)", ICDAR 2019, doi:10.1109/ICDAR.2019.00203; copy: https://web.archive.org/web/20251206122922/https://par.nsf.gov/servlets/purl/10188725
- [S68] CHART-Info, "Metric for Evaluating Data Extraction from Charts". https://chartinfo.github.io/metrics/metric.pdf
- [S69] https://chartinfo.github.io/leaderboards_2020.html ; https://chartinfo.github.io/leaderboards_2022.html
