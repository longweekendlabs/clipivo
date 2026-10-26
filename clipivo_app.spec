# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Clipivo on Linux, Windows and macOS (one-dir builds, plus an .app on macOS).
# Run: python -m PyInstaller --clean --noconfirm clipivo_app.spec
#
# Face models and, on Windows and macOS, ffmpeg are downloaded on first launch
# (core/downloads.py), so neither is in here. VLC is the system's own.

import os
import re
import sys

ROOT = os.path.abspath(SPECPATH)
VERSION = re.search(r'__version__\s*=\s*"([^"]+)"', open(os.path.join(ROOT, "version.py"), encoding="utf-8").read()).group(1)

datas = [
    ("assets", "assets"),
    ("LICENSE", "."),
]

a = Analysis(
    ["app.py"],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=[
        # Imported lazily by core/face_models.py
        "onnxruntime",
        "onnxruntime.capi._pybind_state",
        "vlc",
        "keyring.backends",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Development-only packages that may sit in the build environment.
        "insightface", "onnx", "scipy", "skimage", "sklearn", "matplotlib", "torch",
        "nvidia", "tkinter", "IPython", "pytest",
        # Qt modules Clipivo never imports.
        "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtPdf",
        "PySide6.QtDBus", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtOpenGL",
        "PySide6.QtOpenGLWidgets", "PySide6.QtConcurrent", "PySide6.QtXml",
        "PySide6.QtPrintSupport", "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtUiTools",
    ],
    noarchive=False,
)

# VLC is the system's own. A libVLC copied into the bundle cannot find its plugins and
# never starts, so whatever the build machine had installed is left out.
a.binaries = [entry for entry in a.binaries if not os.path.basename(entry[0]).startswith(("libvlc", "libvlccore"))]

pyz = PYZ(a.pure)

icon = None
if sys.platform == "win32":
    icon = "assets/icons/clipivo.ico"
elif sys.platform == "darwin" and os.path.exists("assets/icons/clipivo.icns"):
    icon = "assets/icons/clipivo.icns"

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="clipivo",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=icon,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="clipivo",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Clipivo.app",
        icon=icon,
        bundle_identifier="io.longweekendlabs.Clipivo",
        info_plist={
            "CFBundleDisplayName": "Clipivo",
            "CFBundleVersion": VERSION,
            "CFBundleShortVersionString": VERSION,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "14.0",
        },
    )
