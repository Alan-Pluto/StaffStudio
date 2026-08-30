from __future__ import annotations

import os
import sys
from pathlib import Path

DEFAULT_APP_VERSION = "0.1.0"
_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def _bundled_version() -> str | None:
    candidates: list[Path] = []
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        candidates.append(Path(bundle_root) / "staff-studio-version.txt")
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).resolve()
        candidates.append(executable.parent / "staff-studio-version.txt")
        if sys.platform == "darwin" and len(executable.parents) >= 2:
            candidates.append(executable.parents[1] / "Resources" / "staff-studio-version.txt")
    for candidate in candidates:
        try:
            value = candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            return value
    return None


def app_version() -> str:
    return (
        os.environ.get("STAFF_STUDIO_VERSION", "").strip()
        or _bundled_version()
        or DEFAULT_APP_VERSION
    )


def update_check_enabled() -> bool:
    configured = os.environ.get("STAFF_STUDIO_UPDATE_CHECK", "").strip().lower()
    if configured in _TRUE_VALUES:
        return True
    if configured in _FALSE_VALUES:
        return False
    return bool(getattr(sys, "frozen", False))


__all__ = ["app_version", "update_check_enabled"]
