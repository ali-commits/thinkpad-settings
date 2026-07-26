"""ThinkPad BIOS Settings — a GTK4 editor for Lenovo firmware attributes."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _installed_version
from pathlib import Path

# The VERSION file at the repository root is the single source of truth, and
# setuptools reads it at build time (pyproject.toml: dynamic version).
_VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"


def _read_version() -> str:
    """Prefer the file, fall back to installed metadata.

    Order matters. An editable install records the version at install time, so
    reading metadata first would report a stale number in a source tree until
    the next `uv sync`. VERSION sits next to the package only in a checkout; an
    installed copy has no such file and correctly falls through to its own
    metadata.
    """
    try:
        return _VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    try:
        return _installed_version("thinkpad-settings")
    except PackageNotFoundError:
        return "0.0.0+unknown"


__version__ = _read_version()
