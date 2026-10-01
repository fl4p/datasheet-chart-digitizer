"""Locate RDS(on)-versus-VGS panels: caption, owned plot frame, owned x axis.

The shared finder (``dsdig find``) routes RDS(on) captions into a single
``rds_on`` kind and, on the sample set this class was built against, misses
most RDS(VGS) panels outright: TI's "Figure 4-7" numbering, Rohm's "Fig.12"
with no space, International Rectifier's "Fig 12." on a page whose frame is a
line grid, charts embedded as images, and pages that are nothing but image
strips. Rather than widen the shared finder -- a data-dependent change that
the full-corpus harness must gate -- this class locates its own panels, and
treats the finder's ``rds_on`` panels as extra caption candidates only.

A caption is only a locator. A panel is owned when, in addition:

  * one plot frame (vector rectangle, or a closed raster grid frame on the
    rendered page) sits directly above or below the caption in its column; and
  * the frame's x-axis title names the gate-source voltage and does NOT name a
    drain current. That second clause is what keeps "On-Resistance Variation
    with Gate Voltage and Drain Current" (onsemi/Fairchild: RDS versus ID at
    several VGS) out of this class even though its caption mentions the gate.

Pages whose text layer is empty or image-only are OCRed page-wide (tesseract),
rendered into the run's own work directory -- never into /tmp.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path

import pymupdf

from .find_charts import (
    PageText,
    detect_raster_grid_frames,
    group_words_into_lines,
    line_bbox,
    line_text,
    run_text_bbox,
)
from .finder_caption_geometry import bbox_iou, page_vector_plot_frames
from .finder_text_ocr import page_text_from_tesseract_tsv, tesseract_tsv
from .finder_types import Word
from .text_dashes import normalize_dashes

BBox = tuple[float, float, float, float]

KIND = "rds_on_vgs"
PAGE_RENDER_DPI = 150
PAGE_OCR_DPI = 300
MIN_TEXT_WORDS = 40
MAX_CAPTION_GAP_ABOVE_PT = 80.0   # frame above caption: x tick labels + axis title between
MAX_CAPTION_GAP_BELOW_PT = 60.0   # frame below caption (Rohm, Winsok)
CAPTION_AMBIGUITY_PT = 10.0
MARGIN_LEFT_PT = 58.0
MARGIN_BOTTOM_PT = 40.0
MARGIN_TOP_PT = 12.0
MARGIN_RIGHT_PT = 12.0

_CAPTION_START_RE = re.compile(r"(?i)^(?:figure|fig)\.?\s*\d*")
_NUMBERED_CAPTION_RE = re.compile(
    r"(?i)^(?:figure|fig)\.?\s*(\d+(?:[.\-]\d+)?[a-z]?)\s*[.:]?\s*(.*)$"
)
_RDS_TOKEN = (
    r"(?:(?:static\s+)?(?:drain[-\s]*(?:to[-\s]*)?source\s+)?on[-\s]*(?:state\s*)?[-\s]*resistance"
    r"|r\s*ds\s*\(?\s*on\s*\)?|rdson)"
)
_GATE_TOKEN = (
    r"(?:gate(?:[-\s]*(?:to[-\s]*)?source)?\s*voltage|g\s*-\s*s\s+voltage|v\s*gs\b)"
)
RDS_VGS_TITLE_RE = re.compile(
    _RDS_TOKEN + r"[^.;]{0,50}?(?:\bvs\.?|\bversus\b|\bwith\b|\bv\b|[-–—])\s*" + _GATE_TOKEN,
    re.IGNORECASE,
)
# OCR reads the subscript "GS" of VGS as "es", "as", "cs" or "gs"; VDS as
# "os"/"ps"/"bs"/"ds" and VSD as "sp"/"so" -- none of which match here.
VGS_AXIS_RE = re.compile(
    r"v\s*[_]?\s*gs\b|\bv\s*[eac6]\s*[s5]\b|gate[-\s]*(?:to[-\s]*)?source\s*voltage|gate\s+voltage|g\s*-\s*s\s+voltage",
    re.IGNORECASE,
)
DRAIN_CURRENT_AXIS_RE = re.compile(
    r"\bi\s*d\b\s*[,(-]|drain[-\s]*(?:to[-\s]*)?(?:source\s*)?current|\bq\s*g\b|gate\s+charge",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Caption:
    number: str
    title: str
    bbox_pt: BBox
    source: str


@dataclass(frozen=True)
class LocatedPanel:
    pdf: str
    part: str
    page: int
    diagram: str
    title: str
    caption_bbox_pt: BBox
    frame_pt: BBox
    frame_source: str
    bbox_pt: BBox
    text_source: str
    x_axis_title: str
    identity: str = "caption"

    def to_json(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class Refusal:
    pdf: str
    page: int
    diagram: str
    title: str
    reason: str

    def to_json(self) -> dict[str, object]:
        return asdict(self)


def locate_panels(
    pdf: Path, work_dir: Path
) -> tuple[list[LocatedPanel], list[Refusal], dict[tuple[int, str], PageText]]:
    """Return owned RDS(VGS) panels, explicit refusals, and each panel's OCR words.

    The per-panel PageText carries only words the text layer does not have
    (page OCR of an image-only page, or of the panel's raster area); the
    digitizer reads the text layer itself with PyMuPDF, which keeps
    subscripted symbols such as "TC" and "ID" as one word.
    """
    located: list[LocatedPanel] = []
    refusals: list[Refusal] = []
    panel_text: dict[tuple[int, str], PageText] = {}
    try:
        pages = run_text_bbox(pdf)
    except RuntimeError:
        # An image-only PDF has no text layer at all; every page then goes
        # through the OCR path below instead of aborting the whole document.
        with pymupdf.open(pdf) as document:
            pages = [
                PageText(index + 1, float(page.rect.width), float(page.rect.height), [], "no_text_layer")
                for index, page in enumerate(document)
            ]
    with pymupdf.open(pdf) as document:
        for page in pages:
            pdf_page = document[page.page_num - 1]
            text_page = page
            captions = find_rds_vgs_captions(text_page)
            if not captions and _needs_ocr(text_page, pdf_page):
                text_page = ocr_page_text(pdf, pdf_page, page.page_num, work_dir)
                captions = find_rds_vgs_captions(text_page)
            others = _other_numbered_captions(text_page, captions) if text_page.text_source == "pdftotext" else []
            if not captions and not others:
                continue
            frames = _page_frames(pdf, page, pdf_page, work_dir)
            side = _caption_side([*captions, *_numbered_captions(text_page, captions)], frames)
            ocr_cache: list[PageText] = []

            def ocr() -> PageText:
                if not ocr_cache:
                    ocr_cache.append(ocr_page_text(pdf, pdf_page, page.page_num, work_dir))
                return ocr_cache[0]

            for caption in captions:
                if not _frame_candidates(caption, frames) and not any(s == "vector_hairline_grid" for _f, s in frames):
                    frames = frames + _hairline_grid_frames(pdf, page, frames)
                try:
                    frame, source, words, x_title, identity = _owned_frame(caption, frames, text_page, ocr, side)
                except LocateError as error:
                    refusals.append(Refusal(str(pdf), page.page_num, caption.number, caption.title, str(error)))
                    continue
                panel = LocatedPanel(
                    pdf=str(pdf),
                    part=pdf.stem,
                    page=page.page_num,
                    diagram=caption.number,
                    title=caption.title,
                    caption_bbox_pt=caption.bbox_pt,
                    frame_pt=frame,
                    frame_source=source,
                    bbox_pt=_panel_bbox(frame, source, caption, frames, text_page),
                    text_source=words.text_source,
                    x_axis_title=x_title,
                    identity=identity,
                )
                if any(o.page == panel.page and bbox_iou(o.frame_pt, panel.frame_pt) > 0.6 for o in located):
                    continue
                located.append(panel)
                panel_text[(panel.page, panel.diagram)] = words
            for caption in others:
                owned = _axis_titled_frame(caption, frames, text_page, pdf_page, side,
                                           [*captions, *_numbered_captions(text_page, captions)])
                if owned is None:
                    continue
                frame, source, x_title, y_title = owned
                if any(o.page == page.page_num and bbox_iou(o.frame_pt, frame) > 0.6 for o in located):
                    continue
                panel = LocatedPanel(
                    pdf=str(pdf), part=pdf.stem, page=page.page_num, diagram=caption.number, title=caption.title,
                    caption_bbox_pt=caption.bbox_pt, frame_pt=frame, frame_source=source,
                    bbox_pt=_panel_bbox(frame, source, caption, frames, text_page),
                    text_source=text_page.text_source, x_axis_title=x_title,
                    identity=(f"axis_titles: y {y_title!r}, x {x_title!r} name RDS(on) versus VGS; the caption "
                              f"{caption.title!r} names another chart and is overruled"),
                )
                located.append(panel)
                panel_text[(panel.page, panel.diagram)] = PageText(
                    text_page.page_num, text_page.width_pt, text_page.height_pt, [], "text_layer_only")
    return located, refusals, panel_text


def _other_numbered_captions(page: PageText, rds_captions: list[Caption]) -> list[Caption]:
    """Numbered captions that do NOT name RDS(VGS), on a page whose text names both RDS(on) and VGS.

    Diodes Inc. (DMN3023L, DMN4008LFG, DMT6009LCT) captions its RDS(on)-vs-VGS
    Figure 4 "Typical Transfer Characteristic". Such a caption is only a
    candidate: ``_axis_titled_frame`` owns its frame only when the frame's own
    axis titles say RDS(on) (y) versus VGS (x).
    """
    joined = normalize_dashes(" ".join(w.text for w in page.words))
    if not re.search(_RDS_TOKEN, joined, re.IGNORECASE) or not VGS_AXIS_RE.search(joined):
        return []
    return [c for c in _numbered_captions(page, rds_captions) if not RDS_VGS_TITLE_RE.search(c.title)]


def _numbered_captions(page: PageText, rds_captions: list[Caption]) -> list[Caption]:
    """Every numbered caption ("Figure 3 ...", "Fig.2 ...") on the page other than ``rds_captions``."""
    out: list[Caption] = []
    lines = group_words_into_lines(page.words)
    for index, line in enumerate(lines):
        for segment in _caption_segments(line):
            text = normalize_dashes(line_text(segment))
            if not (_CAPTION_START_RE.match(text) and re.match(r"(?i)^fig", text)):
                continue
            continuation = _continuation(lines, index, line_bbox(segment))
            full = normalize_dashes(f"{text} {line_text(continuation)}" if continuation else text)
            match = _NUMBERED_CAPTION_RE.match(full)
            if match is None or not match.group(2).strip():
                continue
            bbox = line_bbox(segment + continuation)
            if any(bbox_iou(bbox, c.bbox_pt) > 0.3 for c in [*rds_captions, *out]):
                continue
            out.append(Caption(match.group(1), match.group(2).strip(), bbox, page.text_source))
    return out


Y_TITLE_STRIP_PT = 60.0


def owned_y_axis_title(pdf_page, frame: BBox) -> str | None:
    """The vertical text lines in the strip left of the frame (the y-axis title), text layer only."""
    texts: list[tuple[float, str]] = []
    for block in pdf_page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            dx, _dy = line["dir"]
            if abs(dx) > 0.2:
                continue
            x0, y0, x1, y1 = line["bbox"]
            if not frame[0] - Y_TITLE_STRIP_PT <= 0.5 * (x0 + x1) <= frame[0] or y1 < frame[1] - 5 or y0 > frame[3] + 5:
                continue
            text = " ".join(span["text"].strip() for span in line["spans"] if span["text"].strip())
            if re.search(r"[A-Za-z]{2,}", text):
                texts.append((0.5 * (x0 + x1), normalize_dashes(text)))
    return " | ".join(text for _x, text in sorted(texts)) or None


def _frame_side_candidates(caption: Caption, frames, side: str | None) -> list[tuple[float, BBox, str]]:
    """_frame_candidates, restricted to frames above the caption ("below") or below it ("above")."""
    out = []
    for score, frame, source in _frame_candidates(caption, frames):
        is_above = -2.0 <= caption.bbox_pt[1] - frame[3] <= MAX_CAPTION_GAP_ABOVE_PT
        if side is None or (side == "below" and is_above) or (side == "above" and not is_above):
            out.append((score, frame, source))
    return out


def _caption_side(captions: list[Caption], frames) -> str | None:
    """Which side of its frame this page prints captions on: "below", "above", or None (undecided).

    DMN3023L prints every caption under its chart, 35 pt below; the NEXT
    row's chart starts only 24 pt under the caption, so "nearest frame"
    hands Figure 2's caption the Figure 4 chart. The page's side is the one
    under which the most captions get a frame of their own (a one-to-one
    match); a tie decides nothing.
    """
    counts = {}
    for side in ("below", "above"):
        taken: set[tuple[float, ...]] = set()
        for caption in sorted(captions, key=lambda c: c.bbox_pt[1]):
            for _score, frame, _source in _frame_side_candidates(caption, frames, side):
                if tuple(frame) not in taken:
                    taken.add(tuple(frame))
                    break
        counts[side] = len(taken)
    if counts["below"] == counts["above"]:
        return None
    return max(counts, key=counts.get)


def _x_band_text(page: PageText, frame: BBox) -> str:
    """All words in the x-title band under the frame, tick labels included."""
    band = (frame[0] - 10.0, frame[3] + 1.0, frame[2] + 10.0, frame[3] + MARGIN_BOTTOM_PT)
    words = [w for w in page.words
             if band[0] <= 0.5 * (w.x0 + w.x1) <= band[2] and band[1] <= 0.5 * (w.y0 + w.y1) <= band[3]]
    return " | ".join(normalize_dashes(line_text(line)) for line in group_words_into_lines(words))


def _axis_titled_frame(caption: Caption, frames, text_page: PageText, pdf_page, side: str | None = None,
                       captions_on_page: list[Caption] = ()):
    """(frame, source, x title, y title) when the caption-adjacent frame's own titles say RDS(on) vs VGS.

    The y title (vertical text left of the frame) must name RDS(on) and no
    drain current; the x title must pass the same gate-voltage test as every
    located panel. A transfer chart (y "I D, DRAIN CURRENT", x VGS) fails
    the first test; an RDS(on)-vs-ID chart fails the second.
    """
    # captions (with their continuation lines) are not axis titles: AON7524's
    # "Figure 3: On-Resistance vs. Drain Current and / Gate Voltage (Note E)"
    # puts "Gate Voltage" in the band under its RDS-versus-ID frame.
    boxes = [c.bbox_pt for c in captions_on_page]
    titles = PageText(text_page.page_num, text_page.width_pt, text_page.height_pt, [
        w for w in text_page.words
        if not any(b[0] - 1 <= 0.5 * (w.x0 + w.x1) <= b[2] + 1 and b[1] - 1 <= 0.5 * (w.y0 + w.y1) <= b[3] + 1 for b in boxes)
    ], text_page.text_source)
    evidenced = []
    for score, frame, source in _frame_side_candidates(caption, frames, side):
        y_title = owned_y_axis_title(pdf_page, frame)
        if y_title is None or not re.search(_RDS_TOKEN, y_title, re.IGNORECASE) or DRAIN_CURRENT_AXIS_RE.search(y_title):
            continue
        x_title = owned_x_axis_title(titles, frame)
        try:
            _require_gate_voltage_x_axis(x_title)
            # every word of the band, also those without two letters ("I D (A)")
            _require_gate_voltage_x_axis(_x_band_text(titles, frame))
        except LocateError:
            continue
        evidenced.append((score, frame, source, x_title or "", y_title))
    if not evidenced or (len(evidenced) > 1 and evidenced[1][0] - evidenced[0][0] < CAPTION_AMBIGUITY_PT):
        return None
    _score, frame, source, x_title, y_title = evidenced[0]
    return frame, source, x_title, y_title


def upright_pdf(pdf: Path, work_dir: Path) -> Path:
    """``pdf`` itself, or an upright copy when any page carries a /Rotate.

    ZVNL120A's pages are stored landscape with /Rotate 90: pdftotext reports
    the words upright, but PyMuPDF's words and vector drawings stay in the
    stored (sideways) frame, so the caption, the frames and the crop never
    agree. The copy shows every page as displayed (``show_pdf_page`` with the
    rotation undone), so all readers see one upright coordinate system. It is
    written under the run's own work directory, never /tmp; a PDF without a
    rotated page is used as is.
    """
    with pymupdf.open(pdf) as source:
        rotations = [page.rotation for page in source]
        if not any(rotations):
            return pdf
        target = work_dir / "upright" / pdf.name
        target.parent.mkdir(parents=True, exist_ok=True)
        out = pymupdf.open()
        for index, page in enumerate(source):
            rect, rotation = page.rect, page.rotation
            page.set_rotation(0)
            new = out.new_page(width=rect.width, height=rect.height)
            new.show_pdf_page(new.rect, source, index, rotate=-rotation)
        out.save(target)
        out.close()
    return target


def _owned_frame(caption, frames, text_page: PageText, ocr, side: str | None = None) -> tuple[BBox, str, PageText, str, str]:
    """The nearest caption-adjacent frame whose own x-axis title names VGS.

    Returns (frame, source, words, x title, identity). When no candidate's
    x-axis title can be read at all (text layer and OCR both empty), the
    caption itself may stand in -- see ``caption_names_both_axes``.
    """
    candidates = _frame_candidates(caption, frames)
    if not candidates:
        raise LocateError("no plot frame directly above or below the caption in its column")
    reasons: list[str] = []
    evidenced: list[tuple[float, BBox, str, PageText, str]] = []
    unreadable: list[tuple[float, BBox, str, PageText]] = []
    for score, frame, source in candidates:
        words = text_page if text_page.text_source != "pdftotext" else PageText(
            text_page.page_num, text_page.width_pt, text_page.height_pt, [], "text_layer_only"
        )
        x_title = owned_x_axis_title(text_page, frame)
        if (x_title is None or not VGS_AXIS_RE.search(x_title)) and text_page.text_source == "pdftotext":
            ocr_page = ocr()
            ocr_title = owned_x_axis_title(ocr_page, frame)
            if ocr_title is not None:
                x_title = ocr_title if x_title is None else f"{x_title} | {ocr_title}"
            words = _merge_page_text(text_page, ocr_page, frame)
        try:
            _require_gate_voltage_x_axis(x_title)
        except LocateError as error:
            reasons.append(f"frame {[round(v) for v in frame]}: {error}")
            if x_title is None:
                unreadable.append((score, frame, source, words))
            continue
        evidenced.append((score, frame, source, words, x_title or ""))
    if not evidenced:
        # HSP4048 "Fig.2 On-Resistance vs G-S Voltage": an image chart whose
        # x-axis title neither the text layer nor OCR reads. The caption names
        # BOTH axes and nothing else; it stands in for the unreadable title
        # only for the nearest frame, when no other frame competes for the
        # caption and that frame's title was unreadable (not contradicting).
        # The frame must be on the side of the caption where this page prints
        # its captions (HSP4048 captions sit under their charts; the next
        # row's Fig.4 gate-charge chart starts 13 pt under the Fig.2 caption).
        sided = _frame_side_candidates(caption, frames, side)
        near = sided[0] if sided else None
        alone = len(sided) == 1 or (len(sided) > 1 and sided[1][0] - sided[0][0] >= CAPTION_AMBIGUITY_PT)
        match = [u for u in unreadable if near is not None and u[1] == near[1]]
        if caption_names_both_axes(caption.title) and alone and match:
            _score, frame, source, words = match[0]
            return frame, source, words, "", (
                f"caption_names_both_axes: {caption.title!r}; the x-axis title below the frame is unreadable "
                "(text layer and OCR), so the caption is the only axis-identity evidence")
        raise LocateError("; ".join(reasons))
    if len(evidenced) > 1 and evidenced[1][0] - evidenced[0][0] < CAPTION_AMBIGUITY_PT:
        raise LocateError("two caption-adjacent frames both carry a VGS x axis; ownership ambiguous")
    _score, frame, source, words, x_title = evidenced[0]
    return frame, source, words, x_title, "caption"


_BOTH_AXES_CAPTION_RE = re.compile(
    r"^\s*(?:typical\s+)?(?:static\s+)?" + _RDS_TOKEN + r"\s*(?:\bvs\.?|\bversus\b|\bv\b)\s*" + _GATE_TOKEN + r"\s*\.?\s*$",
    re.IGNORECASE,
)


def caption_names_both_axes(title: str) -> bool:
    """A caption that is exactly "<RDS(on)> vs <gate voltage>": both axes named, nothing else.

    "On-Resistance Variation with Gate Voltage and Drain Current" (an
    RDS-versus-ID chart) does not qualify, nor does any caption naming a
    temperature, a current or a second quantity.
    """
    return _BOTH_AXES_CAPTION_RE.match(normalize_dashes(title)) is not None




class LocateError(RuntimeError):
    """A caption that names RDS(VGS) but whose panel cannot be owned."""


def find_rds_vgs_captions(page: PageText) -> list[Caption]:
    """Numbered captions and bare titles naming RDS(on) versus the gate voltage."""
    captions: list[Caption] = []
    lines = group_words_into_lines(page.words)
    for index, line in enumerate(lines):
        for segment in _caption_segments(line):
            text = line_text(segment)
            numbered = _NUMBERED_CAPTION_RE.match(text) if _CAPTION_START_RE.match(text) else None
            bbox = line_bbox(segment)
            candidate = text
            continuation = _continuation(lines, index, bbox)
            if continuation:
                candidate = f"{text} {line_text(continuation)}"
            candidate = normalize_dashes(candidate)
            match_text = _NUMBERED_CAPTION_RE.match(candidate) if numbered else None
            body = match_text.group(2) if match_text else candidate
            if not RDS_VGS_TITLE_RE.search(body):
                continue
            if numbered is None and not _is_bare_title(body):
                continue
            if continuation:
                bbox = line_bbox(segment + continuation)
            number = match_text.group(1) if match_text else f"t{int(bbox[1])}"
            captions.append(Caption(number, body.strip(), bbox, page.text_source))
    return _dedupe_captions(captions)


def _caption_segments(line: list[Word]) -> list[list[Word]]:
    """Split a merged two-column text line at caption starts and wide gaps.

    A gap is wide at > 28 pt, or at > 12 pt when it is also over five times
    the line's median word gap: ZVNL120A sets its two bare titles 24 pt apart
    ("On-resistance vs gate-source voltage   Normalised R DS(on) ...") with
    2 pt word spacing, and the merged 12-word line was no bare title.
    """
    starts = [
        i for i, word in enumerate(line)
        if re.match(r"(?i)^(?:figure|fig)\.?(?:\d|$)", word.text.strip())
    ]
    gaps = [line[i].x0 - line[i - 1].x1 for i in range(1, len(line))]
    typical = sorted(gaps)[len(gaps) // 2] if gaps else 0.0
    cuts = sorted(set(starts) | {
        i for i in range(1, len(line))
        if gaps[i - 1] > 28.0 or (gaps[i - 1] > 12.0 and gaps[i - 1] > 5.0 * max(typical, 0.5))
    } | {0})
    cuts.append(len(line))
    return [line[a:b] for a, b in zip(cuts, cuts[1:]) if b > a]


def _continuation(lines: list[list[Word]], index: int, bbox: BBox) -> list[Word]:
    out: list[Word] = []
    bottom = bbox[3]
    for following in lines[index + 1 : index + 3]:
        local = [w for w in following if bbox[0] - 10.0 <= 0.5 * (w.x0 + w.x1) <= bbox[2] + 10.0]
        if not local:
            continue
        top = min(w.y0 for w in local)
        if top - bottom > 6.0:
            break
        if _CAPTION_START_RE.match(local[0].text) and re.match(r"(?i)^fig", local[0].text):
            break
        out.extend(local)
        bottom = max(w.y1 for w in local)
    return out


def _is_bare_title(text: str) -> bool:
    """An unnumbered title must BE the title, not a sentence mentioning it."""
    words = text.split()
    return len(words) <= 10 and re.match(
        r"(?i)^\s*(?:typical\s+)?(?:static\s+)?(?:drain|on|r\s*ds|rdson)", text
    ) is not None


def _dedupe_captions(captions: list[Caption]) -> list[Caption]:
    out: list[Caption] = []
    for caption in captions:
        if any(bbox_iou(caption.bbox_pt, other.bbox_pt) > 0.3 for other in out):
            continue
        out.append(caption)
    return out


def _needs_ocr(page: PageText, pdf_page) -> bool:
    if len(page.words) >= MIN_TEXT_WORDS:
        return False
    area = pdf_page.rect.width * pdf_page.rect.height
    image_area = sum(
        (info["bbox"][2] - info["bbox"][0]) * (info["bbox"][3] - info["bbox"][1])
        for info in pdf_page.get_image_info()
    )
    return image_area >= 0.25 * area


def ocr_page_text(pdf: Path, pdf_page, page_num: int, work_dir: Path) -> PageText:
    """OCR one rendered page into PDF-point words (render kept in work_dir)."""
    target = work_dir / "page_ocr" / pdf.stem / f"p{page_num:02d}.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    scale = PAGE_OCR_DPI / 72.0
    pix = pdf_page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
    pix.save(target)
    tsv = tesseract_tsv(target, timeout=90.0)
    if tsv is None:
        return PageText(page_num, float(pdf_page.rect.width), float(pdf_page.rect.height), [], "tesseract_unavailable")
    return page_text_from_tesseract_tsv(
        tsv,
        page_num=page_num,
        width_pt=float(pdf_page.rect.width),
        height_pt=float(pdf_page.rect.height),
        width_px=pix.width,
        height_px=pix.height,
    )


def _merge_page_text(text_page: PageText, ocr_page: PageText, frame: BBox) -> PageText:
    """OCR words from the panel's raster area (the text layer is read separately)."""
    region = (frame[0] - MARGIN_LEFT_PT, frame[1] - MARGIN_TOP_PT, frame[2] + MARGIN_RIGHT_PT, frame[3] + MARGIN_BOTTOM_PT)
    extra = [
        w for w in ocr_page.words
        if region[0] <= 0.5 * (w.x0 + w.x1) <= region[2] and region[1] <= 0.5 * (w.y0 + w.y1) <= region[3]
    ]
    return PageText(text_page.page_num, text_page.width_pt, text_page.height_pt, extra, "text_layer+tesseract_panel")


def _page_frames(pdf: Path, page: PageText, pdf_page, work_dir: Path) -> list[tuple[BBox, str]]:
    """Vector frames, closed raster grid frames, then chart-sized embedded images.

    An embedded image is only a candidate when no detected frame lies inside
    it; its true plot frame is found later inside the crop.
    """
    frames: list[tuple[BBox, str]] = [(f, "vector") for f in page_vector_plot_frames(pdf, page.page_num, page)]
    target = work_dir / "page_renders" / pdf.stem / f"p{page.page_num:02d}.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(pdf) as document:
        pix = document[page.page_num - 1].get_pixmap(dpi=PAGE_RENDER_DPI, alpha=False)
        pix.save(target)
    for frame in detect_raster_grid_frames(target, page):
        if not any(bbox_iou(frame, existing) >= 0.6 for existing, _ in frames):
            frames.append((frame, "raster_page"))
    for info in pdf_page.get_image_info():
        box = tuple(float(v) for v in info["bbox"])
        width, height = box[2] - box[0], box[3] - box[1]
        if not (0.14 * page.width_pt <= width <= 0.60 * page.width_pt and 0.10 * page.height_pt <= height <= 0.45 * page.height_pt):
            continue
        if any(_inside(frame, box) for frame, _ in frames):
            continue
        frames.append((box, "embedded_image"))
    return frames


HAIRLINE_MIN_STROKE_PT = 0.05
GRID_EXTENT_TOL_PT = 2.5


def _hairline_grid_frames(pdf: Path, page: PageText, frames: list[tuple[BBox, str]]) -> list[tuple[BBox, str]]:
    """Line grids drawn in hairlines, tried only for a caption with no frame beside it.

    ZVNL120A draws its axes as open L-shapes (0.4 pt) and its grid at 0.08 pt,
    under the finder's 0.20 pt stroke minimum, and its rules' end points
    differ by a few tenths of a point, so the finder's exact (left, right)
    buckets never collect three of them: the page yields no frame at all.
    Here rules of any stroke >= HAIRLINE_MIN_STROKE_PT are grouped by their
    extent to within GRID_EXTENT_TOL_PT. A frame needs >= 3 horizontal rules
    sharing one x extent and >= 3 vertical rules sharing one y extent, whose
    outermost rules close the rectangle to within GRID_EXTENT_TOL_PT: the
    grid's extent is then the plot area.
    """
    with pymupdf.open(pdf) as document:
        drawings = document[page.page_num - 1].get_drawings()
    min_w, max_w = page.width_pt * 0.14, page.width_pt * 0.48
    min_h, max_h = page.height_pt * 0.08, page.height_pt * 0.40
    horizontal: list[tuple[float, float, float]] = []   # (left, right, y)
    vertical: list[tuple[float, float, float]] = []     # (top, bottom, x)
    for drawing in drawings:
        if drawing.get("type") not in {"s", "fs"} or not HAIRLINE_MIN_STROKE_PT <= float(drawing.get("width") or 0.0) <= 2.5:
            continue
        for item in drawing.get("items", []):
            if item[0] != "l":
                continue
            (x0, y0), (x1, y1) = (item[1].x, item[1].y), (item[2].x, item[2].y)
            if abs(y1 - y0) <= 0.75 and min_w <= abs(x1 - x0) <= max_w:
                horizontal.append((min(x0, x1), max(x0, x1), 0.5 * (y0 + y1)))
            elif abs(x1 - x0) <= 0.75 and min_h <= abs(y1 - y0) <= max_h:
                vertical.append((min(y0, y1), max(y0, y1), 0.5 * (x0 + x1)))

    def groups(rules):
        out: list[list[tuple[float, float, float]]] = []
        for rule in sorted(rules):
            for group in out:
                if abs(group[0][0] - rule[0]) <= GRID_EXTENT_TOL_PT and abs(group[0][1] - rule[1]) <= GRID_EXTENT_TOL_PT:
                    group.append(rule)
                    break
            else:
                out.append([rule])
        return out

    tol = GRID_EXTENT_TOL_PT
    found: list[tuple[BBox, str]] = []
    for h_all in groups(horizontal):
        for v_all in groups(vertical):
            # rules of one extent may belong to two charts (stacked, or side by
            # side): keep only those inside the other family's extent
            h = [r for r in h_all if min(q[0] for q in v_all) - tol <= r[2] <= max(q[1] for q in v_all) + tol]
            v = [r for r in v_all if min(q[0] for q in h_all) - tol <= r[2] <= max(q[1] for q in h_all) + tol]
            if len({round(r[2]) for r in h}) < 3 or len({round(r[2]) for r in v}) < 3:
                continue
            left, right = min(r[0] for r in h), max(r[1] for r in h)
            top, bottom = min(r[2] for r in h), max(r[2] for r in h)
            vtop, vbottom = min(r[0] for r in v), max(r[1] for r in v)
            xs = sorted(r[2] for r in v)
            if max(abs(vtop - top), abs(vbottom - bottom), abs(xs[0] - left), abs(xs[-1] - right)) > tol:
                continue
            box = (left, top, right, bottom)
            if not (min_h <= bottom - top <= max_h) or any(bbox_iou(box, e) >= 0.6 for e, _ in [*frames, *found]):
                continue
            found.append((box, "vector_hairline_grid"))
    return found


def _inside(inner: BBox, outer: BBox) -> bool:
    return inner[0] >= outer[0] - 2 and inner[1] >= outer[1] - 2 and inner[2] <= outer[2] + 2 and inner[3] <= outer[3] + 2


def _frame_candidates(caption: Caption, frames: list[tuple[BBox, str]]) -> list[tuple[float, BBox, str]]:
    cx = 0.5 * (caption.bbox_pt[0] + caption.bbox_pt[2])
    scored: list[tuple[float, BBox, str]] = []
    for frame, source in frames:
        if not frame[0] - 40.0 <= cx <= frame[2] + 40.0:
            continue
        above_gap = caption.bbox_pt[1] - frame[3]
        below_gap = frame[1] - caption.bbox_pt[3]
        offset = 0.15 * abs(0.5 * (frame[0] + frame[2]) - cx)
        if -2.0 <= above_gap <= MAX_CAPTION_GAP_ABOVE_PT:
            scored.append((above_gap + offset, frame, source))
        elif -2.0 <= below_gap <= MAX_CAPTION_GAP_BELOW_PT:
            scored.append((below_gap + offset, frame, source))
    return sorted(scored, key=lambda item: item[0])


def owned_x_axis_title(page: PageText, frame: BBox) -> str | None:
    """Text of the lines just below the frame that name an axis quantity.

    Caption lines are excluded: a caption such as "On-Resistance vs. G-S
    Voltage" names the gate voltage without being evidence that THIS frame's
    x axis is the gate voltage.
    """
    band = (frame[0] - 10.0, frame[3] + 1.0, frame[2] + 10.0, frame[3] + MARGIN_BOTTOM_PT)
    words = [
        w for w in page.words
        if band[0] <= 0.5 * (w.x0 + w.x1) <= band[2] and band[1] <= 0.5 * (w.y0 + w.y1) <= band[3]
    ]
    texts = [normalize_dashes(line_text(line)) for line in group_words_into_lines(words)]
    named = [
        text for text in texts
        if re.search(r"[A-Za-z]{2,}", text) and not _CAPTION_START_RE.match(text.strip())
        and not re.match(r"^\s*\d+(?:[.-]\d+)?\.\s+[A-Z]", text)
    ]
    return " | ".join(named) if named else None


def _require_gate_voltage_x_axis(x_title: str | None) -> None:
    if x_title is None:
        raise LocateError("x-axis title below the frame is unreadable; axis identity unverified")
    if DRAIN_CURRENT_AXIS_RE.search(x_title) and not VGS_AXIS_RE.search(x_title):
        raise LocateError(f"x axis is not the gate voltage: {x_title!r}")
    if not VGS_AXIS_RE.search(x_title):
        raise LocateError(f"x-axis title does not name VGS: {x_title!r}")
    if DRAIN_CURRENT_AXIS_RE.search(x_title):
        raise LocateError(f"x-axis band names both VGS and a drain current/charge: {x_title!r}")


def _panel_bbox(frame: BBox, source: str, caption: Caption, frames: list[tuple[BBox, str]], page: PageText) -> BBox:
    if source == "embedded_image":
        frame = (frame[0] + 0.6 * MARGIN_LEFT_PT, frame[1], frame[2], frame[3] - 0.5 * MARGIN_BOTTOM_PT)
    x0 = frame[0] - MARGIN_LEFT_PT
    y0 = frame[1] - MARGIN_TOP_PT
    x1 = frame[2] + MARGIN_RIGHT_PT
    y1 = frame[3] + MARGIN_BOTTOM_PT
    for other, _source in frames:
        if bbox_iou(other, frame) > 0.6:
            continue
        overlaps_y = other[1] < frame[3] and other[3] > frame[1]
        if overlaps_y and other[2] <= frame[0]:
            x0 = max(x0, other[2] + 2.0)
        if overlaps_y and other[0] >= frame[2]:
            x1 = min(x1, other[0] - 2.0)
        overlaps_x = other[0] < frame[2] and other[2] > frame[0]
        if overlaps_x and other[1] >= frame[3]:
            y1 = min(y1, other[1] - 2.0)
    if caption.bbox_pt[1] >= frame[3] - 2.0:
        y1 = min(y1, caption.bbox_pt[1] - 1.0)
    return (max(0.0, x0), max(0.0, y0), min(page.width_pt, x1), min(page.height_pt, y1))

