"""Files Clipivo fetches on first use instead of carrying in its installer.

- The face models (InsightFace buffalo_l: SCRFD detector and ArcFace recogniser), 191 MB.
- ffmpeg and ffprobe on Windows and macOS, where the system has none. Linux packages depend
  on the distribution's ffmpeg instead.
- On Linux, optionally, NVIDIA GPU acceleration: the CUDA build of ONNX Runtime and the CUDA
  libraries it needs, from PyPI, about 1.5 GB. Windows (DirectML) and macOS (CoreML) builds
  use the GPU without it.

Every file is checked against a published SHA-256 before it is used, and copies that already
exist on the machine (a source checkout, insightface's own cache, another Long Weekend Labs
app) are used instead of downloading again.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import sys
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

ProgressFn = Callable[[int, int, str], None]
CancelledFn = Callable[[], bool]

APP_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))


class DownloadError(RuntimeError):
    pass


def data_dir() -> Path:
    """Clipivo's own folder for downloaded files, per platform convention."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "Clipivo"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Clipivo"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "clipivo"


@dataclass(frozen=True)
class RemoteFile:
    name: str
    size: int
    sha256: str
    urls: tuple[str, ...]


# Face models ---------------------------------------------------------------------------

FACE_MODEL_FILES = (
    RemoteFile(
        "det_10g.onnx", 16_923_827, "5838f7fe053675b1c7a08b633df49e7af5495cee0493c7dcf6697200b85b5b91",
        ("https://huggingface.co/public-data/insightface/resolve/main/models/buffalo_l/det_10g.onnx",),
    ),
    RemoteFile(
        "w600k_r50.onnx", 174_383_860, "4c06341c33c2ca1f86781dab0e829f88ad5b64be9fba56e56bc9ebdefc619e43",
        ("https://huggingface.co/public-data/insightface/resolve/main/models/buffalo_l/w600k_r50.onnx",),
    ),
)
# InsightFace's own release, used when the per-file mirror cannot be reached.
FACE_MODEL_ARCHIVE = "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip"
FACE_MODEL_NOTICE = (
    "The face models are InsightFace's buffalo_l pack, provided by InsightFace for "
    "non-commercial research use. Clipivo downloads them from InsightFace's published files."
)


def face_model_dir() -> Path:
    return data_dir() / "models" / "buffalo_l"


def _face_model_candidates() -> list[Path]:
    """Folders where the pack may already be, read-only, before downloading another copy."""
    candidates = [face_model_dir(), APP_DIR / "models" / "buffalo_l", Path.home() / ".insightface" / "models" / "buffalo_l"]
    shared = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    candidates.append(shared / "long-weekend-labs" / "models" / "buffalo_l")
    try:
        # A source checkout next to other Long Weekend Labs projects (Castivo uses the same pack).
        candidates.extend(sorted(APP_DIR.parent.glob("*/models/buffalo_l")))
    except OSError:
        pass
    return candidates


def find_face_models() -> Path | None:
    """The folder holding both model files, complete, or None."""
    for folder in _face_model_candidates():
        if all((folder / item.name).is_file() and (folder / item.name).stat().st_size == item.size for item in FACE_MODEL_FILES):
            return folder
    return None


def face_models_download_size() -> int:
    return sum(item.size for item in FACE_MODEL_FILES)


def download_face_models(progress: ProgressFn | None = None, cancelled: CancelledFn | None = None) -> Path:
    existing = find_face_models()
    if existing:
        return existing
    target = face_model_dir()
    target.mkdir(parents=True, exist_ok=True)
    total = face_models_download_size()
    done = 0
    try:
        for item in FACE_MODEL_FILES:
            _fetch(item.urls[0], target / item.name, item.size, item.sha256, done, total, progress, cancelled, "Face models")
            done += item.size
    except DownloadError as first_error:
        if cancelled and cancelled():
            raise
        try:
            _face_models_from_archive(target, progress, cancelled)
        except DownloadError:
            raise first_error
    return target


