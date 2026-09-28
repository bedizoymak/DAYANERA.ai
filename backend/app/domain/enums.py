"""Domain vocabulary shared by services, API and tests."""
from __future__ import annotations

from enum import StrEnum

REFUSAL_PHRASE = "Bu kaynak setinde doğrulayamadım"


class Role(StrEnum):
    OWNER_ADMIN = "owner_admin"
    MEMBER = "member"


class ConfidenceStatus(StrEnum):
    VERIFIED_SOURCE = "verified_source"
    USER_CONFIRMED = "user_confirmed"
    DRAFT_EXTRACTION = "draft_extraction"
    UNVERIFIED = "unverified"
    SUPERSEDED = "superseded"
    DELETED = "deleted"
    ARCHIVED = "archived"
    REJECTED = "rejected"


ACTIVE_EVIDENCE_STATUSES = (ConfidenceStatus.VERIFIED_SOURCE.value, ConfidenceStatus.USER_CONFIRMED.value)


class AnswerMode(StrEnum):
    GENERAL = "general"
    VERIFIED_SOURCE = "verified_source"
    CALCULATION = "calculation"
    DRAFT_EXTRACTION = "draft_extraction"
    UNVERIFIED = "unverified"


ANSWER_MODE_LABELS_TR = {
    AnswerMode.GENERAL: "Genel sohbet",
    AnswerMode.VERIFIED_SOURCE: "Doğrulanmış kaynak cevabı",
    AnswerMode.CALCULATION: "Hesap sonucu",
    AnswerMode.DRAFT_EXTRACTION: "Taslak çıkarım",
    AnswerMode.UNVERIFIED: "Doğrulanamadı",
}


class DocumentStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    DELETED = "deleted"
    ARCHIVED = "archived"
    FAILED = "failed"


class VersionState(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    DELETED = "deleted"
    ARCHIVED = "archived"
    FAILED = "failed"


class IngestionStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    INDEXED = "indexed"
    STORED_ONLY = "stored_only"
    FAILED = "failed"


class CorpusState(StrEnum):
    """Availability of the verified ISO corpus, separate from ingestion state."""

    EMPTY = "EMPTY"
    INDEXED_UNAPPROVED = "INDEXED_UNAPPROVED"
    READY = "READY"


class SourceKind(StrEnum):
    WATCHED = "watched"
    UPLOAD = "upload"
    ATTACHMENT = "attachment"


class ExtractionMethod(StrEnum):
    NATIVE_TEXT = "native_text"
    NATIVE_TEXT_REBUILT = "native_text_rebuilt"
    OCR = "ocr"
    TRANSCRIPTION = "transcription"
    OFFICE = "office"
    PLAIN_TEXT = "plain_text"
    ARCHIVE_MEMBER_TEXT = "archive_member_text"
    METADATA = "metadata"


DRAFT_METHODS = {ExtractionMethod.OCR.value, ExtractionMethod.TRANSCRIPTION.value}

NOTE_STATUSES = ("öneri", "inceleniyor", "uygulandı", "reddedildi")
