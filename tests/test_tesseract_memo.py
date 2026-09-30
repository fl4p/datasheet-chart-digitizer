"""The opt-in tesseract memo (DSDIG_OCR_CACHE): keyed on every input, never trusts a bad entry.

Scratch output goes under the repo's gitignored ``out/`` -- never /tmp.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from datasheet_chart_digitizer import tesseract_memo

OUT_ROOT = Path(__file__).resolve().parents[1] / "out"
HAVE_TESSERACT = shutil.which("tesseract") is not None


def _digit_png(path: Path, text: str = "42") -> Path:
    image = np.full((80, 160), 255, np.uint8)
    cv2.putText(image, text, (20, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.6, 0, 3)
    cv2.imwrite(str(path), image)
    return path


@unittest.skipUnless(HAVE_TESSERACT, "needs tesseract")
class TesseractMemoTests(unittest.TestCase):
    def setUp(self):
        OUT_ROOT.mkdir(exist_ok=True)
        self._tmp = tempfile.TemporaryDirectory(prefix="ocr-memo-", dir=OUT_ROOT)
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "cache"
        self.png = _digit_png(self.tmp / "digits.png")
        self.argv = ["tesseract", str(self.png), "stdout", "--psm", "7"]
        env = patch.dict(os.environ, {tesseract_memo.ENV: str(self.root), "OMP_THREAD_LIMIT": "1"})
        env.start()
        self.addCleanup(env.stop)
        tesseract_memo.tesseract_fingerprint("tesseract")  # before any test patches subprocess.run

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self):
        return tesseract_memo.run(self.argv, self.png, capture_output=True, text=True, timeout=60)

    def test_hit_equals_the_real_run_and_does_not_run_tesseract(self):
        real = subprocess.run(self.argv, capture_output=True, text=True, timeout=60)
        first = self._run()
        self.assertEqual((first.returncode, first.stdout), (real.returncode, real.stdout))
        with patch.object(subprocess, "run", side_effect=AssertionError("tesseract ran on a hit")):
            second = tesseract_memo.run(self.argv, self.png, capture_output=True, text=True, timeout=60)
        self.assertEqual((second.returncode, second.stdout, second.stderr), (0, first.stdout, first.stderr))

    def test_key_follows_image_bytes_not_path_and_every_argument(self):
        key = tesseract_memo.cache_key(self.argv, self.png)
        copy = self.tmp / "renamed.png"
        shutil.copyfile(self.png, copy)
        self.assertEqual(tesseract_memo.cache_key(["tesseract", str(copy), "stdout", "--psm", "7"], copy), key)
        data = bytearray(self.png.read_bytes())
        data[-20] ^= 0x01
        (self.tmp / "flipped.png").write_bytes(bytes(data))
        flipped = self.tmp / "flipped.png"
        self.assertNotEqual(tesseract_memo.cache_key(["tesseract", str(flipped), "stdout", "--psm", "7"], flipped), key)
        self.assertNotEqual(tesseract_memo.cache_key(self.argv[:-1] + ["8"], self.png), key)
        with patch.dict(os.environ, {"OMP_THREAD_LIMIT": "4"}):
            self.assertNotEqual(tesseract_memo.cache_key(self.argv, self.png), key)

    def test_corrupt_or_partial_entries_are_recomputed_never_trusted(self):
        good = self._run()
        key = tesseract_memo.cache_key(self.argv, self.png)
        path = tesseract_memo._entry_path(self.root, key)
        original = path.read_text()
        for broken in (original[: len(original) // 2],                        # partial write
                       original.replace(good.stdout.strip() or "42", "99"),   # payload altered
                       ""):
            path.write_text(broken)
            self.assertIsNone(tesseract_memo.load(self.root, key))
            calls = []
            real_run = subprocess.run

            def counting(*a, **k):
                calls.append(a)
                return real_run(*a, **k)

            with patch.object(subprocess, "run", counting):
                again = self._run()
            self.assertEqual(len(calls), 1)
            self.assertEqual(again.stdout, good.stdout)
            self.assertIsNotNone(tesseract_memo.load(self.root, key))

    def test_a_failed_run_is_not_stored(self):
        failed = subprocess.CompletedProcess(self.argv, 1, "", "boom")
        with patch.object(subprocess, "run", return_value=failed):
            self.assertEqual(self._run().returncode, 1)
        self.assertIsNone(tesseract_memo.load(self.root, tesseract_memo.cache_key(self.argv, self.png)))

    def test_a_call_that_cannot_be_keyed_passes_through_unchanged(self):
        # 2026-09-30, merge with main: rds_digitize_cache (imported by another
        # test in the same process) had turned the memo on, and find_charts'
        # mocked-subprocess test lost its return value to a hash of a missing file
        missing = self.tmp / "no-such-page.png"
        argv = ["/usr/bin/tesseract", str(missing), "stdout", "--psm", "11", "tsv"]
        fake = types.SimpleNamespace(stdout="tsv")          # not a CompletedProcess
        with patch.object(subprocess, "run", return_value=fake) as run:
            self.assertIs(tesseract_memo.run(argv, missing, text=True, stdout=subprocess.PIPE,
                                             stderr=subprocess.PIPE, timeout=7), fake)
        run.assert_called_once_with(argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=7)
        self.assertFalse(self.root.exists())

    def test_an_unidentifiable_installation_is_not_fingerprinted(self):
        fake = types.SimpleNamespace(stdout="tsv")
        with patch.dict(tesseract_memo._FINGERPRINT, clear=True), \
                patch.object(subprocess, "run", return_value=fake) as run:
            self.assertIs(self._run(), fake)                    # passed through
            self.assertEqual(tesseract_memo._FINGERPRINT, {})   # nothing remembered
        self.assertEqual(run.call_args_list[-1].args[0], self.argv)
        self.assertFalse(self.root.exists())

    def test_off_without_the_environment_variable(self):
        with patch.dict(os.environ, {tesseract_memo.ENV: ""}), \
                patch.object(subprocess, "run", return_value="passthrough") as run:
            self.assertEqual(self._run(), "passthrough")
        run.assert_called_once_with(self.argv, capture_output=True, text=True, timeout=60)
        self.assertFalse(self.root.exists())


if __name__ == "__main__":
    unittest.main()
