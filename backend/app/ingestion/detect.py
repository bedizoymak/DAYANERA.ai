"""File type detection: magic bytes first, extension second."""
from __future__ import annotations

import mimetypes
from dataclasses import dataclass

import filetype

TEXT_EXT = {".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".html", ".htm", ".log", ".ini", ".yaml", ".yml", ".rst"}
OFFICE_NEW = {".docx": "docx", ".xlsx": "xlsx", ".pptx": "pptx", ".docm": "docx", ".xlsm": "xlsx"}
OFFICE_LEGACY = {".doc", ".xls", ".ppt", ".rtf", ".odt", ".ods", ".odp"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp"}
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".wma", ".aac"}
VIDEO_EXT = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".wmv", ".m4v", ".mpg", ".mpeg"}
ARCHIVE_EXT = {".zip": "zip", ".tar": "tar", ".tgz": "tar", ".gz": "tar", ".bz2": "tar", ".xz": "tar",
               ".7z": "other", ".rar": "other"}


@dataclass
class Detected:
    category: str  # pdf|docx|xlsx|pptx|office_legacy|text|image|audio|video|zip|tar|archive_other|unknown
    mime_type: str
    extension: str


def detect(filename: str, head: bytes) -> Detected:
    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    kind = filetype.guess(head)
    mime = kind.mime if kind else (mimetypes.guess_type(filename)[0] or "application/octet-stream")

    if head.startswith(b"%PDF") or ext == ".pdf":
        return Detected("pdf", "application/pdf", ext or ".pdf")
    if ext in OFFICE_NEW:
        return Detected(OFFICE_NEW[ext], mime, ext)
    if ext in OFFICE_LEGACY:
        return Detected("office_legacy", mime, ext)
    if (kind and kind.mime.startswith("image/")) or ext in IMAGE_EXT:
        return Detected("image", mime if mime.startswith("image/") else "image/" + ext.lstrip("."), ext)
    if (kind and kind.mime.startswith("audio/")) or ext in AUDIO_EXT:
        return Detected("audio", mime, ext)
    if (kind and kind.mime.startswith("video/")) or ext in VIDEO_EXT:
        return Detected("video", mime, ext)
    if ext in ARCHIVE_EXT or (kind and kind.mime in ("application/zip", "application/x-tar", "application/gzip")):
        sub = ARCHIVE_EXT.get(ext) or ("zip" if kind and kind.mime == "application/zip" else "tar")
        return Detected("zip" if sub == "zip" else "tar" if sub == "tar" else "archive_other", mime, ext)
    if ext in TEXT_EXT or _looks_like_text(head):
        return Detected("text", mimetypes.guess_type(filename)[0] or "text/plain", ext)
    return Detected("unknown", mime, ext)


def _looks_like_text(head: bytes) -> bool:
    if not head:
        return False
    if b"\x00" in head[:4096]:
        return False
    try:
        head[:4096].decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False
