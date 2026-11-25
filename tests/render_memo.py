"""Test-only on-disk memo of ``pdftoppm`` page renders, keyed on everything the render reads.

The finder and the Poppler OCR paths render the same pages again and again
(profiled 2026-11-24 on vup: 1385 pdftoppm runs in one warm full-suite run,
~0.25-0.55 s each; region_ocr re-renders the whole page for every crop).
``install()`` wraps ``subprocess.run`` so that a pdftoppm call of the form
the package makes is served from ``DSDIG_RENDER_CACHE`` when every input is
identical; any other call passes straight through. A test that patches
``subprocess.run`` itself sits on top of the wrapper and sees no change.

Key: the PDF's SHA-256 (never its path); the argv with the executable, the
PDF and the output prefix replaced by placeholders; a fingerprint of the
Poppler installation (``pdftoppm -v``, the resolved binary, every
``libpoppler*`` beside it, the ``fc-list`` font set and the fontconfig
environment, since unembedded fonts are substituted from it). Only a clean
run (return code 0, at least one PNG) is stored; an entry carries its key
and the SHA-256 of every image, is written atomically, and is a miss (then
recomputed and overwritten) if it does not parse or verify.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

ENV = "DSDIG_RENDER_CACHE"
FORMAT = 1
_ENV_KEYS = ("FONTCONFIG_FILE", "FONTCONFIG_PATH", "LANG", "LC_ALL")
_FINGERPRINT: dict[str, str] = {}
_REAL_RUN = subprocess.run
STATS = {"hit": 0, "stored": 0, "passthrough": 0}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def poppler_fingerprint(executable: str) -> str:
    resolved = shutil.which(executable) or executable
    real = Path(os.path.realpath(resolved))
    if str(real) in _FINGERPRINT:
        return _FINGERPRINT[str(real)]
    parts = {"binary": _sha256_file(real)}
    version = _REAL_RUN([str(real), "-v"], capture_output=True, text=True, timeout=60)
    parts["version"] = version.stdout + version.stderr
    if "pdftoppm version" not in parts["version"]:
        raise RuntimeError(f"{real} -v did not identify pdftoppm: {parts['version'][:200]!r}")
    for lib in sorted((real.parent.parent / "lib").glob("libpoppler*")):
        if lib.is_file() and not lib.is_symlink():
            parts[f"lib:{lib.name}"] = _sha256_file(lib)
    if not any(k.startswith("lib:libpoppler.") for k in parts):
        raise RuntimeError(f"cannot locate libpoppler next to {real}")
    fc_list = shutil.which("fc-list", path=str(real.parent)) or shutil.which("fc-list")
    if fc_list is None:
        raise RuntimeError("fc-list not found: the font set cannot be fingerprinted")
    fonts = _REAL_RUN([fc_list, "--format", "%{file}|%{family}|%{style}\n"], capture_output=True,
                      text=True, timeout=60)
    if fonts.returncode != 0:
        raise RuntimeError("fc-list failed")
    parts["fonts"] = hashlib.sha256("\n".join(sorted(fonts.stdout.splitlines())).encode()).hexdigest()
    fingerprint = hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()
    _FINGERPRINT[str(real)] = fingerprint
    return fingerprint


def _parse(argv) -> tuple[Path, Path] | None:
    """(pdf, output prefix) of a ``pdftoppm -r D -png [-singlefile] -f N -l N PDF PREFIX`` call."""
    if not isinstance(argv, (list, tuple)) or len(argv) < 4:
        return None
    argv = [os.fspath(a) for a in argv]
    if os.path.basename(argv[0]) != "pdftoppm" or "-png" not in argv:
        return None
    flags = argv[1:-2]
    allowed_value = {"-r", "-f", "-l"}
    i = 0
    while i < len(flags):
        if flags[i] in allowed_value and i + 1 < len(flags):
            i += 2
        elif flags[i] in {"-png", "-singlefile"}:
            i += 1
        else:
            return None   # any other option: not a form this memo knows, pass through
    return Path(argv[-2]), Path(argv[-1])


def cache_key(argv, pdf: Path, prefix: Path) -> str:
    rest = [os.fspath(a) for a in argv[1:-2]]
    payload = {
        "format": FORMAT,
        "poppler": poppler_fingerprint(os.fspath(argv[0])),
        "pdf_sha256": _sha256_file(pdf),
        "argv": rest + ["<PDF>", "<OUT>"],
        "env": {k: os.environ.get(k) for k in _ENV_KEYS},
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _entry_dir(root: Path, key: str) -> Path:
    return root / key[:2] / key


def _b(value) -> bytes:
    return value.encode("utf-8", "surrogateescape") if isinstance(value, str) else (value or b"")


def load(root: Path, key: str) -> tuple[dict[str, bytes], bytes, bytes] | None:
    """({suffix after the prefix: PNG bytes}, stdout, stderr) of a verified entry, or None."""
    entry = _entry_dir(root, key)
    try:
        meta = json.loads((entry / "meta.json").read_text())
        if meta["key"] != key or not meta["files"]:
            return None
        out = {}
        for i, (suffix, sha) in enumerate(sorted(meta["files"].items())):
            data = (entry / f"{i}.png").read_bytes()
            if hashlib.sha256(data).hexdigest() != sha:
                return None
            out[suffix] = data
        stdout, stderr = (bytes.fromhex(meta[k]) for k in ("stdout", "stderr"))
        if meta["streams_sha"] != hashlib.sha256(stdout + b"\0" + stderr).hexdigest():
            return None
        return out, stdout, stderr
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def store(root: Path, key: str, files: dict[str, bytes], stdout: bytes, stderr: bytes) -> None:
    entry = _entry_dir(root, key)
    tmp = entry.with_name(f".{key}.{os.getpid()}.tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    meta = {}
    for i, (suffix, data) in enumerate(sorted(files.items())):
        (tmp / f"{i}.png").write_bytes(data)
        meta[suffix] = hashlib.sha256(data).hexdigest()
    (tmp / "meta.json").write_text(json.dumps({
        "key": key, "files": meta, "stdout": stdout.hex(), "stderr": stderr.hex(),
        "streams_sha": hashlib.sha256(stdout + b"\0" + stderr).hexdigest()}))
    shutil.rmtree(entry, ignore_errors=True)
    try:
        os.replace(tmp, entry)
    except OSError:        # another worker won the race with the same content
        shutil.rmtree(tmp, ignore_errors=True)


def _outputs(prefix: Path) -> dict[str, Path]:
    return {p.name[len(prefix.name):]: p for p in prefix.parent.glob(f"{prefix.name}*.png")}


def run(argv, *args, **kwargs):
    root = os.environ.get(ENV)
    parsed = _parse(argv) if root and not args and "input" not in kwargs else None
    if parsed is None:
        return _REAL_RUN(argv, *args, **kwargs)
    pdf, prefix = parsed
    try:
        key = cache_key(argv, pdf, prefix)
    except Exception:  # noqa: BLE001 - no key, no memo
        STATS["passthrough"] += 1
        return _REAL_RUN(argv, *args, **kwargs)
    before = set(_outputs(prefix))
    hit = load(Path(root), key)
    if hit is not None and not before & set(hit[0]):
        files, stdout, stderr = hit
        for suffix, data in files.items():
            Path(f"{prefix}{suffix}").write_bytes(data)
        STATS["hit"] += 1
        text = bool(kwargs.get("text") or kwargs.get("universal_newlines") or kwargs.get("encoding"))

        def stream(data: bytes, name: str):
            if not (kwargs.get("capture_output") or kwargs.get(name) == subprocess.PIPE):
                return None
            return data.decode(kwargs.get("encoding") or "utf-8", kwargs.get("errors") or "strict") if text else data
        return subprocess.CompletedProcess(argv, 0, stream(stdout, "stdout"), stream(stderr, "stderr"))
    proc = _REAL_RUN(argv, *args, **kwargs)
    produced = {s: p for s, p in _outputs(prefix).items() if s not in before}
    if isinstance(proc, subprocess.CompletedProcess) and proc.returncode == 0 and produced:
        # an uncaptured stream went to the terminal and is not replayed on a hit
        store(Path(root), key, {s: p.read_bytes() for s, p in produced.items()},
              _b(proc.stdout), _b(proc.stderr))
        STATS["stored"] += 1
    return proc


def install(root: Path) -> None:
    """Default DSDIG_RENDER_CACHE to ``root`` and route ``subprocess.run`` through :func:`run`."""
    os.environ.setdefault(ENV, str(root))
    if subprocess.run is not run:
        subprocess.run = run