def _face_models_from_archive(target: Path, progress: ProgressFn | None, cancelled: CancelledFn | None) -> None:
    archive = target / "buffalo_l.zip"
    try:
        _fetch(FACE_MODEL_ARCHIVE, archive, 288_621_354, "", 0, 288_621_354, progress, cancelled, "Face models")
        with zipfile.ZipFile(archive) as bundle:
            for item in FACE_MODEL_FILES:
                member = next((name for name in bundle.namelist() if name.endswith(item.name)), None)
                if member is None:
                    raise DownloadError(f"{item.name} is missing from InsightFace's download.")
                partial = target / f"{item.name}.part"
                with bundle.open(member) as source, open(partial, "wb") as handle:
                    shutil.copyfileobj(source, handle, 1 << 20)
                _verify(partial, item.size, item.sha256)
                partial.replace(target / item.name)
    finally:
        archive.unlink(missing_ok=True)


# ffmpeg -------------------------------------------------------------------------------

@dataclass(frozen=True)
class ToolPackage:
    url: str
    size: int
    sha256: str
    members: dict[str, str] = field(default_factory=dict)  # archive member -> installed name


if sys.platform == "win32":
    FFMPEG_PACKAGES = (
        ToolPackage(
            "https://github.com/GyanD/codexffmpeg/releases/download/7.1.1/ffmpeg-7.1.1-essentials_build.zip",
            92_234_348, "04861d3339c5ebe38b56c19a15cf2c0cc97f5de4fa8910e4d47e5e6404e4a2d4",
            {"ffmpeg-7.1.1-essentials_build/bin/ffmpeg.exe": "ffmpeg.exe",
             "ffmpeg-7.1.1-essentials_build/bin/ffprobe.exe": "ffprobe.exe"},
        ),
    )
elif sys.platform == "darwin":
    _RIEDL = "https://ffmpeg.martin-riedl.de/download/macos/arm64/1789931890_9.0.2"
    FFMPEG_PACKAGES = (
        ToolPackage(f"{_RIEDL}/ffmpeg.zip", 28_395_699,
                    "c8ed4c4e6978a03c485edbfe4e0a5dc2380f8a30bba5150531b31b094492d924", {"ffmpeg": "ffmpeg"}),
        ToolPackage(f"{_RIEDL}/ffprobe.zip", 28_317_701,
                    "fcbe839537485eaee7a7a8bc5cbc0f90d53617e80943e8a5b2e31cb851197ea6", {"ffprobe": "ffprobe"}),
    )
else:
    FFMPEG_PACKAGES = ()


def tools_dir() -> Path:
    return data_dir() / "ffmpeg"


def _executable(name: str) -> str:
    return f"{name}.exe" if sys.platform == "win32" else name


def tool_path(name: str) -> str:
    """ffmpeg or ffprobe: Clipivo's downloaded copy, else the one on PATH, else the bare name."""
    own = tools_dir() / _executable(name)
    if own.is_file():
        return str(own)
    return shutil.which(name) or name


def ffmpeg_available() -> bool:
    return all((tools_dir() / _executable(name)).is_file() or shutil.which(name) for name in ("ffmpeg", "ffprobe"))


def ffmpeg_can_download() -> bool:
    return bool(FFMPEG_PACKAGES)


def ffmpeg_download_size() -> int:
    return sum(package.size for package in FFMPEG_PACKAGES)


