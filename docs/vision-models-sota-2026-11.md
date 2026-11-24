# Dedicated vision models for dsdig (issue #19): what to train, on what, under which licence

Research date: 2026-11-24. Scope: dedicated vision models (no LLMs or VLMs as extractors) that would only **propose** boxes, masks or tokens for dsdig's geometric checks. This note is the delta to `docs/chart-digitisation-landscape-2026-09.md` (the "2026-09 survey"). That survey judged models as end-to-end extractors. This one judges them as trainable proposal components, and adds what appeared after 2026-09-29.

Conventions:
- `[Sn]` is a source ID. IDs up to S72 are the 2026-09 survey's own (reused for the same works). New sources start at **S73**, because the 2026-09 survey already used S70–S72.
- Every number carries its source and a locator.
- "Self-reported" means the authors' or vendor's own benchmark.
- "Inference" marks my reasoning, not the source's words.
- Licence and activity facts come from the GitHub REST API and Hugging Face API on 2026-11-24 [S73, S83] unless another source is named.
- I did not digitise any figure.

## Executive summary

**Short answer.** Build items 2–4 on Apache-licensed detector and OCR families, and do not build item 3 on LineFormer or on any query-based instance segmenter as the primary path. The strongest published evidence for thin crossing curves now favours a dense per-pixel model trained only on synthetic charts, with crossing identity resolved afterwards by deterministic matching. Licences changed in ways that matter: DEIMv2 went non-commercial in August 2026, and several "obvious" choices (Ultralytics YOLO, DocLayout-YOLO, AlbumentationsX, ChartDete's training data, CHART-Synthetic, Surya weights) carry copyleft or non-commercial terms.

### Task 2: layout detector (panel, plot box, axis titles, tick labels, legend, in-plot curve labels)

| Rank | Candidate | Licence verdict | Expected training data | Main risk |
|---|---|---|---|---|
| 1 | **RF-DETR** Nano/Small/Medium/Large detection (DINOv2 backbone) | **Usable.** Code and these sizes are Apache-2.0. The Atto/Femto/Pico/XL/2XL sizes are PML 1.0 and must not be used [S75 lines 23, 67–77] | Renders of the vector corpus, labels derived from vector geometry. No source states a page count for this task (bounded absence, §10) | Accuracy numbers are the vendor's own; an independent forum benchmark found it wins on 2 and loses on 2 of 4 small datasets against YOLO, and a commenter reports it about 10× slower on CPU [S119]. Tick labels are small objects; on RF100-VL the DETR family's AP_small is 30–39 [S78 Table 4], which is not 1 px box accuracy. The box is only a search window, so this is acceptable if the geometric seat stays mandatory (inference) |
| 2 | **D-FINE** or **DEIM (v1)** | **Usable.** Apache-2.0 [S73; DEIM LICENSE text read] | Same | RF-DETR's authors report that D-FINE's COCO hyperparameters transfer worse to RF100-VL than RT-DETR's [S78 §5, p. 9]. That is a competitor's claim |
| 3 | **RT-DETR / RT-DETRv2** | **Usable.** Apache-2.0 [S73]; in Hugging Face `transformers` [S117] | Same | Older; PP-DocLayout uses RT-DETR-L for page layout [S86], which shows it works on documents but says nothing about tick labels |
| — | ChartDete / CACHED | **Usable with conditions** as a teacher or baseline only. Code and checkpoints MIT, but the refined PMC training set is **CC BY-NC-SA 4.0** [S84]. Needs `mmcv-full` 1.7.2, MMDetection 2.28 fork, PyTorch 1.13.1 [S84] | Ready-made 18 classes including tick labels, tick marks, plot area, legend parts [S84] | Legacy stack; scientific-figure domain; COCO box AP 0.729 [S84] is not a calibration accuracy |
| Blocked | Ultralytics YOLOv8/11/26, YOLOv10/12/13, DocLayout-YOLO code | **Blocked** unless dsdig ships under AGPL-3.0 or buys a licence [S73, S82] | — | — |
| Blocked | DEIMv2 | **Blocked** for commercial use since 2026-08-24 [S74] | — | — |
| Blocked | YOLOv9 (GPL-3.0), MMYOLO (GPL-3.0) | **Blocked** for a permissive dsdig [S73] | — | — |

No document-layout model I checked detects chart **sub-elements**. Docling's Heron model has "Picture" [S85]; PP-DocLayout-plus has "chart" [S86]; the datasheet-specific EDocNet has "characteristic curve diagram" [S108]. They can at most propose the **panel**.

### Task 3: curve segmentation (raster only)

| Rank | Candidate | Licence verdict | Expected training data | Main risk |
|---|---|---|---|---|
| 1 | **Own dense model**: U-Net with CoordConv, a semantic curve head plus a per-pixel embedding head, in the style of ChartZero [S94]. Add a sub-pixel centreline output (distance-to-centreline regression or ridge detection on the probability map). Resolve identity at crossings with deterministic junction matching (PLECTA-style [S95], or dsdig's own tracer) | **Usable** (own code; PLECTA is MIT [S95]). ChartZero released no code I could find (§10) | Synthetic only, per ChartZero: trained "exclusively on a purely synthetic dataset of simple mathematical functions" [S94 Abstract] | ChartZero's numbers are self-reported on its own benchmark, with no log–log or B/W datasheet charts identified. Its own failure modes are local swaps at acute intersections and "phantom" curves in dense clusters [S94 §4.8, Fig. 5], which are exactly dsdig's hard cases |
| 2 | **Mask2Former or EoMT** through Hugging Face `transformers` (LineFormer's formulation, rebuilt) | **Usable.** Mask2Former MIT (repo archived), EoMT MIT, both in `transformers` 5.24 [S73, S117] | Synthetic renders; LineFormer trained on AdobeSynth19, UB-PMC and LineEX data [S3] | Query-based instance models over-produce instances on charts: locally 8 of 19 human-verified transfer charts got extra instances (18 extra in total) [S120]; on scanned river charts LineFormer returned 1–12 polylines where one was expected [S98 §4.5.4] |
| 3 | **RF-DETR-Seg** (all sizes Apache [S75]) | Usable | Same | Instance-mask resolution at 312–768 px input [S75 lines 115–120] is probably too coarse for 0.3–0.8 pt strokes (inference, untested) |
| Do not use | **LineFormer** | **Blocked.** No licence file; issue #12 asking for one has been open and unanswered since 2026-06-18 [S115] | — | — |
| Do not use | **SAM 3** | Custom SAM License [S76]; 0.28 IoU on thin crossing chart curves in ChartZero's test [S94 Table 3] | — | — |

### Task 4: tick-label OCR

| Rank | Candidate | Licence verdict | Expected training data | Main risk |
|---|---|---|---|---|
| 1 | **PP-OCRv6 small or tiny recogniser** (5.2 M parameters for small [S93]; ONNX exports and `transformers` ports exist [S83, S117]) | **Usable.** Apache-2.0 [S93] | Synthetic tick-label crops rendered from the corpus fonts; no source states a size for this task (bounded absence) | Its published accuracy is on PaddlePaddle's own multi-scenario set, not on small numeric tokens [S93 table] |
| 2 | **PARSeq**, trained with docTR's tooling | **Usable.** Both Apache-2.0 [S73, S83] | Same | No published accuracy on chart tick labels found |
| 3 | **TrOCR printed (small/base)** | **Usable with conditions.** The unilm repo is MIT and the TrOCR README defers to it [S107], but the Hugging Face weight cards declare no licence [S83] | Same | Heavier than CTC models; same evidence gap |
| — | Formula OCR (PP-FormulaNet Apache, UniMERNet Apache, pix2tex MIT) for exponents | Usable [S73, S83] | — | No evidence on tick-label crops. Simpler: let the layout detector propose a separate "exponent" box and fuse base and exponent by geometry, per figverify's rule [S1 App. C] (inference) |
| Avoid | Surya (weights modified OpenRAIL-M, CC BY-NC-SA on HF; now a 689 M-parameter VLM) [S88, S83]; GOT-OCR2 (repo has no licence while the HF card says Apache-2.0) [S73, S83]; plain Tesseract (needs training for superscripts [S106]) | — | — | — |

### Three things to know before building

1. **Learned confidence is not a gate.** On 180 scanned river charts, LineFormer's confidence picked the best candidate only 78.2 % of the time; confidence plus coverage reached 87.7 %; the author kept human selection [S98 §4.5.4, Table 4]. Use model scores to order proposals, never to accept them (inference, consistent with the issue's constraints).
2. **Synthetic-to-real is a real gap for chart text.** The best chart-text recogniser at ICPR 2020 scored NCRR 0.973 on synthetic charts and 0.765 on real PMC charts [S55 Table 4]. Plan a real raster evaluation set before training.
3. **Licence traps in the augmentation stack.** Albumentations (MIT) is unmaintained since June 2025 and its successor AlbumentationsX is AGPL-3.0 or commercial [S103]. Augraphy is MIT [S73]. ocrodeg has no licence [S73]. CHART-Synthetic (AdobeSynth) is CC BY-NC-ND 4.0 [S104].

## 1. Layout and chart-element detection

### 1.1 Detector families

| Family | Code licence | Weights licence | Small-object or small-dataset evidence | Inference cost evidence | Framework |
|---|---|---|---|---|---|
| RF-DETR N/S/M/L (det), all Seg sizes | Apache-2.0 | Apache-2.0 [S75 l. 23] | RF100-VL (100 small real datasets) AP_small: N 30.1, S 34.2, M 35.8, 2XL 38.7 [S78 Table 4]. Self-run by Roboflow | T4 TensorRT FP16: N 2.3 ms, S 3.5 ms, M 4.4 ms, L 6.8 ms [S75 l. 72–75]. No CPU or Apple-Silicon figures found | `rfdetr` pip; `transformers` has `rf_detr` [S117] |
| RF-DETR A/F/P/XL/2XL | Apache code | **PML 1.0**: commercial self-hosting needs Roboflow Enterprise [S75 l. 23] | — | — | `rfdetr[plus]` |
| D-FINE | Apache-2.0 | Apache-2.0 (per repo) | RF100-VL AP_small N 32.4, S 36.6, M 36.0 [S78 Table 4] | T4: N 2.1 ms, 3.8 M params [S75 l. 93] | `transformers` `d_fine` [S117] |
| DEIM (v1) | Apache-2.0 (LICENSE text) | same | not checked | — | repo |
| DEIMv2 | **Non-commercial** "DEIMv2 License" since 2026-08-24, covering code **and weights** [S74 §2, §5; commit "docs: update license information" 2026-08-24]. Also uses DINOv3, which has its own custom licence [S77] | non-commercial | — | — | `transformers` `deimv2` [S117] |
| LW-DETR | Apache-2.0 | Apache-2.0 [S75 l. 88–92] | RF100-VL AP_small N 31.2, S 32.1, M 34.0 [S78 Table 4] | T4 1.9–13.0 ms [S75] | `transformers` `lw_detr` |
| RT-DETR, RT-DETRv2 | Apache-2.0 | Apache-2.0 | RF100-VL AP_small M 36.4 [S78 Table 4] | — | `transformers` `rt_detr`, `rt_detr_v2` |
| RT-DETRv3 | Apache-2.0 (last push 2025-03) | — | — | — | Paddle |
| RT-DETRv4 (AIMIA-PKU) | Apache-2.0 | not checked | — | — | repo |
| Ultralytics YOLOv8/11/26 | **AGPL-3.0** or Enterprise licence; the Enterprise option is for "integration of Ultralytics software and AI models into business products" [S82] | same | RF100-VL AP_small 4.7–7.3 for YOLOv8/11 N–M [S78 Table 4]. The caption warns that YOLO's default NMS threshold "may not work well" across RF100-VL. These numbers come from a competitor and look anomalous next to AP 55–57; treat them as unverified | YOLO26n "up to 43 % faster CPU inference" than YOLO11n, ONNX, Intel Xeon 2.0 GHz [S81 §4, p. 7] (self-reported) | `ultralytics` |
| YOLOv10, v12, v13 | AGPL-3.0 | — | — | — | — |
| YOLOv9 (WongKinYiu) | GPL-3.0 | — | — | — | — |
| MIT YOLO (MultimediaTechLab/YOLO) | MIT | not checked | — | — | repo, last push 2026-03 |
| YOLOX | Apache-2.0 (last push 2025-06) | — | — | — | — |
| Co-DETR | MIT (last push 2024-12) | — | — | — | MMDetection 3 |
| DINO / detrex | Apache-2.0 | — | — | — | detectron2-based |

What the evidence does and does not say:
- The vendor's multi-dataset fine-tuning comparison is RF-DETR's own RF100-VL table [S78 Table 4]. Its YOLO AP_small numbers are a factor of five below the DETRs' while the overall AP is close. I cannot reconcile that from the paper, so I do not rely on it.
- An independent practitioner benchmark (DetectionBench: 79 models, 6 small real-world datasets, one recipe, weights public) found that "RF-DETR is not universally better than YOLO. It wins on SeaDronesSee and ExDark, loses on GWHD and Brackish" [S119, post body]. A commenter adds, from his own experiments, that RF-DETR on CPU is "around 10x slower than similarly named Yolo11", and that it showed better recall but worse precision at equal operating points [S119, comments]. These are forum claims, not measured by me.
- RF-DETR's own text: "Higher resolution improves small object detection performance" [S78 §3, p. 7]. Inference: run the detector on the panel crop at native resolution, or tile the page, so a tick label is not a 6 px object.
- Inference cost on CPU or Apple Silicon: no paper in this set measures it for the DETR family; the only figure is the forum claim above. Measure locally before choosing a size. If CPU cost decides, D-FINE-N (3.8 M parameters [S75 l. 93]) or YOLOX/MIT-YOLO are the permissive light options (inference).
- Net reading (inference): choose among Apache DETRs on dsdig's own held-out pages, not on COCO or RF100-VL tables. The licence rules out the YOLO families that would otherwise be the CPU-cheap baseline.

### 1.2 Document-layout models: do any detect chart sub-elements?

| Model | Classes relevant to charts | Code licence | Weights licence |
|---|---|---|---|
| Docling `docling-layout-heron` | "Picture" (17 classes total, none for chart parts) [S85] | MIT (`docling-ibm-models`) | Apache-2.0 [S83] |
| PP-DocLayout_plus-L (RT-DETR-L) | "chart" among 20 page classes; mAP@0.5 83.2 on Baidu's own set [S86] | Apache-2.0 | Apache-2.0 |
| PP-DocLayoutV2, V3 (V3: multi-point boxes for warped pages, ECCV 2026 per card), V4 (in `transformers`) | page-level classes; not chart sub-elements (cards read; V4 card not read) [S87, S117] | Apache-2.0 | Apache-2.0 |
| DocLayout-YOLO (v1 2024; v2 "Axis-Attentive … Mesh-Guided Synthetic Pretraining", arXiv 2610.28171, Oct 2026) | page-level classes | **AGPL-3.0** (built on Ultralytics) [S73] | HF card says Apache-2.0 [S83]. The weights run only through AGPL code (inference) |
| Surya layout | Table, Picture, SectionHeader, Equation, … [S88] | Apache-2.0 | modified OpenRAIL-M; HF tag CC BY-NC-SA 4.0 [S88 l. 70, S83] |
| EDocNet (datasheet layout) | 21 datasheet classes including "characteristic curve diagram", "timing diagram", "pin name diagram" [S108 §III, Fig. list] | no code or data release found | — |

EDocNet is the only datasheet-specific layout model I found. It trained on 8,677 page images from 500 component datasheets [S108 §V]. Its AP table is internally inconsistent: AP@[.5:.95] 0.765 is higher than AP@0.5 0.711 [S108 Table II]. I did not find a public dataset or weights (bounded absence, §10).

### 1.3 ChartDete / CACHED, re-checked

- Repository activity since 2026-09: a 2026-10-29 commit added Hugging Face links [S84]. The checkpoints and data are now on Hugging Face as "the original 2023 releases" [S84 README l. 26–28].
- Classes: 18, including `x_title`, `y_title`, `plot_area`, `xlabel`/`ylabel` (tick labels), `x_tick`/`y_tick` (tick marks), `legend_patch`, `legend_label`, `legend_title`, `legend_area`, `chart_title`, `x_axis_area`, `y_axis_area`, `mark_label`, `value_label`, `tick_grouping`, `other` [S84 model card "Classes"].
- Accuracy: COCO box AP 0.729 on 381 test images (ICPR 2022 PMC split), re-evaluated in October 2026 with the uploaded files at 0.729 [S84 model card table]. Self-reported.
- Licences: model card MIT; **dataset card CC BY-NC-SA 4.0** [S83, S84]. Inference: a model fine-tuned from these weights is not obviously encumbered by the data licence, but redistributing the data or derived data is.
- Burden: "a fork of MMDetection 2.28"; "cannot be loaded with `transformers`, upstream MMDetection, or MMDetection 3.x"; tested with PyTorch 1.13.1, CUDA 11.7, `mmcv-full` 1.7.2 [S84 model card "Quick start"]. MMDetection itself has had no push since 2024-08-21 [S73].
- Use (inference): as a teacher that pre-labels raster crops, or as a baseline to beat. Not as the production model.

### 1.4 Datasets with chart-element boxes

| Dataset | Boxes for | Licence | Fit |
|---|---|---|---|
| ChartDete-PMC2022 (refined ICPR 2022 PMC) | 18 element classes [S84] | CC BY-NC-SA 4.0 [S83] | Real scientific charts; non-commercial |
| PlotQA | title, x/y axis "bounding boxes, axis labels, ticks lables", legend, plot area, figure [S105 PlotQA_Dataset.md l. 29–34] | data CC BY-4.0, code MIT [S105 README l. 45] | **Permissive.** Synthetic bar, line and dot-line plots (inference: no log axes; the 2026-09 benchmarks doc found none) |
| CHART-Info 2024 / UB-PMC | text roles, tick positions, legend (tasks 3–5) | Built from PMC figures under CC BY or CC0 [S17 §3] | Real; 29 of 1,033 line charts with a log axis (2026-09 benchmarks doc) |
| AdobeSynth19 (CHART-Synthetic) | element annotations | **CC BY-NC-ND 4.0** [S104] | No derivatives |
| ChartGalaxy++ (Nov 2026) | infographic elements; "axis" is one group including ticks and tick labels [S116 App. "axis" definition] | per-source, Appendix C.1 (not read) | Low: infographics, not tick-level |
| InfoDet / OrionBench | infographic text and chart boxes (search listing only) | not checked | Low |

**Recommendation (inference).** dsdig's own vector corpus gives tick-label and tick-mark boxes exactly, and PlotQA is the only permissive public set with tick-label boxes. Pretrain on renders of the corpus, optionally mix PlotQA, and keep ChartDete-PMC2022 for evaluation only.

## 2. Curve segmentation on raster line charts

### 2.1 Candidates and evidence

| Work | Year | Representation | Evidence | Licence / code | Fit |
|---|---|---|---|---|---|
| **ChartZero** [S94] (Islam et al., Univ. of Florida) | 2026-05 | U-Net + CoordConv; semantic mask head + L2-normalised per-pixel embedding head; "Global Orthogonal Instance" loss forces instances into orthogonal subspaces and penalises small fragments at intersections | Trained only on synthetic simple functions. Instance IoU / NRMSE on its own benchmark: ChartZero 0.82 / 0.028; LineFormer 0.68 / —; LineEX — / 0.436; SAM 3 0.28 / — [S94 Table 3]. Ablation: U-Net+discriminative loss 0.71/0.087 → +CoordConv 0.75/0.071 → +GOI 0.82/0.028 [S94 Table 7]. Failure modes: local instance swapping at "highly acute intersections"; "phantom" curves or merges in dense clusters [S94 Fig. 5]. All self-reported | No code found (§10). Its benchmark (388 of 1,000 charts with a log axis) is "unreleased" per the 2026-09 benchmarks doc §1.3; I found no release since | **Highest method fit**; must be re-implemented |
| LineFormer [S3] | 2023 | Mask2Former instance masks, 3 px GT | Local, base model, 19 human-verified transfer charts: 8 charts with extra instances, 18 extra instances, 13 swap events, true-crossing purity 0.797, median of per-curve median error 1.41 px, p95 3.37 px. Battery fine-tune: 8, 17, 12, 0.850, 1.22 px, 3.18 px [S120] | **No licence** [S73]; issue #12 open since 2026-06-18, no reply [S115] | Baseline only |
| Yang, He, Zhang (Graphical Models) [S30] | 2025 | Mamba-enhanced mask-guided instance segmentation | Already in the 2026-09 survey; no code release found since (ywking/ChartParser is now **archived**, no licence [S73]) | — | — |
| ChartLine [S99] (same group) | 2024 | **semantic** (binary) curve detection; encoder–decoder with spatial-sequence attention | F-measure (ODS) 0.8162 on EXC synthetic set [S99 Table 1]. Notes that grid lines disturb curves "especially when the image is not a color image" [S99 §1] | Open-access paper; code not checked | Semantic only; no instance identity |
| PLECTA [S95] | 2026-11 | Deterministic: from a binary **centreline** mask, a maximum-weight partial matching at each junction decides which segments continue; crossing pixels may belong to several strands | Beat "three released alternatives at every density tested" on synthetic strand scenes [S95 Abstract] | MIT, code and Zenodo archive [S95 "Data and code availability"; S73] | **High** for crossing identity downstream of any mask model (inference: dsdig curves are x-monotone functions, a stronger prior than PLECTA assumes) |
| Danube gauge charts (ESSD 2026) [S98] | 2026-03 | LineFormer off the shelf on 19th-century scans, plus dewarping and calibration | See §6 for the confidence result | CC BY 4.0 paper | Practitioner evidence |
| Chart2SVG [S100] | 2026-08 | raster chart → SVG by an LLM agent | — | MIT code | Out of scope (LLM) |
| RF-DETR-Seg, Mask DINO, OneFormer, EoMT | — | query-based instance masks | No chart-specific evidence found | Apache (RF-DETR-Seg, Mask DINO), MIT (OneFormer, EoMT) [S73, S75] | Possible re-implementation base for LineFormer's approach |
| Deep Vectorization of Technical Drawings (Egiazarian et al.) | 2020 | regresses vector primitives directly | Line drawings, not charts | MPL-2.0 [S73] | Low |

### 2.2 Which output gives sub-pixel centreline accuracy?

No source in this set measures centreline accuracy of a learned chart-curve model at the sub-pixel level (bounded absence, §10). What the evidence supports:

- **Instance masks are a poor carrier of position.** LineFormer trains on 3 px-thick masks [S3] and dsdig's local run gives a median error near 1.2–1.4 px with p95 above 3 px [S120]. Inference: the mask defines which pixels belong to a curve, not where its centre is.
- **Quantised dense outputs lose position unless decoded with care.** In pose estimation, heatmap decoding "suffers from quantisation errors", fixed by distribution-aware sub-pixel decoding [S114 §1]. Inference: the same applies to any per-column heatmap or downsampled mask.
- **Classic sub-pixel line extraction already exists.** Steger's ridge detector gives sub-pixel line centres from image derivatives (search listing only; not opened). Inference: run it on the model's probability map, or on the raw raster inside the model's mask, rather than ask the network to regress coordinates.
- **Topology-aware losses help connectivity, not position.** clDice targets topology preservation for tubular structures [S113 Abstract]. It addresses gaps at thin segments, not centre offsets (inference).
- **Polyline or Bézier regression** (vectorization models) has no chart evidence in this set.

Proposed head for dsdig (inference): a semantic probability map at full input resolution, a distance-to-centreline regression channel, and an embedding channel for identity. Extract centres with sub-pixel ridge detection, link identities through crossings with an x-monotone matching, and let dsdig's existing tracer and gates decide.

### 2.3 Training framework burden, late 2026

| Option | State on 2026-11-24 | Burden (inference unless noted) |
|---|---|---|
| Hugging Face `transformers` 5.24.0 (2026-11-21) | Has `mask2former`, `oneformer`, `eomt`, `eomt_dinov3`, `rf_detr`, `d_fine`, `lw_detr`, `rt_detr(_v2)`, `deimv2`, `sam3`, `pp_ocrv5_*`, `pp_ocrv6_*`, `pp_doclayout_v2/v3/v4`, `pp_formulanet`, `trocr`, `got_ocr2` [S117] | Lowest: one framework for detector, segmenter and OCR |
| MMDetection | last push 2024-08-21 [S73] | High: ChartDete and LineFormer both pin MMDetection 2.x forks [S84, S3] |
| detectron2 | Apache-2.0, last push 2026-10-27 [S73] | Medium; Mask2Former's own repo is archived [S73] |
| Plain PyTorch U-Net | own code | Low, and the ChartZero-style model is small |

## 3. Tick-label OCR

### 3.1 Candidates

| Model | Code / weights licence | Fine-tuning on synthetic crops | Published accuracy relevant to small numeric text | Cost |
|---|---|---|---|---|
| PP-OCRv6 rec (tiny/small/medium; June 2026; arXiv 2606.13108) | Apache-2.0 / Apache-2.0 [S93, S83] | PaddleOCR fine-tuning docs; also `transformers` ports [S117] | Small: W-Avg 81.3, Printed EN 93.3 on Baidu's internal multi-scenario set [S93 table]. Not numeric-specific. Self-reported | small 5.2 M params, CTC+NRTR heads [S93] |
| PP-OCRv5 rec (server/mobile/en mobile) | Apache-2.0 [S83] | yes | Printed EN 85.1 (server) on the same set [S93 table]; the PaddleOCR 3.0 report evaluates on a "self-built dataset" [S92 Fig. 1 caption] | mobile variant "tailored for deployment in CPU-only environments" [S92 §2.1] |
| PARSeq | Apache-2.0 (last push 2024-05) [S73]; docTR ships a PARSeq checkpoint, Apache-2.0 [S83] | docTR has training scripts (not read) | none found | — |
| docTR | Apache-2.0, active [S73] | yes | none found | — |
| TrOCR printed | MIT repo; HF cards declare **no licence** [S83, S107] | yes (HF) | none found | encoder-decoder, heavier (inference) |
| EasyOCR | Apache-2.0 [S73] | yes | none found | — |
| Tesseract 5 | Apache-2.0; `tesstrain` for training [S73] | yes | Tesseract "can not recognize the small number '1'" in superscripts; the maintainer's answer is to train it [S106] | CPU |
| GOT-OCR2 | repo: no licence; HF cards: Apache-2.0 [S73, S83] | — | — | not checked |
| Surya | code Apache-2.0; weights restricted [S88] | — | — | now a 689 M-parameter VLM [S88 l. 23] |
| PP-FormulaNet plus S/L, UniMERNet, pix2tex | Apache / Apache / MIT [S83, S73] | yes | none on tick labels | heavier than text OCR |

Chart-specific evidence:
- ICPR 2020 CHART-Info Task 2: top recogniser NCRR 0.973 on AdobeSynth vs 0.765 on UB-PMC; a Tesseract-based entry 0.606 on UB-PMC. The organisers: Tesseract "is not specifically designed for chart OCR … tuning models to perform OCR for charts is necessary" [S55 Table 4, §5.2].
- Misreads documented in the 2026-09 survey: "20"→"2" [S26], 0.5→5 and 5.6→56 [S30].
- Inference, not sourced: CTC decoding merges repeated characters unless a blank separates them, so a narrow "00" at low horizontal resolution can collapse to "0". This matches the "20"→"2" class of digit drop only loosely; test it on synthetic crops of "100", "1000", "0.001".

### 3.2 Design for exponents and units (inference)

- Detect base and exponent as separate boxes in the layout model (class `tick_exponent`). Recognise each with a digit-and-sign charset. Fuse "10" + exponent only when the exponent box is smaller and raised, as figverify does [S1 App. C].
- Restrict the decoder charset to digits, `.`, `,`, `−`, `-`, `+`, `E`, `e`, `µ`, `m`, `k`, `M`, `n`, `p` and space; reject outputs that do not parse.
- Every reading goes through `axis_calibration`'s linear/log fit and residual gate; a reading that breaks the fit is refused, not corrected.

## 4. Synthetic-to-real training from exact vector ground truth

| Evidence | What it shows | Source |
|---|---|---|
| ICPR 2020 Task 2 OCR: 0.973 synthetic vs 0.765 real NCRR | A large synthetic-to-real gap for chart text | [S55 Table 4] |
| ChartZero: synthetic-only training, zero-shot on ExcelData400k and LineEX test sets (IoU 0.62 and 0.69, close to LineFormer's 0.58 and 0.64) | Synthetic simple-function curves can transfer to other chart styles, self-reported | [S94 Table 3] |
| DocLayout-YOLO v1: DocSynth-300K pretraining +1.2 mAP on D4LA and +2.6 on DocLayNet, still followed by real fine-tuning | Synthetic pretraining helps a little when real data follows | [S89 §5.4.1, Table 1] |
| DocLayout-YOLO v2: MeshLayout-1M pretraining alone +0.5/1.3/1.7/4.7 mAP averaged over five sizes | Same pattern | [S90 §5 ablation] |
| Augraphy | A denoiser trained on Augraphy-noised pages scored SSIM 0.71 on the physically degraded ShabbyReal set; Tesseract word accuracy dropped 52 % on Augraphy-noised pages. **No with/without-augmentation transfer ablation** | [S102 §5.4, §6.1] |
| Danube 19th-century charts | An off-the-shelf LineFormer needed dewarping, baseline correction and human selection on scans | [S98 §4.5] |
| PowerBrain | Decoded datasheet PDF vector data as ground truth (2026-09 survey) | [S26 §IV.B] |
| DocBank (second-hand) | Token-level labels by weak supervision from LaTeX-compiled PDFs | [S108 §II.A, second-hand] |

How much real fine-tuning data is needed: **no source in this set quantifies it for charts or chart text** (bounded absence, §10). Inference: hold out a real raster set (dsdig's scanned or raster-only datasheets) for evaluation from day one; only then decide whether real fine-tuning is needed.

Known sim-to-real failure modes, from the sources above: OCR on real figures [S55]; dense or acute crossings [S94 Fig. 5]; warped scans and baseline drift [S98 §4.5]; grid lines on monochrome charts [S99 §1, S30 §4.5].

Augmentation licences: Augraphy MIT; genalog MIT; Albumentations MIT but unmaintained, AlbumentationsX AGPL-3.0 [S73, S103]; ocrodeg no licence [S73].

## 5. Datasheet-specific prior art since 2026-09 and anything missed

| Item | Date | What it is | Relevance |
|---|---|---|---|
| EDocNet [S108] | arXiv 2025-02 (missed by the 2026-09 survey) | Datasheet layout detector, 21 classes, 8,677 pages from 500 devices; same group as EDocCurve [S25] | Panel-level only; no release found |
| D2S-FLOW [S109] | arXiv 2025-02 | Parameter extraction from datasheet tables (metadata only) | Not charts |
| PartsPlan AI guide [S110] | 2026-10-11 | Commercial datasheet MCP. One IGBT (IKW40N120CS6), 30 graph-only values: 30/30 within 3 % or 0.05 V, mean error 0.3 %, max 3.3 % (self-reported; method undisclosed) | Shows a vendor product; the tolerance is far looser than dsdig's 0.5 % |
| SPECTRUM (IGBT) [S28] | 2025-10 | Cites LineFormer per OpenAlex [S101] | Still paywalled (2026-09 survey, §9) |
| Rajendran, Kotta, Yousef, KONVENS 2026 [S118] | 2026-09 | Review of 66 extraction tools; chart digitisation tested on 6 figures with DePlot | Low: no datasheet charts, no proposal-component evaluation |
| PowerBrain follow-ups | — | None found (search §10) | — |
| Datasheet chart datasets | — | None public found | — |

## 6. Verification-friendly design: learned proposer, deterministic verifier

| Evidence | Lesson |
|---|---|
| Danube: LineFormer confidence recalled 78.2 % of the visually best candidates; a confidence/coverage blend 87.7 %; automatic selection 0.937 vs manual 0.968 accuracy; "we therefore retain manual visual inspection" [S98 §4.5.4, Table 4] | Model scores rank candidates; a check outside the model must accept them |
| ChartZero failure modes: phantom curves, swaps at acute crossings [S94 Fig. 5] | Every proposed curve must be supported by stroke pixels or a vector path, and every crossing re-checked |
| figverify re-render certificate; RF-CDA closed loop; KM-GPT re-plot (2026-09 survey) [S1, S35, S39] | Re-render and diff is the established gate |
| Conformal prediction for boxes: two-step conformal intervals give coverage guarantees for box coordinates under exchangeability; the guarantee is marginal [S111 §2] | Usable to size the **search window** around a proposed box from a calibration set. Not a per-chart guarantee; a datasheet from a new vendor breaks exchangeability (inference) |
| Probabilistic OD with conformal prediction (Ries et al., COPA 2026) [S112] | Same family; abstract only |

Tri-state mapping (inference):
- `proposal_ok` only when a geometric check passes: a tick-label box contains a parsed token and lies next to a tick or gridline stroke; a curve mask overlaps stroke pixels and its centreline re-renders within the residual gate.
- `unverified` when the model proposed something the geometry cannot check (no stroke found, OCR token unparsable).
- `refused` when model and geometry disagree.
- Model confidence never upgrades a state.

## 7. What changed since the 2026-09 survey

1. **DEIMv2 relicensed to non-commercial** on 2026-08-24, code and weights [S74]. (Before the survey, but the survey did not cover detectors.)
2. **ChartDete checkpoints and data on Hugging Face** (2026-10-29), with the dataset tagged CC BY-NC-SA 4.0 [S84].
3. **LineFormer**: still no licence; an explicit licence request (issue #12, 2026-06-18) has no reply [S115].
4. **New curve-segmentation work**: ChartZero (2026-05, not in the survey) [S94]; PLECTA (2026-11) [S95].
5. **New benchmarks**: PlotGround-1k (2026-10; 1,119 questions from 1,066 bioRxiv preprints with author source data; best model 87.5 % at ±5 %, and every model loses 11–24 points at ±2 %) [S96 Abstract]. VLM-only and ±2 % at best, so not a dsdig yardstick (inference).
6. **PP-OCRv6** (2026-06) and **PP-DocLayoutV3/V4**; `transformers` now carries RF-DETR, D-FINE, LW-DETR, DEIMv2, EoMT, SAM 3 and the PP-OCR family [S93, S87, S117].
7. **Surya** became a single 689 M-parameter VLM [S88].
8. **Albumentations** handed over to AGPL AlbumentationsX [S103].
9. **DocLayout-YOLO v2** (2026-10) with synthetic pretraining [S90].
10. Practitioner: a commercial datasheet MCP (PartsPlan) reports graph reading on one IGBT at 3 % tolerance [S110].

## 8. Where this contradicts or extends the issue's "Off-the-shelf options" table

1. **"Element detection | ChartDete (MIT)".** Code and weights are MIT, but the training data is CC BY-NC-SA 4.0, and the model needs MMDetection 2.28 / `mmcv-full` 1.7.2 / PyTorch 1.13.1 [S84]. "Reasonable base for fine-tuning" holds only inside that legacy stack. An Apache DETR (RF-DETR, D-FINE) fine-tuned on corpus renders is the better base; ChartDete fits as teacher or baseline.
2. **Task 2 says "RT-DETR or YOLO".** Every current Ultralytics YOLO is AGPL-3.0 or paid [S82]; YOLOv9 and MMYOLO are GPL-3.0 [S73]. The permissive choices are RF-DETR N–L, D-FINE, DEIM v1, RT-DETR(v2), LW-DETR, YOLOX, MIT YOLO.
3. **"LineFormer | no licence file".** Still true, and now with an unanswered public request [S115]. It should move from "weak" to "not usable as a dependency".
4. **Task 3 says "Mask2Former-style instance segmentation".** The newest evidence (ChartZero, self-reported) favours a dense embedding model trained on synthetic data over Mask2Former-style queries on thin crossing curves [S94 Table 3]. Query models over-produce instances in both local and published runs [S120, S98].
5. **Task 4 says "TrOCR or PaddleOCR".** TrOCR's HF weights carry no licence tag [S83]. PaddleOCR now has PP-OCRv6 (Apache) [S93].
6. **Missing row:** document-layout models (Docling, PP-DocLayout, DocLayout-YOLO, Surya, EDocNet) do not detect chart sub-elements; at most they find the panel [S85, S86, S108].
7. **The table's "Keypoints" and "Chart-to-table" rows** are unchanged by this research.

## 9. Claims I could not verify, with the highest access rung reached

| Item | What depends on it | Highest rung and result |
|---|---|---|
| ChartZero code or benchmark release | Whether task 3 can start from released weights | GitHub search "ChartZero" (one unrelated Chinese charting app), paper text has no code link, Serper web search. Not found. No access block involved |
| EDocNet dataset or weights | A datasheet-layout pretraining source | Paper text has no release link (grep "github", "available"). Not found |
| PARSeq, docTR, EasyOCR accuracy on small numeric tokens | Ranking in task 4 | No published number found; must be measured locally |
| CPU / Apple-Silicon latency of RF-DETR, D-FINE, PP-OCRv6 | Inference cost | Not published in the sources read; must be measured locally |
| RF100-VL YOLO AP_small anomaly | Detector ranking | Only one (competitor-run) source. The independent DetectionBench thread [S119] was read in full through Fab's Chrome; it reports mAP per dataset, not AP_small, so the anomaly stays unexplained. The ranking in this note does not rest on it |
| CPU latency claim "RF-DETR ≈10× slower than YOLO11 on CPU" | Detector cost | Forum comment only [S119]; not measured |

No source remains blocked. The KONVENS 2026 review was blocked on ResearchGate at every browser rung, including Fab's Chrome (DataDome CAPTCHA, also on the homepage warm-up), and was then obtained from the conference host [S118].

## 10. Search record

**Seed set (§2.3 of the research contract).** Built before external queries from: issue #19 (`gh issue view 19`); `docs/chart-digitisation-landscape-2026-09.md` (all 397 lines read in two windows, end reached); the executive summary and §3 of `docs/chart-digitisation-benchmarks-2026-09.md`; the headings of `docs/model-benchmark-2026-09.md`; `out/lineformer-eval/summary_base.txt` and `summary_battery.txt`; `~/dev/kb/INDEX.md` (grep for chart/digitise/vision/OCR/YOLO/DETR/segment: no chart-model note exists). Origin citations for this topic: LineFormer [S3], ChartDete [S53/S84], Yang 2025 [S30], ChartOCR/LineEX/ChartDETR, TrOCR, PaddleOCR, RT-DETR, YOLO, Mask2Former, `tools/synth_charts`.

**Surfaces.** Serper (Google web); GitHub REST API and `gh search repos`; Hugging Face model API and raw model cards; OpenAlex API (works search and `cites:` filter); arXiv PDF endpoint. The arXiv export API and the Semantic Scholar API both returned HTTP 429 at 14:37Z; I switched to OpenAlex for lookups and did not retry them.

**Queries (Serper unless noted).**
- "line chart curve instance segmentation 2026 arXiv crossing curves same color"
- "ChartZero GitHub code \"Global Orthogonal Instance\" chart extraction"
- "datasheet curve extraction deep learning 2026 MOSFET IGBT chart digitization dataset" (past year)
- "\"datasheet\" curves extraction automatic 2026 arXiv power semiconductor characteristic curves digitization neural" (past month)
- "PowerBrain datasheet extraction KU Leuven Tian 2025 2026 follow-up curve extraction"
- "sub-pixel centerline extraction deep learning distance transform regression thin curvilinear structures accuracy"
- "raster line drawing vectorization neural network polyline Bezier output 2025 2026 arXiv technical drawings"
- "tick label OCR chart axis numbers superscript exponent recognition small text fine-tune PARSeq PaddleOCR"
- "CTC decoding repeated characters collapse \"00\" digits dropped …"
- "OCR superscript recognition scientific notation \"10^\" exponent …"
- "synthetic document degradation training transfer to real scans ablation OCR layout \"sim-to-real\" …"
- "conformal prediction object detection bounding box guarantees 2024 2025 arXiv document layout"
- "chart element detection tick label detection dataset 2026 arXiv axis tick labels bounding boxes detector"
- OpenAlex: works citing LineFormer (W4385990998, 27 results, all listed) and citing Yang 2025 (W4408815964, 4 results); title lookups for RF-DETR, D-FINE, DEIM, RT-DETRv4, DocLayout-YOLO, PaddleOCR 3.0, Augraphy, the two 2026 reviews.
- GitHub: `gh search repos` "ChartZero", "line chart instance segmentation" (0 results), "chart curve segmentation" (0 results); `gh api` licence and activity for 60 repositories [S73].

**Challenge searches (one per conclusion cluster).**
- (a) "DETRs beat YOLO for small objects after fine-tuning": query "YOLO outperforms DETR small dataset fine-tuning small objects comparison RT-DETR D-FINE RF-DETR 2026". Found one contrary practitioner claim (LinkedIn snippet, not opened, not cited as evidence) and the DetectionBench thread [S119], which contradicts "DETR always wins" (2 wins, 2 losses). Conclusion weakened to "vendor-reported; choose on dsdig's own held-out pages", and the ranking does not rest on AP_small.
- (b) "LineFormer is not usable": checked the repository's issues for a licence (found #12, open). No contrary evidence.
- (c) "Synthetic-only training can work for curves": the contrary evidence is the OCR gap [S55] and the Danube scans [S98]; both are reported.
- (d) "No layout model detects chart sub-elements": checked class lists of Heron, PP-DocLayout-plus, PP-DocLayoutV3 card, Surya README, EDocNet. Bounded to those models.

**Absence claims.**
- "No public code or benchmark for ChartZero": searched GitHub and Google on 2026-11-24 with the title and the loss name; paper text grep. Confidence medium (authors may release later or under another name).
- "No source quantifies real fine-tuning data needed for chart models": searched within all papers read plus the sim-to-real query above. Confidence low-to-medium: I ran one dedicated query.
- "No published sub-pixel centreline accuracy for a learned chart-curve model": within the sources read and the LineFormer/Yang citation lists. Confidence medium.
- "No public datasheet chart dataset": searches above plus OpenAlex citation lists. Confidence medium.
- "No new PowerBrain follow-up": one Serper query plus the OpenAlex lists. Confidence low-to-medium.

## 11. Source access log

Columns: ID · identifier (URL/DOI, version) · evidence state · attempt history · route · validation · identity · load-bearing. Rows for sources reused from the 2026-09 survey (S1, S3, S17, S25, S26, S28, S30, S35, S39, S53, S55) are in that document; S55 Table 4 and §5.2 were re-read today from the archived `icpr2020.pdf` (route raw text, validated, local).

| ID | Identifier | State | Attempts | Route | Valid. | Identity | LB |
|---|---|---|---|---|---|---|---|
| S73 | GitHub REST API `repos/{owner}/{repo}` for 60 repositories (licence SPDX, pushed_at, archived), 2026-11-24; output kept at `/tmp/r19/ghmeta.tsv` | inspected | gh api, first attempt; 2 repos 404 (augraphy/augraphy, tmbdev/ocrodeg), re-queried as sparkfish/augraphy, NVlabs/ocrodeg and ocropus/ocrodeg | raw text | validated | anonymous | yes |
| S74 | https://github.com/Intellindust-AI-Lab/DEIMv2/blob/main/LICENSE.md and commit history of LICENSE.md / LICENSE | inspected (§1–7) | gh api | raw text | validated | anonymous | yes |
| S75 | https://github.com/roboflow/rf-detr README (develop, 2026-11-24) | inspected (licence, benchmark tables) | gh api | raw text | validated | anonymous | yes |
| S76 | https://github.com/facebookresearch/sam3/blob/main/LICENSE (SAM License, 2025-11-19) | partial (header, definitions) | gh api | raw text | validated | anonymous | no |
| S77 | https://github.com/facebookresearch/dinov3/blob/main/LICENSE.md (DINOv3 License, 2025-08-19) | partial (header) | gh api | raw text | validated | anonymous | no |
| S78 | arXiv:2511.09554v2, RF-DETR (ICLR 2026), doi 10.48550/arXiv.2511.09554 | partial (§3 resolution bullet, §5, Table 4, p. 9) | OpenAlex lookup; curl arxiv.org/pdf → %PDF 12.3 MB | raw text | validated (title p.1) | anonymous | yes |
| S79 | arXiv:2410.13842v1, D-FINE | metadata only (identity checked) | curl %PDF | raw text | validated | anonymous | no |
| S80 | arXiv:2412.04234v3, DEIM (CVPR 2025, doi 10.1109/CVPR52734.2025.01412) | metadata only | curl %PDF | raw text | validated | anonymous | no |
| S81 | arXiv:2606.03748v1, Ultralytics YOLO26 | partial (STAL, CPU claim p. 7) | curl %PDF | raw text | validated | anonymous | no |
| S82 | https://github.com/ultralytics/ultralytics README "License" | inspected | gh api | raw text | validated | anonymous | yes |
| S83 | Hugging Face model API `api/models/{id}` cardData.license for 31 repos (listed in §1–3), 2026-11-24 | inspected | curl JSON; `datalab-to/*` and `mindee/parseq` returned "Invalid username or password" (gated or absent; not used) | raw text | validated | anonymous | yes |
| S84 | https://github.com/pengyu965/ChartDete README (2026-10-29) and https://huggingface.co/Pengyu965/ChartDete model card; dataset card licence via S83 | inspected | gh api; curl raw README | raw text | validated | anonymous | yes |
| S85 | https://huggingface.co/docling-project/docling-layout-heron card | inspected (class map) | curl raw | raw text | validated | anonymous | no |
| S86 | https://huggingface.co/PaddlePaddle/PP-DocLayout_plus-L card | inspected | curl raw | raw text | validated | anonymous | no |
| S87 | HF cards PP-DocLayoutV2, PP-DocLayoutV3 | partial | curl raw | raw text | validated | anonymous | no |
| S88 | https://github.com/datalab-to/surya README (2026-11-13) | partial (licence, features) | gh api | raw text | validated | anonymous | no |
| S89 | arXiv:2410.12628v1, DocLayout-YOLO | partial (§5.4.1, Table 1) | curl %PDF | raw text | validated | anonymous | no |
| S90 | arXiv:2610.28171v1, DocLayout-YOLO v2 | partial (abstract, ablation text) | curl %PDF | raw text | validated | anonymous | no |
| S91 | arXiv:2503.17213v1, PP-DocLayout | metadata only | curl %PDF | raw text | validated | anonymous | no |
| S92 | arXiv:2507.05595v1, PaddleOCR 3.0 Technical Report | partial (§2.1, Fig. 1 caption) | curl %PDF | raw text | validated | anonymous | no |
| S93 | https://huggingface.co/PaddlePaddle/PP-OCRv6_small_rec card (PP-OCRv6, arXiv 2606.13108 not fetched) | inspected (card) | curl raw | raw text | validated | anonymous | yes |
| S94 | arXiv:2605.05820v1, ChartZero (Islam, Mahmud, Saha, Tehranipoor, Farahmandi) | partial (Abstract, §3.2–3.3, §4.3–4.8, Tables 3, 7) | curl %PDF | raw text | validated | anonymous | yes |
| S95 | arXiv:2611.21283v1, PLECTA (Allan, Ostanin); code https://github.com/Jorallan/PLECTA | partial (Abstract, §1, data availability) | curl %PDF; gh api | raw text | validated | anonymous | no |
| S96 | arXiv:2610.06825v2, PlotGround | partial (Abstract, §1) | curl %PDF | raw text | validated | anonymous | no |
| S97 | arXiv:2611.23680, Structured extraction of diffraction data from COFs | metadata only (title page) | curl %PDF | raw text | validated | anonymous | no |
| S98 | Rehbein, ESSD 18:1783–1811 (2026), doi 10.5194/essd-18-1783-2026 | partial (Abstract, §4.5.4, Table 4) | curl %PDF 15 MB | raw text | validated | anonymous | yes |
| S99 | ChartLine, PMC11548359 | partial (§1, §4, Table 1) | fetch.py headless → status 203, CONTENT, title matched | DOM | validated | anonymous | no |
| S100 | arXiv:2608.26544v1, Chart2SVG | partial (code link, metrics) | curl %PDF | raw text | validated | anonymous | no |
| S101 | OpenAlex `filter=cites:W4385990998` and `cites:W4408815964`; title lookups | inspected | API JSON | raw text | validated | anonymous | no |
| S102 | Augraphy, ICDAR 2023, doi 10.1007/978-3-031-41682-8_24; arXiv:2208.14558v2 | partial (§5–6, Tables 3–4) | curl %PDF | raw text | validated | anonymous | no |
| S103 | https://github.com/albumentations-team/albumentations README (archived) | partial (notice) | gh api | raw text | validated | anonymous | no |
| S104 | https://github.com/adobe-research/CHART-Synthetic LICENSE (CC BY-NC-ND 4.0) | inspected (header) | gh api | raw text | validated | anonymous | no |
| S105 | https://github.com/NiteshMethani/PlotQA README and PlotQA_Dataset.md | partial (licence, annotations) | gh api | raw text | validated | anonymous | no |
| S106 | https://github.com/tesseract-ocr/tesseract/issues/3853 | inspected | gh api | raw text | validated | anonymous | no |
| S107 | https://github.com/microsoft/unilm/blob/master/trocr/README.md "License" | inspected (section) | gh api | raw text | validated | anonymous | no |
| S108 | arXiv:2502.16541, EDocNet (Chen, Wu, Zhang) | partial (§II–III, §V, Tables II–III) | curl %PDF | raw text | validated | anonymous | no |
| S109 | arXiv:2502.16540, D2S-FLOW | metadata only | curl %PDF | raw text | validated | anonymous | no |
| S110 | https://partsplanai.com/en/guides/graph-values-from-datasheets (updated 2026-10-11) | inspected | fetch.py headless → CONTENT | DOM | validated | anonymous | no |
| S111 | arXiv:2403.07263v2, Timans et al., ECCV 2024, doi 10.1007/978-3-031-73223-2_21 | partial (Abstract, §2) | curl %PDF | raw text | validated | anonymous | no |
| S112 | arXiv:2605.07549v2, Ries et al., PMLR 329 (COPA 2026) | metadata + abstract | curl %PDF | raw text | validated | anonymous | no |
| S113 | arXiv:2003.07311v7, clDice (CVPR 2021) | metadata + abstract | curl %PDF | raw text | validated | anonymous | no |
| S114 | arXiv:1910.06278v1, DARK (CVPR 2020) | partial (Abstract, §1) | curl %PDF | raw text | validated | anonymous | no |
| S115 | https://github.com/TheJaeLal/LineFormer/issues/12 and commit list | inspected | gh api | raw text | validated | anonymous | yes |
| S116 | arXiv:2611.15418v1, ChartGalaxy++ | partial (code links, element definitions) | curl %PDF | raw text | validated | anonymous | no |
| S117 | https://github.com/huggingface/transformers `src/transformers/models` listing; latest release v5.24.0 (2026-11-21) | inspected | gh api | raw text | validated | anonymous | no |
| S118 | A. Rajendran, J. Kotta, T. Yousef, "Numerical Evidence Extraction from Scientific PDFs: A Systematic Review and Empirical Evaluations", KONVENS 2026 paper 125; copy https://www.korpuslab.uni-hamburg.de/en/konvens2026/125.pdf (file `konvens2026_125.pdf`); ResearchGate 414679716; no DOI found (OpenAlex title search, 2026-11-24) | partial (Abstract, §3.2, §4 test-set description) | OpenAlex: not indexed; RG rung 4 fetch.py headless: 403, DataDome interstitial (`captcha-delivery.com`), 36 s; RG rung 5 headed scratch-profile Chrome (off-screen, 45 s): blank, title "researchgate.net"; RG Fab's Chrome via `fab-chrome-fetch`: CAPTCHA (geo.captcha-delivery), 0 bytes; RG homepage warm-up then deep link in the same session (recipe of 2026-10-30): homepage itself CAPTCHA after 61 s; challenge not solvable through this one-shot tool. Rung 6 Serper RAG found the conference-host copy: curl 200 %PDF 611 kB, title and authors match | raw text | validated (title p.1) | anonymous (final copy); real profile — gate AUTHORIZED 2026-11-24T14:34:08Z (RG attempts) | no |
| S119 | https://old.reddit.com/r/computervision/comments/1vmi5ec/detectionbench_an_open_benchmark_comparing_yolo/ (posted 2026-08-12) | inspected (post and all 9 comments) | known host → `fab-chrome-fetch` first: exit 4 (relay busy with another session's Codex clients, 30 s); retry with 600 s lock wait → CONTENT 11.5 kB, 8 s; tab closed, extension placeholder closed | DOM | validated (title matched) | real profile — gate AUTHORIZED 2026-11-24T14:34:08Z | no |
| S120 | Local: `/Users/fab/dev/pv/ee/datasheet-chart-digitizer/out/lineformer-eval/summary_base.txt`, `summary_battery.txt` (gitignored) | inspected (transfer human-verified block) | local read | raw text | validated | local | yes |
| S121 | OpenAlex record, "Metadata Extraction From Tables and Charts in Scientific Publications: A Systematic Literature Review", doi 10.1177/22104968261506439 | metadata only | API JSON | raw text | validated | anonymous | no |

Archive: PDFs in `/Users/fab/dev/pv/ee/datasheet-chart-digitizer/out/chart-digitisation-refs/2026-11/` with `SHA256SUMS` (23 files). The arXiv papers, the ESSD paper and the KONVENS paper are open access. Model cards, READMEs and web pages were read as text and are not archived.

## 12. Browser and delegation record

- Browser proof: `fetch.py` (Playwright, Chromium 149 headless, plain Chrome UA, launch mode, owns its browser) on https://github.com/lyuwenyu/RT-DETR at 14:34Z: CONTENT, title matched.
- Rung-5 headed Chrome: Playwright `launch_persistent_context`, channel chrome, off-screen window, scratch profile `$TMPDIR/r19prof-rddfnz75`; closed in `finally`; profile directory deleted afterwards; no process left running.
- Profile gate `check`: exit 0 (AUTHORIZED) at 2026-11-24T14:34:08Z. Fab's Chrome was used only through `fab-chrome-fetch`, three runs (S119; S118 deep link; S118 homepage + deep link). Each run reported "tab: closed (ours) … pre-existing tabs all present and unchanged; extension placeholder tab closed". Tabs listed 1, closed 1, held 0, failed 0 per run. An AppleScript tab listing after the runs showed none of the fetched URLs and no blank tab.
- No sub-agents were launched.
