"""Typed request/response models of the DAYANERA Core API (v1).

The contract is transport-level and independent of the inference provider
and the database implementation.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ErrorOut(BaseModel):
    detail: str


# --- auth / users -----------------------------------------------------------
class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=512)


class UserOut(BaseModel):
    id: str
    username: str
    display_name: str
    role: Literal["owner_admin", "member"]
    is_active: bool = True


class UserCreateIn(BaseModel):
    username: str = Field(min_length=2, max_length=64, pattern=r"^[a-zA-Z0-9._-]+$")
    display_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=512)
    role: Literal["owner_admin", "member"] = "member"


class UserUpdateIn(BaseModel):
    display_name: str | None = Field(default=None, max_length=120)
    role: Literal["owner_admin", "member"] | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=512)


class ScopeIn(BaseModel):
    scope_type: Literal["knowledge_area", "document", "conversation"]
    scope_id: str


class ScopeOut(BaseModel):
    id: str
    scope_type: str
    scope_id: str
    label: str | None = None
    granted_at: str


class KnowledgeAreaOut(BaseModel):
    id: str
    slug: str
    name: str
    description: str | None
    is_verified_corpus: bool


# --- conversations / messages ---------------------------------------------
class ConversationOut(BaseModel):
    id: str
    title: str
    status: Literal["active", "archived"]
    owner_id: str
    created_at: str
    updated_at: str


class ConversationCreateIn(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class ConversationPatchIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    status: Literal["active", "archived"] | None = None


class SourceOut(BaseModel):
    rank: int
    document_id: str
    version_id: str
    version_number: int
    document_title: str
    standard_code: str | None
    page_number: int | None
    locator: str
    excerpt: str
    score: float
    confidence_status: str
    cited: bool


class AttachmentRef(BaseModel):
    document_id: str
    filename: str
    status: str
    ingestion_status: str | None
    mime_type: str | None


class MessageOut(BaseModel):
    id: str
    conversation_id: str
    role: Literal["user", "assistant", "system"]
    content: str
    answer_mode: Literal["general", "verified_source", "calculation", "draft_extraction", "unverified"] | None
    answer_mode_label: str | None
    status: str
    error_code: str | None
    show_sources: bool
    sources: list[SourceOut]
    attachments: list[AttachmentRef]
    metadata: dict[str, Any]
    created_at: str | None
    model: str | None = None
    latency_ms: int | None = None


class MessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=20000)
    attachment_ids: list[str] = Field(default_factory=list, max_length=20)


class ChatTurnOut(BaseModel):
    user_message: MessageOut
    assistant_message: MessageOut | None


# --- documents ---------------------------------------------------------------
class DocumentOut(BaseModel):
    id: str
    title: str
    standard_code: str | None
    source_kind: str
    source_relpath: str | None
    original_filename: str
    status: str
    knowledge_area: str | None
    is_verified_corpus: bool
    current_version: dict[str, Any] | None
    created_at: str
    updated_at: str
    deleted_at: str | None = None
    delete_reason: str | None = None


class DocumentListOut(BaseModel):
    items: list[DocumentOut]
    total: int


class DeleteIn(BaseModel):
    reason: str = Field(default="Kullanıcı tarafından silindi", max_length=500)


class CorpusNoteIn(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


# --- extraction review -----------------------------------------------------
class ConfirmValueIn(BaseModel):
    value: float | None = None
    unit: str | None = Field(default=None, max_length=20)
    note: str | None = Field(default=None, max_length=1000)


class ReviewNoteIn(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


class ConfirmPageIn(BaseModel):
    text: str | None = Field(default=None, max_length=200000)
    note: str | None = Field(default=None, max_length=1000)


# --- calculations ------------------------------------------------------------
class CalcInputIn(BaseModel):
    value: float
    unit: str = Field(default="", max_length=20)
    provenance: Literal["user_input", "extracted_value", "memory_item"] = "user_input"
    ref_id: str | None = None


class CalcIn(BaseModel):
    calc_type: str = Field(max_length=80)
    inputs: dict[str, CalcInputIn]
    compare_with_llm: bool = False
    llm_draft_outputs: dict[str, float] | None = Field(
        default=None, description="Test/entegrasyon: harici bir taslak sonucu motorla karşılaştırmak için.")
    conversation_id: str | None = None


class CalcOut(BaseModel):
    id: str
    calc_type: str
    status: str
    engine_version: str
    result: dict[str, Any]
    inputs: dict[str, Any]
    llm_draft: dict[str, Any] | None
    comparison: dict[str, Any] | None
    mismatch: bool
    created_at: str
    message_id: str | None = None


# --- memory ---------------------------------------------------------------
class MemoryIn(BaseModel):
    kind: Literal["fact", "preference", "technical_value", "source_link", "relationship", "note"] = "fact"
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1, max_length=20000)
    status: Literal["user_confirmed", "unverified"] = "user_confirmed"
    visibility: Literal["private", "shared"] = "private"
    structured: dict[str, Any] = Field(default_factory=dict)


class MemoryPatchIn(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    content: str | None = Field(default=None, max_length=20000)
    structured: dict[str, Any] | None = None
    status: Literal["user_confirmed", "unverified", "archived", "deleted"] | None = None


# --- notes -----------------------------------------------------------------
class NoteIn(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    recommendation: str = Field(min_length=1, max_length=20000)
    rationale: str = ""
    affected_areas: str = ""
    expected_benefit: str = ""
    risks: str = ""
    status: Literal["öneri", "inceleniyor", "uygulandı", "reddedildi"] = "öneri"
    author: str | None = Field(default=None, max_length=200)


class NotePatchIn(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    status: Literal["öneri", "inceleniyor", "uygulandı", "reddedildi"] | None = None
    recommendation: str | None = None
    rationale: str | None = None
    affected_areas: str | None = None
    expected_benefit: str | None = None
    risks: str | None = None


class RetrievalIn(BaseModel):
    query: str = Field(min_length=2, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)
