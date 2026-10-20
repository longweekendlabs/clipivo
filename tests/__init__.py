"""Clipivo tests."""

import tempfile

from PySide6.QtCore import QSettings

# Keep tests away from the user's real Clipivo settings.
_SETTINGS_DIR = tempfile.mkdtemp(prefix="clipivo-test-settings-")
for _format in (QSettings.NativeFormat, QSettings.IniFormat):
    QSettings.setPath(_format, QSettings.UserScope, _SETTINGS_DIR)