def download_ffmpeg(progress: ProgressFn | None = None, cancelled: CancelledFn | None = None) -> Path:
    target = tools_dir()
    target.mkdir(parents=True, exist_ok=True)
    total = ffmpeg_download_size()
    done = 0
    for package in FFMPEG_PACKAGES:
        archive = target / Path(package.url).name
        try:
            _fetch(package.url, archive, package.size, package.sha256, done, total, progress, cancelled, "ffmpeg")
            with zipfile.ZipFile(archive) as bundle:
                for member, installed in package.members.items():
                    destination = target / installed
                    with bundle.open(member) as source, open(destination, "wb") as handle:
                        shutil.copyfileobj(source, handle, 1 << 20)
                    destination.chmod(destination.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        except (KeyError, zipfile.BadZipFile) as exc:
            raise DownloadError(f"The ffmpeg download was not as expected: {exc}") from exc
        finally:
            archive.unlink(missing_ok=True)
        done += package.size
    return target


# NVIDIA GPU acceleration on Linux -------------------------------------------------------

def gpu_dir() -> Path:
    return data_dir() / "gpu" / "site-packages"


def gpu_pack_installed() -> bool:
    return (gpu_dir() / "onnxruntime").is_dir() and (gpu_dir() / ".complete").is_file()


def gpu_pack_offered() -> bool:
    """Linux with an NVIDIA driver, where this build's ONNX Runtime has no CUDA."""
    if not sys.platform.startswith("linux") or not shutil.which("nvidia-smi"):
        return False
    try:
        import onnxruntime

        return "CUDAExecutionProvider" not in onnxruntime.get_available_providers()
    except ImportError:
        return False


def activate_gpu_pack() -> bool:
    """Put the downloaded CUDA build of ONNX Runtime ahead of the built-in one. Call before
    onnxruntime is imported."""
    if not gpu_pack_installed() or "onnxruntime" in sys.modules:
        return False
    sys.path.insert(0, str(gpu_dir()))
    return True


def preload_gpu_libraries() -> None:
    """Load the CUDA libraries shipped as NVIDIA's pip packages so ONNX Runtime finds them."""
    try:
        import onnxruntime

        preload = getattr(onnxruntime, "preload_dlls", None)
        if callable(preload) and gpu_pack_installed():
            preload(cuda=True, cudnn=True, msvc=False, directory="")
    except Exception:  # noqa: BLE001 - CPU still works; the GPU simply is not used.
        pass


def _onnxruntime_version() -> str:
    try:
        import importlib.metadata

        for name in ("onnxruntime", "onnxruntime-gpu"):
            try:
                return importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                continue
    except ImportError:
        pass
    import onnxruntime

    return onnxruntime.__version__


def _pypi(name: str, version: str | None = None) -> dict:
    url = f"https://pypi.org/pypi/{name}/json" if version is None else f"https://pypi.org/pypi/{name}/{version}/json"
    try:
        with urllib.request.urlopen(_request(url), timeout=30, context=_ssl_context()) as response:
            return json.load(response)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise DownloadError(f"Could not reach PyPI for {name}: {exc}") from exc


def _wheel_for(release_files: list[dict]) -> dict | None:
    """The Linux x86_64 wheel a CPython 3.11 process can load."""
    tags = ("cp311-cp311-manylinux", "py3-none-manylinux", "py3-none-any")
    for tag in tags:
        for item in release_files:
            name = item["filename"]
            if name.endswith(".whl") and tag in name and ("x86_64" in name or name.endswith("none-any.whl")):
                return item
    return None


def _compatible(version: str, spec: str) -> bool:
    """Enough of PEP 440 for '~=13.0' and '>=9' style pins on NVIDIA's numeric versions."""
    numbers = [int(part) for part in re.findall(r"\d+", version.split("+")[0])[:3]]
    for clause in filter(None, (part.strip() for part in spec.split(","))):
        operator, wanted = re.match(r"(~=|==|>=|<=|<|>)\s*([\d.]+)", clause).groups()
        target = [int(part) for part in wanted.split(".")]
        if operator == "~=":
            prefix = target[:-1] or target
            if numbers[:len(prefix)] != prefix or numbers < target:
                return False
        elif operator == "==" and numbers[:len(target)] != target:
            return False
        elif operator == ">=" and numbers < target:
            return False
        elif operator == "<" and numbers >= target:
            return False
        elif operator == "<=" and numbers > target:
            return False
        elif operator == ">" and numbers <= target:
            return False
    return True


def _requirements(info: dict, extras: tuple[str, ...]) -> list[tuple[str, str]]:
    found = []
    for line in info.get("requires_dist") or []:
        requirement, _, marker = line.partition(";")
        if marker and "extra" in marker and not any(f'"{extra}"' in marker for extra in extras):
            continue
        if marker and "extra" not in marker and "linux" not in marker and "sys_platform" in marker:
            continue
        match = re.match(r"\s*([A-Za-z0-9_.\-]+)\s*(.*)", requirement)
        name, spec = match.group(1).lower(), match.group(2).strip().strip("()")
        if name.startswith("nvidia-"):
            found.append((name, spec))
    return found


def gpu_pack_plan() -> list[RemoteFile]:
    """The wheels to fetch: onnxruntime-gpu matching this build, plus every NVIDIA package it
    and they need, newest compatible versions, each with PyPI's SHA-256."""
    version = _onnxruntime_version()
    runtime = _pypi("onnxruntime-gpu", version)
    wheel = _wheel_for(runtime["urls"])
    if wheel is None:
        raise DownloadError(f"PyPI has no Linux build of onnxruntime-gpu {version} for this Clipivo.")
    plan = [RemoteFile(wheel["filename"], wheel["size"], wheel["digests"]["sha256"], (wheel["url"],))]
    pending = _requirements(runtime["info"], ("cuda", "cudnn"))
    # cuBLAS is loaded by the CUDA provider but not always listed.
    pending.append(("nvidia-cublas", pending_major(pending)))
    seen: set[str] = set()
    while pending:
        name, spec = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        project = _pypi(name)
        versions = sorted(
            (item for item in project["releases"].items() if item[1] and _compatible(item[0], spec)),
            key=lambda item: [int(part) for part in re.findall(r"\d+", item[0])[:4]],
        )
        for release_version, files in reversed(versions):
            chosen = _wheel_for(files)
            if chosen:
                plan.append(RemoteFile(chosen["filename"], chosen["size"], chosen["digests"]["sha256"], (chosen["url"],)))
                pending.extend(_requirements(_pypi(name, release_version)["info"], ()))
                break
        else:
            raise DownloadError(f"PyPI has no Linux wheel of {name} {spec}.")
    return plan


def pending_major(pending: list[tuple[str, str]]) -> str:
    """The CUDA major version the runtime asks for, as a '~=N.0' pin for cuBLAS."""
    for name, spec in pending:
        if name.startswith("nvidia-cuda-runtime"):
            match = re.search(r"(\d+)", spec)
            if match:
                return f"~={match.group(1)}.0"
    return ""


def gpu_pack_size(plan: list[RemoteFile]) -> int:
    return sum(item.size for item in plan)


def download_gpu_pack(plan: list[RemoteFile], progress: ProgressFn | None = None, cancelled: CancelledFn | None = None) -> Path:
    target = gpu_dir()
    staging = target.with_name("site-packages.partial")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    total = gpu_pack_size(plan)
    done = 0
    try:
        for item in plan:
            wheel = staging / item.name
            _fetch(item.urls[0], wheel, item.size, item.sha256, done, total, progress, cancelled, "GPU acceleration")
            with zipfile.ZipFile(wheel) as archive:
                archive.extractall(staging)
            wheel.unlink()
            done += item.size
        for library in staging.rglob("*.so*"):
            library.chmod(library.stat().st_mode | stat.S_IXUSR)
        (staging / ".complete").write_text("\n".join(item.name for item in plan), encoding="utf-8")
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    shutil.rmtree(target, ignore_errors=True)
    staging.replace(target)
    return target


def remove_gpu_pack() -> None:
    shutil.rmtree(gpu_dir().parent, ignore_errors=True)


# Transfer ----------------------------------------------------------------------------

def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers={"User-Agent": "Clipivo"})


