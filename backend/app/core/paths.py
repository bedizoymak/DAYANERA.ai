"""Filesystem helpers: Windows long paths and path-safety checks."""
from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

_LONG_PREFIX = "\\\\?\\"
_UNC_LONG_PREFIX = "\\\\?\\UNC\\"


def fs(path: str | os.PathLike[str]) -> str:
    """Return an OS path string usable for any length on Windows.

    Windows limits classic paths to 260 characters (MAX_PATH). Several ISO
    booklet filenames exceed that limit, so on Windows we always use the
    extended-length ``\\\\?\\`` prefix for absolute paths.
    """
    raw = os.fspath(path)
    if os.name != "nt":
        return os.path.abspath(raw)
    if raw.startswith(_LONG_PREFIX):
        return raw
    s = os.path.abspath(raw)
    if len(s) < 240:
        return s  # short paths stay plain for maximum library compatibility
    if s.startswith("\\\\"):
        return _UNC_LONG_PREFIX + s[2:]
    return _LONG_PREFIX + s


def strip_long_prefix(path: str) -> str:
    if path.startswith(_UNC_LONG_PREFIX):
        return "\\\\" + path[len(_UNC_LONG_PREFIX):]
    if path.startswith(_LONG_PREFIX):
        return path[len(_LONG_PREFIX):]
    return path


def is_within(child: str | os.PathLike[str], parent: str | os.PathLike[str]) -> bool:
    c = os.path.normcase(os.path.abspath(strip_long_prefix(os.fspath(child))))
    p = os.path.normcase(os.path.abspath(strip_long_prefix(os.fspath(parent))))
    return c == p or c.startswith(p.rstrip("\\/") + os.sep)


def safe_join(base: str | os.PathLike[str], *parts: str) -> Path:
    """Join path parts below ``base`` and refuse anything that escapes it."""
    base_abs = Path(os.path.abspath(strip_long_prefix(os.fspath(base))))
    candidate = Path(os.path.abspath(os.path.join(str(base_abs), *parts)))
    if not is_within(candidate, base_abs):
        raise PermissionError("Yol, izin verilen klasörün dışına çıkıyor (path traversal engellendi).")
    return candidate


_TR_MAP = str.maketrans({
    "ç": "c", "Ç": "c", "ğ": "g", "Ğ": "g", "ı": "i", "İ": "i", "ö": "o", "Ö": "o",
    "ş": "s", "Ş": "s", "ü": "u", "Ü": "u",
})


def slugify(text: str, max_len: int = 60) -> str:
    text = text.translate(_TR_MAP)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return (text[:max_len].strip("-")) or "not"


def dir_size_bytes(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for root, _dirs, files in os.walk(fs(path)):
        for f in files:
            try:
                total += os.stat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total
