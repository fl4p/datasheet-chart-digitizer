"""Opt-in on-disk memo of tesseract runs, keyed on everything tesseract reads.

OCR is ~90-97 % of an RDS(on)-vs-VGS digitization (profiled 2026-09-29:
RQ3E110AJ 50 of 56 s in 640 tesseract calls on axis-label blobs), and the
test suites and the mutation harness re-OCR the same images again and again.
Tesseract is an external pure function of its input image, its argv, its
binary + libraries + traineddata and a few environment variables (with
OMP_THREAD_LIMIT=1, see ``rdson_gate_voltage.digitize_pdf``), so a result can
be reused whenever ALL of those are identical.

Off unless ``DSDIG_OCR_CACHE`` names a directory: production runs call
``subprocess.run`` exactly as before. When on:

- the key hashes the image BYTES (never its path), the argv with the image
  path and the executable replaced by those fingerprints, the environment
  variables tesseract reads, and a fingerprint of the tesseract installation
  (``--version`` output, the resolved binary, its libtesseract, every
  traineddata file);
- only a clean run is stored (return code 0, no exception): a timeout or a
  failure under load is never remembered;
- an entry is written atomically (temp file + rename) and carries its own
  key and a checksum of its payload; an entry that fails to parse or verify
  is treated as a miss, recomputed and overwritten, never trusted.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

ENV = "DSDIG_OCR_CACHE"
_ENV_KEYS = ("OMP_THREAD_LIMIT", "OMP_NUM_THREADS", "TESSDATA_PREFIX", "LANG", "LC_ALL")
_FINGERPRINT: dict[str, str] = {}
FORMAT = 1


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def tesseract_fingerprint(executable: str) -> str:
    """Hash of the tesseract installation that `executable` resolves to."""
    resolved = shutil.which(executable) or executable
    real = Path(os.path.realpath(resolved))
    if str(real) in _FINGERPRINT:
        return _FINGERPRINT[str(real)]
    parts = {"binary": _sha256_file(real)}
    version = subprocess.run([str(real), "--version"], capture_output=True, text=True, timeout=60)
    parts["version"] = version.stdout + version.stderr
    for lib in sorted((real.parent.parent / "lib").glob("libtesseract*")):
        if lib.is_file() and not lib.is_symlink():
            parts[f"lib:{lib.name}"] = _sha256_file(lib)
    langs = subprocess.run([str(real), "--list-langs"], capture_output=True, text=True, timeout=60)
    tessdata = None
    for line in (langs.stdout + langs.stderr).splitlines():
        if '"' in line and "languages" in line:
            tessdata = Path(line.split('"')[1])
    if tessdata is None or not tessdata.is_dir():
        raise RuntimeError(f"cannot locate tessdata for {real}: {langs.stdout!r} {langs.stderr!r}")
    for data in sorted(tessdata.glob("*.traineddata")):
        parts[f"data:{data.name}"] = _sha256_file(data)
    fingerprint = hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()
    _FINGERPRINT[str(real)] = fingerprint
    return fingerprint


def cache_key(argv: list[str], image: Path, cwd: str | os.PathLike | None = None) -> str:
    image_path = Path(cwd) / image if cwd is not None and not Path(image).is_absolute() else Path(image)
    rest = [("<IMAGE>" if a == str(image) else a) for a in argv[1:]]
    if "<IMAGE>" not in rest:
        raise ValueError(f"image {image} is not an argument of {argv}")
    payload = {
        "format": FORMAT,
        "tesseract": tesseract_fingerprint(argv[0]),
        "image_sha256": _sha256_file(image_path),
        "argv": rest,
        "env": {k: os.environ.get(k) for k in _ENV_KEYS},
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _entry_path(root: Path, key: str) -> Path:
    return root / key[:2] / f"{key}.json"


def _payload_sha(stdout: str, stderr: str) -> str:
    return hashlib.sha256(json.dumps([stdout, stderr]).encode()).hexdigest()


def load(root: Path, key: str) -> tuple[str, str] | None:
    """(stdout, stderr) of a verified entry, or None (absent, partial, corrupt)."""
    path = _entry_path(root, key)
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
        if entry["key"] != key or entry["sha"] != _payload_sha(entry["stdout"], entry["stderr"]):
            return None
        return entry["stdout"], entry["stderr"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def store(root: Path, key: str, stdout: str, stderr: str) -> None:
    path = _entry_path(root, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"key": key, "stdout": stdout, "stderr": stderr,
                               "sha": _payload_sha(stdout, stderr)}), encoding="utf-8")
    os.replace(tmp, path)


def run(argv: list[str], image: str | os.PathLike, **kwargs) -> subprocess.CompletedProcess:
    """``subprocess.run(argv, **kwargs)`` for a tesseract call reading ``image``.

    Memoized only when DSDIG_OCR_CACHE is set and the call captures text
    output; otherwise exactly ``subprocess.run``.
    """
    root = os.environ.get(ENV)
    captured = kwargs.get("capture_output") or (
        kwargs.get("stdout") == subprocess.PIPE and kwargs.get("stderr") == subprocess.PIPE)
    if not root or not captured or not kwargs.get("text") or "input" in kwargs:
        return subprocess.run(argv, **kwargs)
    key = cache_key(argv, Path(image), kwargs.get("cwd"))
    hit = load(Path(root), key)
    if hit is not None:
        return subprocess.CompletedProcess(argv, 0, hit[0], hit[1])
    proc = subprocess.run(argv, **kwargs)
    if proc.returncode == 0:
        store(Path(root), key, proc.stdout, proc.stderr)
    return proc