def _ssl_context():
    """A certificate store that exists: a frozen build's own libssl looks where the build
    machine kept certificates, so certifi's bundle is used when present."""
    import ssl

    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        return ssl.create_default_context()


def _fetch(
    url: str, target: Path, size: int, sha256: str, done: int, total: int,
    progress: ProgressFn | None, cancelled: CancelledFn | None, label: str,
) -> None:
    """Download to `target`, via a .part file so an interrupted transfer never looks complete."""
    partial = target.with_name(target.name + ".part")
    digest = hashlib.sha256()
    received = 0
    try:
        with urllib.request.urlopen(_request(url), timeout=60, context=_ssl_context()) as response, open(partial, "wb") as handle:
            while True:
                if cancelled and cancelled():
                    raise DownloadError("Download cancelled.")
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                handle.write(chunk)
                digest.update(chunk)
                received += len(chunk)
                if progress:
                    progress(done + received, total, label)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        partial.unlink(missing_ok=True)
        raise DownloadError(f"Could not download {label}: {exc}") from exc
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    if size and received != size:
        partial.unlink(missing_ok=True)
        raise DownloadError(f"{label} arrived incomplete ({received} of {size} bytes). Try again.")
    if sha256 and digest.hexdigest() != sha256:
        partial.unlink(missing_ok=True)
        raise DownloadError(f"{label} did not match its published checksum, so it was not used.")
    partial.replace(target)


def _verify(path: Path, size: int, sha256: str) -> None:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    if path.stat().st_size != size or (sha256 and digest.hexdigest() != sha256):
        path.unlink(missing_ok=True)
        raise DownloadError(f"{path.name} did not match its published checksum, so it was not used.")
