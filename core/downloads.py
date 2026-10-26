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
import subprocess
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
    """A download that did not complete. `blocked` is True when the server could not be
    reached at all, which is usually a firewall, antivirus, proxy or no internet."""

    def __init__(self, message: str, *, blocked: bool = False, url: str = ""):
        super().__init__(message)
        self.blocked = blocked
        self.url = url


def explain_failure(exc: BaseException, url: str, label: str) -> DownloadError:
    """Turn a network error into words a user can act on."""
    import ssl
    import urllib.parse

    host = urllib.parse.urlparse(url).hostname or url
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (401, 403, 407, 451):
            return DownloadError(
                f"{host} refused the download of {label} (HTTP {exc.code}). A proxy or company "
                "network may be blocking it.", blocked=True, url=url,
            )
        return DownloadError(f"{host} answered HTTP {exc.code} for {label}. Try again later.", url=url)
    reason = getattr(exc, "reason", exc)
    if isinstance(reason, ssl.SSLError) or isinstance(exc, ssl.SSLError):
        return DownloadError(
            f"The secure connection to {host} failed for {label}. Antivirus software that "
            "inspects HTTPS, or a company proxy, often causes this.", blocked=True, url=url,
        )
    if isinstance(reason, (TimeoutError, OSError)) or isinstance(exc, TimeoutError):
        return DownloadError(
            f"Clipivo could not reach {host} to download {label}. Check the internet connection; "
            "a firewall, antivirus or proxy may be blocking it.", blocked=True, url=url,
        )
    return DownloadError(f"Could not download {label} from {host}: {exc}", url=url)


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


def face_models_manual() -> tuple[list[str], Path]:
    """Links to download by hand, and the folder to put the files in."""
    return [item.urls[0] for item in FACE_MODEL_FILES], face_model_dir()


def ffmpeg_manual() -> tuple[list[str], Path]:
    return [package.url for package in FFMPEG_PACKAGES], tools_dir()


def face_model_dir() -> Path:
    return data_dir() / "models" / "buffalo_l"


def _app_folders() -> list[Path]:
    """Every per-user and installed application folder on this computer: where other apps,
    including other Long Weekend Labs apps, keep the same model pack."""
    home = Path.home()
    if sys.platform == "win32":
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        roots = [
            local, Path(os.environ.get("APPDATA", home / "AppData" / "Roaming")), local / "Programs",
            Path(os.environ.get("PROGRAMFILES", "C:/Program Files")),
            Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")),
        ]
    elif sys.platform == "darwin":
        roots = [home / "Library" / "Application Support", Path("/Applications"), home / "Applications"]
    else:
        roots = [
            Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share")),
            Path("/opt"), Path("/usr/share"), Path("/usr/local/share"),
        ]
    folders: list[Path] = []
    for root in roots:
        try:
            folders.extend(sorted(child for child in root.iterdir() if child.is_dir()))
        except OSError:
            continue
    return folders


# Where an app keeps the pack inside its own folder: a data folder, a PyInstaller build,
# a macOS bundle.
_PACK_PLACES = (
    "models/buffalo_l",
    "_internal/models/buffalo_l",
    "Contents/Resources/models/buffalo_l",
    "Contents/Frameworks/models/buffalo_l",
)


def _face_model_candidates() -> list[Path]:
    """Folders where the pack may already be, read-only, before downloading another copy:
    Clipivo's own, insightface's cache, then every other application's folder."""
    candidates = [face_model_dir(), APP_DIR / "models" / "buffalo_l", Path.home() / ".insightface" / "models" / "buffalo_l"]
    shared = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    candidates.append(shared / "long-weekend-labs" / "models" / "buffalo_l")
    for folder in _app_folders():
        candidates.extend(folder / place for place in _PACK_PLACES)
    try:
        # Running from a source checkout beside other projects that use the same pack.
        candidates.extend(sorted(APP_DIR.parent.glob("*/models/buffalo_l")))
    except OSError:
        pass
    unique: list[Path] = []
    for folder in candidates:
        if folder not in unique:
            unique.append(folder)
    return unique


def _verified_cache() -> Path:
    return data_dir() / "verified-files.json"


def _is_verified(path: Path, item: RemoteFile, cache: dict) -> bool:
    """The right size, and its checksum confirmed once: the result is remembered by size and
    modification time, so a copy found elsewhere is not hashed again on every start."""
    try:
        info = path.stat()
    except OSError:
        return False
    if info.st_size != item.size:
        return False
    stamp = [info.st_size, info.st_mtime_ns]
    if cache.get(str(path)) == stamp:
        return True
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    if digest.hexdigest() != item.sha256:
        return False
    cache[str(path)] = stamp
    return True


_FOUND: dict[str, Path | None] = {}


def forget_found() -> None:
    """Search again next time, after a download or when the user asks."""
    _FOUND.clear()


def find_face_models() -> Path | None:
    """The folder holding both model files, complete and checked, or None. Remembered for
    the session, since every application folder is searched."""
    if "face" in _FOUND and (_FOUND["face"] is None or _FOUND["face"].is_dir()):
        return _FOUND["face"]
    _FOUND["face"] = _search_face_models()
    return _FOUND["face"]


