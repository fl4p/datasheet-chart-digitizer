"""Raster renderings (from the final PDF, via MuPDF) and seeded, recorded degradations.

Pixel convention: continuous coordinates with the origin at the top-left corner of pixel
(0, 0); a PDF point ``p`` maps to ``p * dpi / 72 - origin`` where ``origin`` is the clip
pixmap's integer offset in the full-page pixel grid. ``plot_box_px`` uses this convention.

Degradations are simple numpy/OpenCV equivalents of augraphy's scan effects (augraphy is
MIT-licensed but pulls numba/scikit-image/scikit-learn and does not expose its geometric
transform). Geometric ops record their exact 2x3 affine (clean px -> degraded px).
"""
from __future__ import annotations

import io

import cv2
import fitz
import numpy as np
from PIL import Image


def render_clip(pdf_path, page_no, clip_pt, dpi):
    """Render a PDF clip rect (pt, top-left origin). Returns (rgb uint8, origin_px (x, y), scale)."""
    doc = fitz.open(pdf_path)
    page = doc[page_no]
    s = dpi / 72.0
    rect = fitz.Rect(*clip_pt) & page.rect
    pix = page.get_pixmap(matrix=fitz.Matrix(s, s), clip=rect, alpha=False)
    img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3].copy()
    origin = (pix.x, pix.y)
    doc.close()
    return img, origin, s


def pt_to_px(box_pt, origin, s):
    x0, y0, x1, y1 = box_pt
    return [x0 * s - origin[0], y0 * s - origin[1], x1 * s - origin[0], y1 * s - origin[1]]


# ----------------------------------------------------------------------------- degradations
def sample_degradation(rng, tier: str, dpi: int = 150) -> list[dict]:
    """A list of ops (applied in order). Heavier and more numerous in higher tiers."""
    heavy = tier == "adversarial"
    ops = []
    if rng.random() < 0.6:
        ops.append({"op": "blur", "sigma": round(float(rng.uniform(0.3, 1.1 if heavy else 0.8)), 2)})
    if rng.random() < 0.35 and dpi >= 96:
        ops.append({"op": "resample", "factor": round(float(rng.uniform(0.5, 0.8)), 2)})
    if rng.random() < 0.5:
        ops.append({"op": "low_contrast", "lo": int(rng.integers(20, 70)), "hi": int(rng.integers(200, 245)),
                    "gamma": round(float(rng.uniform(0.8, 1.3)), 2)})
    if rng.random() < 0.35:
        ops.append({"op": "bleed", "k": 2 if rng.random() < 0.7 else 3})
    if rng.random() < (0.5 if heavy else 0.25):
        ops.append({"op": "rotate", "deg": round(float(rng.uniform(-0.8, 0.8) if heavy else rng.uniform(-0.3, 0.3)), 3),
                    "shear": round(float(rng.uniform(-0.004, 0.004)), 4)})
    if rng.random() < 0.6:
        ops.append({"op": "noise", "sigma": round(float(rng.uniform(3, 14 if heavy else 8)), 1),
                    "speckle": round(float(rng.uniform(0, 0.004 if heavy else 0.001)), 4)})
    ops.append({"op": "jpeg", "q": int(rng.integers(30 if heavy else 50, 85))})
    if len(ops) == 1 and rng.random() < 0.5:
        ops.insert(0, {"op": "blur", "sigma": 0.5})
    return ops


def apply_degradation(img: np.ndarray, ops: list[dict], seed: int):
    """Apply ops; returns (img, affine 2x3 mapping clean px -> output px)."""
    rng = np.random.default_rng(seed)
    A = np.array([[1.0, 0, 0], [0, 1.0, 0]])
    out = img.astype(np.float32)
    for op in ops:
        k = op["op"]
        if k == "blur":
            out = cv2.GaussianBlur(out, (0, 0), op["sigma"])
        elif k == "resample":
            h, w = out.shape[:2]
            small = cv2.resize(out, (max(8, int(w * op["factor"])), max(8, int(h * op["factor"]))),
                               interpolation=cv2.INTER_AREA)
            out = cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)
        elif k == "low_contrast":
            x = np.clip(out / 255.0, 0, 1) ** op["gamma"]
            out = op["lo"] + x * (op["hi"] - op["lo"])
        elif k == "bleed":
            ker = np.ones((op["k"], op["k"]), np.uint8)
            out = 0.5 * out + 0.5 * cv2.erode(out, ker)
        elif k == "rotate":
            h, w = out.shape[:2]
            M = cv2.getRotationMatrix2D((w / 2, h / 2), op["deg"], 1.0)
            M[0, 1] += op["shear"]
            out = cv2.warpAffine(out, M, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
            A = _compose(M, A)
        elif k == "noise":
            out = out + rng.normal(0, op["sigma"], out.shape[:2])[..., None]
            if op["speckle"]:
                m = rng.random(out.shape[:2]) < op["speckle"]
                out[m] = rng.choice([0.0, 255.0], int(m.sum()))[:, None]
        elif k == "jpeg":
            buf = io.BytesIO()
            Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)).save(buf, "JPEG", quality=op["q"])
            out = np.asarray(Image.open(io.BytesIO(buf.getvalue())).convert("RGB")).astype(np.float32)
    return np.clip(out + 0.5, 0, 255).astype(np.uint8), A


def _compose(M, A):
    """Affine M applied after A (both 2x3). cv2 maps pixel centres; the half-pixel shift is
    folded in so the result maps continuous (corner-origin) coordinates."""
    T = np.array([[1, 0, -0.5], [0, 1, -0.5], [0, 0, 1.0]])
    Ti = np.array([[1, 0, 0.5], [0, 1, 0.5], [0, 0, 1.0]])
    M3 = Ti @ np.vstack([M, [0, 0, 1]]) @ T
    return (M3 @ np.vstack([A, [0, 0, 1]]))[:2]


def apply_affine(A, pts):
    pts = np.asarray(pts, float)
    return pts @ np.asarray(A)[:, :2].T + np.asarray(A)[:, 2]
