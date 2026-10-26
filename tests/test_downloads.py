from __future__ import annotations

import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import numpy as np

from core import downloads
from core.face_models import _similarity


class DownloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="clipivo-downloads-")
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _source(self, name: str, data: bytes) -> tuple[str, str]:
        path = self.root / "source" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path.as_uri(), hashlib.sha256(data).hexdigest()

    def test_a_file_is_kept_only_when_its_checksum_matches(self) -> None:
        url, digest = self._source("model.onnx", b"model bytes")
        target = self.root / "out" / "model.onnx"
        target.parent.mkdir()
        downloads._fetch(url, target, 11, digest, 0, 11, None, None, "Model")
        self.assertEqual(target.read_bytes(), b"model bytes")
        bad = self.root / "out" / "bad.onnx"
        with self.assertRaises(downloads.DownloadError):
            downloads._fetch(url, bad, 11, "0" * 64, 0, 11, None, None, "Model")
        self.assertFalse(bad.exists())
        self.assertFalse(bad.with_name("bad.onnx.part").exists())

    def test_a_cancelled_download_leaves_nothing_behind(self) -> None:
        url, digest = self._source("big.bin", b"x" * 3_000_000)
        target = self.root / "big.bin"
        with self.assertRaises(downloads.DownloadError):
            downloads._fetch(url, target, 3_000_000, digest, 0, 3_000_000, None, lambda: True, "Big")
        self.assertEqual(list(self.root.glob("big.bin*")), [])

    def test_existing_face_models_are_found_instead_of_downloaded(self) -> None:
        folder = self.root / "shared" / "buffalo_l"
        folder.mkdir(parents=True)
        fake = (
            downloads.RemoteFile("det_10g.onnx", 3, "", ()),
            downloads.RemoteFile("w600k_r50.onnx", 4, "", ()),
        )
        (folder / "det_10g.onnx").write_bytes(b"abc")
        (folder / "w600k_r50.onnx").write_bytes(b"ab")  # incomplete
        with patch.object(downloads, "FACE_MODEL_FILES", fake), patch.object(downloads, "_face_model_candidates", return_value=[folder]):
            self.assertIsNone(downloads.find_face_models())
            (folder / "w600k_r50.onnx").write_bytes(b"abcd")
            self.assertEqual(downloads.find_face_models(), folder)

    def test_ffmpeg_comes_out_of_its_archive(self) -> None:
        archive = self.root / "source" / "ff.zip"
        archive.parent.mkdir(parents=True)
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("build/bin/ffmpeg.exe", b"ffmpeg")
            bundle.writestr("build/bin/ffprobe.exe", b"ffprobe")
        data = archive.read_bytes()
        package = downloads.ToolPackage(
            archive.as_uri(), len(data), hashlib.sha256(data).hexdigest(),
            {"build/bin/ffmpeg.exe": "ffmpeg.exe", "build/bin/ffprobe.exe": "ffprobe.exe"},
        )
        with patch.object(downloads, "FFMPEG_PACKAGES", (package,)), patch.object(downloads, "tools_dir", return_value=self.root / "tools"):
            downloads.download_ffmpeg()
        self.assertEqual((self.root / "tools" / "ffprobe.exe").read_bytes(), b"ffprobe")
        self.assertEqual(sorted(path.name for path in (self.root / "tools").iterdir()), ["ffmpeg.exe", "ffprobe.exe"])

    def test_version_pins_for_the_gpu_download(self) -> None:
        self.assertTrue(downloads._compatible("13.4.92", "~=13.0"))
        self.assertFalse(downloads._compatible("12.9.1", "~=13.0"))
        self.assertFalse(downloads._compatible("14.0.0", "~=13.0"))
        self.assertTrue(downloads._compatible("9.27.0.42", "~=9.0"))
        self.assertTrue(downloads._compatible("1.2.3", ""))
        info = {"requires_dist": [
            "numpy>=1.21.6",
            'nvidia-cuda-runtime~=13.0; extra == "cuda"',
            'nvidia-cudnn-cu13~=9.0; extra == "cudnn"',
            'ml-dtypes; extra == "quantization"',
        ]}
        self.assertEqual(
            downloads._requirements(info, ("cuda", "cudnn")),
            [("nvidia-cuda-runtime", "~=13.0"), ("nvidia-cudnn-cu13", "~=9.0")],
        )
        self.assertEqual(downloads.pending_major([("nvidia-cuda-runtime", "~=13.0")]), "~=13.0")


class AlignmentTests(unittest.TestCase):
    def test_similarity_recovers_a_known_turn_scale_and_shift(self) -> None:
        source = np.array([[10, 20], [40, 22], [25, 35], [14, 50], [36, 49]], dtype=np.float32)
        angle, scale, shift = np.deg2rad(12), 1.7, np.array([5.0, -3.0])
        rotation = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
        target = (scale * (rotation @ source.T)).T + shift
        matrix = _similarity(source, target)
        np.testing.assert_allclose(matrix[:, :2], scale * rotation, atol=1e-6)
        np.testing.assert_allclose(matrix[:, 2], shift, atol=1e-5)


if __name__ == "__main__":
    unittest.main()
