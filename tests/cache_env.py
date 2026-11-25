"""Where the test caches live and whether they are on (shared by conftest and rds_digitize_cache).

    DSDIG_TEST_CACHE=0      no digitize cache, no OCR memo, no render memo (everything live)
    DSDIG_TEST_CACHE_DIR    cache root (default: <repo>/out/test-cache)
"""

from __future__ import annotations

import os
from pathlib import Path

OUT_ROOT = Path(__file__).resolve().parents[1] / "out"


def enabled() -> bool:
    return os.environ.get("DSDIG_TEST_CACHE", "1").strip().lower() not in {"0", "off", "false", "no"}


def cache_root() -> Path:
    return Path(os.environ.get("DSDIG_TEST_CACHE_DIR") or OUT_ROOT / "test-cache")
