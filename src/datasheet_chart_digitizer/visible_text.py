"""The text a reader of the rendered page can see.

A PDF text layer also holds text that is never painted: render mode 3
(neither filled nor stroked), zero fill alpha, or white fill on a white
background. Read as tick labels, axis titles or units, such text changes a
value with nothing on the page to show for it. The readers here return what
``page.get_text`` returns with those characters removed.

Two kinds of hidden text are kept because a reader does see them:

* unpainted or transparent text over an image is the OCR layer of a scan;
  the image shows the same label;
* white text is kept where the rendered page under it is not white (white on
  a dark fill or a dark image).

A page without hidden text returns ``page.get_text`` output unchanged.
"""

from __future__ import annotations

import collections
import unicodedata

import numpy as np
import pymupdf

_FILLED, _STROKED, _CLIP = 16, 32, 64  # FZ_STEXT_FILLED, _STROKED, _CLIP
_RAW_FLAGS = pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_IMAGES
_PAD = 0.5  # pt, word box slack when collecting its characters
_DARK = 200  # gray level below which a pixel is not background white
_DARK_SHARE = 0.25  # share of non-white pixels that makes white text visible
_INK_SHARE = 0.005  # an image hosting an OCR layer shows some ink
_NEAR_WHITE = 0xF0  # every channel at or above: white for the background test


def hidden_reason(span: dict) -> str | None:
    """Why *span* is unpainted by its own attributes, or None.

    Raises when the span carries no render information (``char_flags``):
    without it render mode 3 cannot be told apart from painted text.
    """
    if "char_flags" not in span:
        raise RuntimeError("PyMuPDF reports no char_flags: render mode 3 text cannot be detected")
    flags = span["char_flags"]
    if flags & _CLIP:
        return None  # a clip path: what is painted through it shows
    if not flags & (_FILLED | _STROKED):
        return "unpainted"
    if span.get("alpha") == 0:
        return "alpha"
    color = span.get("color", 0)
    if all((color >> s) & 0xFF >= _NEAR_WHITE for s in (16, 8, 0)) and not flags & _STROKED:
        return "white"
    return None


class PageVisibility:
    """Hidden and visible characters of one page (centres, page coordinates).

    Holds no reference to the page: methods that need it take it.
    """

    def __init__(self, page: pymupdf.Page):
        self._images: list[pymupdf.Rect] | None = None
        self._gray: np.ndarray | None = None
        hidden, visible = [], []
        for block in page.get_text("rawdict", flags=_RAW_FLAGS).get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    out = hidden if self.span_hidden(page, span) else visible
                    out.extend(((ch["bbox"][0] + ch["bbox"][2]) / 2, (ch["bbox"][1] + ch["bbox"][3]) / 2, ch["c"])
                               for ch in span.get("chars", []))
        self.hidden = _Chars(hidden)
        self.visible = _Chars(visible if hidden else [])

    def span_hidden(self, page: pymupdf.Page, span: dict) -> bool:
        """True when a reader cannot see *span* on *page*."""
        reason = hidden_reason(span)
        if reason is None:
            return False
        bbox = pymupdf.Rect(span["bbox"])
        if reason == "white":
            return not self._dark_under(page, bbox)
        return not self._over_image_ink(page, bbox)

    def _over_image_ink(self, page, bbox: pymupdf.Rect) -> bool:
        if self._images is None:  # a blank image hosts no scan
            rects = [pymupdf.Rect(i["bbox"]) for i in page.get_image_info()]
            self._images = [r for r in rects if self.dark_share(page, r) >= _INK_SHARE]
        centre = pymupdf.Point((bbox.x0 + bbox.x1) / 2, (bbox.y0 + bbox.y1) / 2)
        return any(centre in image for image in self._images)

    def _dark_under(self, page, bbox: pymupdf.Rect) -> bool:
        return self.dark_share(page, bbox) >= _DARK_SHARE

    def dark_share(self, page, bbox: pymupdf.Rect) -> float:
        """Share of rendered pixels under *bbox* darker than background white."""
        if self._gray is None:
            pix = page.get_pixmap(colorspace=pymupdf.csGRAY, alpha=False)
            self._gray = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)
        r = (bbox * page.rotation_matrix) & pymupdf.Rect(0, 0, self._gray.shape[1], self._gray.shape[0])
        cells = self._gray[int(r.y0):int(np.ceil(r.y1)), int(r.x0):int(np.ceil(r.x1))]
        return float(np.mean(cells < _DARK)) if cells.size else 0.0

    def word_hidden(self, bbox, text: str, matrix=None) -> bool:
        """True when the word at *bbox* (page coordinates, or *matrix*-mapped
        ones) holds hidden characters and its visible ones do not spell it."""
        x0, y0, x1, y1 = (float(v) for v in bbox[:4])
        box = (x0 - _PAD, y0 - _PAD, x1 + _PAD, y1 + _PAD)
        if not self.hidden.inside(box, matrix):
            return False
        seen = self.visible.inside(box, matrix)
        return not seen or _spelling(seen) != _spelling(text)


