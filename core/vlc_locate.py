"""Finding the VLC already installed on this computer, for playback.

Clipivo never downloads or installs VLC. It looks where VLC installs itself on each system,
points python-vlc at that copy before it loads, and can say what it found: where, which
version, or why a copy it found cannot be used (32-bit VLC on Windows, a Flatpak on Linux).
"""

from __future__ import annotations

import ctypes.util
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class VlcLocation:
    library: Path | None
    plugins: Path | None
    # Why a VLC that was found cannot be used, or what to install; empty when usable.
    problem: str = ""

    @property
    def usable(self) -> bool:
        return self.library is not None and not self.problem


def _windows_registry_folders() -> list[Path]:
    folders = []
    try:
        import winreg

        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(hive, r"Software\VideoLAN\VLC", 0, winreg.KEY_READ | view) as key:
                        value, _kind = winreg.QueryValueEx(key, "InstallDir")
                        folders.append(Path(value))
                except OSError:
                    continue
    except ImportError:
        pass
    return folders


def _is_64bit_dll(path: Path) -> bool:
    """Read the PE header: machine 0x8664 is x86-64, 0xAA64 is ARM64."""
    try:
        with open(path, "rb") as handle:
            handle.seek(0x3C)
            offset = int.from_bytes(handle.read(4), "little")
            handle.seek(offset + 4)
            machine = int.from_bytes(handle.read(2), "little")
        return machine in (0x8664, 0xAA64)
    except OSError:
        return False


def _locate_windows() -> VlcLocation:
    candidates = _windows_registry_folders()
    for variable in ("PROGRAMFILES", "PROGRAMW6432", "LOCALAPPDATA", "PROGRAMFILES(X86)"):
        base = os.environ.get(variable)
        if base:
            candidates.append(Path(base) / ("Programs/VideoLAN/VLC" if variable == "LOCALAPPDATA" else "VideoLAN/VLC"))
    only_32bit = None
    for folder in dict.fromkeys(candidates):
        library = folder / "libvlc.dll"
        if not library.is_file():
            continue
        if _is_64bit_dll(library):
            return VlcLocation(library, folder / "plugins")
        only_32bit = only_32bit or folder
    if only_32bit:
        return VlcLocation(
            None, None,
            f"The VLC in {only_32bit} is the 32-bit version, which 64-bit Clipivo cannot use. "
            "Install the 64-bit VLC from videolan.org.",
        )
    return VlcLocation(None, None, "VLC is not installed. Install it from videolan.org for playback.")


def _locate_macos() -> VlcLocation:
    for app in (Path("/Applications/VLC.app"), Path.home() / "Applications" / "VLC.app"):
        library = app / "Contents" / "MacOS" / "lib" / "libvlc.dylib"
        if library.is_file():
            return VlcLocation(library, app / "Contents" / "MacOS" / "plugins")
    return VlcLocation(None, None, "VLC is not installed. Put VLC.app in Applications for playback.")


def _locate_linux() -> VlcLocation:
    for folder in ("/usr/lib64", "/usr/lib/x86_64-linux-gnu", "/usr/lib", "/usr/local/lib"):
        library = Path(folder) / "libvlc.so.5"
        if library.is_file():
            return VlcLocation(library, None)
    name = ctypes.util.find_library("vlc")
    if name:
        return VlcLocation(Path(name), None)
    if shutil.which("flatpak"):
        try:
            listed = subprocess.run(["flatpak", "info", "org.videolan.VLC"], capture_output=True, timeout=5, check=False)
            if listed.returncode == 0:
                return VlcLocation(
                    None, None,
                    "VLC is installed as a Flatpak, whose libraries other apps cannot use. "
                    "Install your distribution's vlc package as well.",
                )
        except (OSError, subprocess.SubprocessError):
            pass
    return VlcLocation(None, None, "VLC is not installed. Install the vlc package for playback.")


_LOCATION: VlcLocation | None = None


def locate() -> VlcLocation:
    global _LOCATION
    if _LOCATION is None:
        if sys.platform == "win32":
            _LOCATION = _locate_windows()
        elif sys.platform == "darwin":
            _LOCATION = _locate_macos()
        else:
            _LOCATION = _locate_linux()
    return _LOCATION


def prepare() -> VlcLocation:
    """Point python-vlc at the installed VLC. Call before `import vlc`."""
    location = locate()
    if location.usable and location.library.is_absolute():
        os.environ.setdefault("PYTHON_VLC_LIB_PATH", str(location.library))
        if location.plugins and location.plugins.is_dir():
            os.environ.setdefault("PYTHON_VLC_MODULE_PATH", str(location.plugins))
            os.environ.setdefault("VLC_PLUGIN_PATH", str(location.plugins))
        if sys.platform == "win32" and hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(str(location.library.parent))
            except OSError:
                pass
    return location
