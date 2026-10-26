"""Print Clipivo's version. Used by the release workflow on every platform.

A one-liner inline in YAML has to survive bash, PowerShell and YAML quoting all at once;
this is the same call everywhere.
"""
import pathlib
import re
import sys

TEXT = pathlib.Path(__file__).resolve().parent.parent.joinpath("version.py").read_text(encoding="utf-8")
match = re.search(r'__version__\s*=\s*"([^"]+)"', TEXT)
if not match:
    sys.exit("could not find __version__ in version.py")
print(match.group(1))