class _Chars:
    """Character centres with a vectorised box query."""

    def __init__(self, chars):
        self.xy = np.array([(x, y) for x, y, _ in chars], float).reshape(-1, 2)
        self.c = [c for _, _, c in chars]
        self._mapped: dict = {}

    def __bool__(self) -> bool:
        return bool(self.c)

    def inside(self, box, matrix=None) -> list[str]:
        xy = self.xy
        if matrix is not None and len(xy):
            key = tuple(matrix)
            if key not in self._mapped:
                a, b, c, d, e, f = key
                self._mapped[key] = np.column_stack((a * xy[:, 0] + c * xy[:, 1] + e, b * xy[:, 0] + d * xy[:, 1] + f))
            xy = self._mapped[key]
        x0, y0, x1, y1 = box
        hit = (xy[:, 0] >= x0) & (xy[:, 0] <= x1) & (xy[:, 1] >= y0) & (xy[:, 1] <= y1)
        return [self.c[i] for i in np.flatnonzero(hit)]


def _spelling(chars) -> collections.Counter:
    text = unicodedata.normalize("NFKC", "".join(chars))
    text = "".join(text.split()).replace("μ", "u").replace("µ", "u")
    for dash in "−–—‑‐":
        text = text.replace(dash, "-")
    return collections.Counter(text)


def page_visibility(page) -> PageVisibility | None:
    """:class:`PageVisibility` of a PyMuPDF page, cached on its document (keyed
    on the page's content stream xrefs: a new stream is seen, an in-place
    ``update_stream`` is not); None for other
    page objects (OCR word pages, test doubles), which hold no text layer."""
    if not isinstance(page, pymupdf.Page):
        return None
    doc = page.parent
    cache = getattr(doc, "_dsdig_visibility", None)
    if cache is None:
        cache = doc._dsdig_visibility = {}
    key = (page.number, page.rotation, tuple(page.get_contents()), doc.xref_length())
    hit = cache.get(key)
    if hit is None:
        hit = cache[key] = PageVisibility(page)
    return hit


def words(page, *args, **kwargs) -> list:
    """``page.get_text("words", ...)`` without words a reader cannot see."""
    out = page.get_text("words", *args, **kwargs)
    vis = page_visibility(page)
    if vis is None or not vis.hidden:
        return out
    return [w for w in out if not vis.word_hidden(w[:4], str(w[4]))]


def payload(page, kind: str = "dict", *args, **kwargs) -> dict:
    """``page.get_text(kind, ...)`` (dict, rawdict) without hidden spans."""
    out = page.get_text(kind, *args, **kwargs)
    vis = page_visibility(page)
    if vis is None or not vis.hidden:
        return out
    for block in out.get("blocks", []):
        if "lines" not in block:
            continue
        for line in block["lines"]:
            line["spans"] = [s for s in line.get("spans", []) if not vis.span_hidden(page, s)]
        block["lines"] = [line for line in block["lines"] if line["spans"]]
    return out


def text(page, *args, **kwargs) -> str:
    """``page.get_text("text", ...)`` without hidden spans (one line per
    text line on a page that has hidden text)."""
    vis = page_visibility(page)
    if vis is None or not vis.hidden:
        return page.get_text("text", *args, **kwargs)
    if args:
        kwargs["clip"] = args[0]
    kwargs.setdefault("flags", pymupdf.TEXTFLAGS_TEXT)
    lines = []
    for block in payload(page, "dict", **kwargs).get("blocks", []):
        for line in block.get("lines", []):
            lines.append("".join(s["text"] for s in line["spans"]) + "\n")
    return "".join(lines)


def poppler_matrix(page: pymupdf.Page) -> pymupdf.Matrix:
    """Map PyMuPDF text coordinates (unrotated, CropBox origin) to those of
    ``pdftotext -bbox`` (rotated, MediaBox origin)."""
    cb, mb = page.cropbox, page.mediabox
    w, h = mb.width, mb.height
    rot = {
        0: (1, 0, 0, 1, 0, 0), 90: (0, 1, -1, 0, h, 0),
        180: (-1, 0, 0, -1, w, h), 270: (0, -1, 1, 0, 0, w),
    }[page.rotation % 360]
    # cropbox y already counts from the MediaBox top; x from the PDF origin
    return pymupdf.Matrix(1, 0, 0, 1, cb.x0 - mb.x0, cb.y0) * pymupdf.Matrix(*rot)


def drop_hidden_words(pdf_page: pymupdf.Page, page_words: list) -> list:
    """*page_words* of ``pdftotext -bbox`` (objects with x0..y1 and text)
    minus the hidden ones."""
    vis = page_visibility(pdf_page)
    if vis is None or not vis.hidden:
        return page_words
    matrix = poppler_matrix(pdf_page)
    return [w for w in page_words if not vis.word_hidden((w.x0, w.y0, w.x1, w.y1), w.text, matrix)]


def get(page, option: str = "text", *args, **kwargs):
    """``page.get_text(option, ...)`` through the reader for *option*."""
    if option == "words":
        return words(page, *args, **kwargs)
    if option in ("dict", "rawdict"):
        return payload(page, option, *args, **kwargs)
    if option == "text":
        return text(page, *args, **kwargs)
    return page.get_text(option, *args, **kwargs)