def _search_face_models() -> Path | None:
    try:
        cache = json.loads(_verified_cache().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    before = dict(cache)
    found = None
    for folder in _face_model_candidates():
        if all((folder / item.name).is_file() for item in FACE_MODEL_FILES) and all(
            _is_verified(folder / item.name, item, cache) for item in FACE_MODEL_FILES
        ):
            found = folder
            break
    if cache != before:
        try:
            _verified_cache().parent.mkdir(parents=True, exist_ok=True)
            _verified_cache().write_text(json.dumps(cache, indent=1), encoding="utf-8")
        except OSError:
            pass
    return found


def describe_location(folder: Path) -> str:
    """Where a found copy came from, in words."""
    if folder == face_model_dir():
        return "downloaded by Clipivo"
    if ".insightface" in folder.parts:
        return "InsightFace's own cache"
    if folder == APP_DIR / "models" / "buffalo_l":
        return "beside Clipivo"
    for root_part in ("_internal", "Contents", "models"):
        if root_part in folder.parts:
            owner = folder.parts[folder.parts.index(root_part) - 1]
            return f"shared with {owner}"
    return "found on this computer"


def face_models_download_size() -> int:
    return sum(item.size for item in FACE_MODEL_FILES)


def download_face_models(progress: ProgressFn | None = None, cancelled: CancelledFn | None = None) -> Path:
    forget_found()
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
    forget_found()
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


# The CUDA 12 libraries ONNX Runtime's CUDA build loads, and the last onnxruntime-gpu made for
# CUDA 12: when the computer already has these, only that one wheel is downloaded.
_CUDA12_LIBRARIES = ("libcudart.so.12", "libcublas.so.12", "libcublasLt.so.12", "libcufft.so.11",
                     "libcurand.so.10", "libcudnn.so.9")
_CUDA12_RUNTIME = "1.26.0"
_CUDA12_FOLDERS = ("/usr/local/cuda/lib64", "/usr/local/cuda/targets/x86_64-linux/lib", "/usr/lib64", "/usr/lib/x86_64-linux-gnu")


def system_cuda12() -> dict[str, str] | None:
    """Where an installed CUDA 12 and cuDNN 9 keep the libraries ONNX Runtime needs, or None."""
    if not sys.platform.startswith("linux"):
        return None
    found: dict[str, str] = {}
    try:
        cache = subprocess.run(["ldconfig", "-p"], capture_output=True, text=True, timeout=10, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        cache = ""
    for name in _CUDA12_LIBRARIES:
        for line in cache.splitlines():
            if line.strip().startswith(name + " ") and "x86-64" in line:
                found[name] = line.rsplit("=>", 1)[-1].strip()
                break
        else:
            for folder in _CUDA12_FOLDERS:
                if (Path(folder) / name).is_file():
                    found[name] = str(Path(folder) / name)
                    break
    return found if len(found) == len(_CUDA12_LIBRARIES) else None


def gpu_pack_kind() -> str:
    """"system-cuda12" when the pack uses the installed CUDA, "bundled" when it carries its own."""
    try:
        return json.loads((gpu_dir() / ".complete").read_text(encoding="utf-8")).get("kind", "bundled")
    except (OSError, ValueError):
        return "bundled"


def preload_gpu_libraries() -> None:
    """Make the CUDA libraries findable for ONNX Runtime's CUDA build: the installed CUDA 12,
    loaded by full path, or NVIDIA's pip packages that came with the pack."""
    if not gpu_pack_installed():
        return
    try:
        if gpu_pack_kind() == "system-cuda12":
            import ctypes

            for path in (system_cuda12() or {}).values():
                ctypes.CDLL(path, mode=ctypes.RTLD_GLOBAL)
            return
        import onnxruntime

        preload = getattr(onnxruntime, "preload_dlls", None)
        if callable(preload):
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
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise explain_failure(exc, url, f"the {name} details") from exc
    except ValueError as exc:
        raise DownloadError(f"PyPI sent an unreadable answer for {name}: {exc}") from exc


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
    """The wheels to fetch, each with PyPI's SHA-256. With CUDA 12 and cuDNN 9 already
    installed, only onnxruntime-gpu built for CUDA 12 (about 280 MB). Otherwise onnxruntime-gpu
    matching this build plus every NVIDIA package it needs (about 1.5 GB)."""
    if system_cuda12():
        runtime = _pypi("onnxruntime-gpu", _CUDA12_RUNTIME)
        wheel = _wheel_for(runtime["urls"])
        if wheel:
            return [RemoteFile(wheel["filename"], wheel["size"], wheel["digests"]["sha256"], (wheel["url"],))]
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


def gpu_pack_estimate() -> int:
    """Roughly what the GPU download will be, without asking PyPI: one onnxruntime-gpu wheel
    when CUDA 12 is installed, otherwise that plus NVIDIA's CUDA libraries."""
    return 280_000_000 if system_cuda12() else 1_550_000_000


def download_gpu_pack_now(progress: ProgressFn | None = None, cancelled: CancelledFn | None = None) -> Path:
    """Resolve the exact files on PyPI, then download them."""
    return download_gpu_pack(gpu_pack_plan(), progress, cancelled)


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
        kind = "system-cuda12" if len(plan) == 1 and system_cuda12() else "bundled"
        (staging / ".complete").write_text(json.dumps({"kind": kind, "files": [item.name for item in plan]}), encoding="utf-8")
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
        raise explain_failure(exc, url, label) from exc
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
