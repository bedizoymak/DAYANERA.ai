"""Common extraction data structures and text normalization."""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.core.config import Settings
from app.ingestion.storage import Storage

_UNICODE_SPACES = re.compile(r"[  -​  　﻿]")
_BOILERPLATE = [
    re.compile(r"^Copyright International Organization for Standardization\s*$"),
    re.compile(r"^Provided by IHS under license with ISO\s*$"),
    re.compile(r"^Not for Resale\s*$"),
    re.compile(r"^No reproduction or networking permitted without license from IHS\s*$"),
    re.compile(r"^--`[`,\-]*---\s*$"),
    re.compile(r"^www\.bzfxw\.com\s*$"),
]


def normalize_hyphens(text: str) -> str:
    """Unify hyphen variants: U+2010/U+2011/U+2012 -> '-'. A soft hyphen (U+00AD)
    at a line break is hyphenation and joins the word; anywhere else some PDF
    fonts use it for a visible hyphen (e.g. "ISO 1328\\xad1"), so it becomes '-'."""
    text = text.replace("‐", "-").replace("‑", "-").replace("‒", "-")
    text = re.sub(r"­[ \t]*\r?\n", "", text)
    return text.replace("­", "-")


def normalize_text(text: str) -> str:
    text = normalize_hyphens(text)
    text = _UNICODE_SPACES.sub(" ", text).replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for ln in text.split("\n"):
        s = ln.rstrip()
        if any(p.match(s.strip()) for p in _BOILERPLATE):
            continue
        lines.append(s)
    out = "\n".join(lines)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


@dataclass
class PageOut:
    page_number: int
    text: str
    method: str
    locator: str
    ocr_confidence: float | None = None


@dataclass
class ExtractionOutput:
    pages: list[PageOut] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    stored_only: bool = False
    stored_only_reason: str | None = None
    archive_members: list[dict[str, Any]] = field(default_factory=list)
    media: list[str] = field(default_factory=list)


@dataclass
class ExtractContext:
    settings: Settings
    storage: Storage
    version_id: uuid.UUID
    sha256: str
    filename: str
    abs_path: str  # stored raw file path (short, below DATA_ROOT)
    depth: int = 0  # archive nesting depth
