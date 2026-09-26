"""Conversations, messages, streaming generation, sources and calculation detail."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user, db_dep, deny, provider_dep, scopes_dep, settings_dep
from app.api.schemas import (
    ChatTurnOut,
    ConversationCreateIn,
    ConversationOut,
    ConversationPatchIn,
    MessageIn,
    MessageOut,
)
from app.core.config import Settings
from app.db.models import Calculation, Conversation, Message, MessageSource
from app.inference.base import LLMProvider
from app.services import audit
from app.services.access import ScopeSet, can_view_conversation, can_write_conversation
from app.services.auth import AuthenticatedUser
from app.services.chat import NEW_TITLE, ChatService
from app.services.serialize import conversation_to_dict, message_to_dict, source_to_dict

router = APIRouter(tags=["conversations"])


def _uuid(v: str) -> uuid.UUID:
    try:
        return uuid.UUID(v)
    except ValueError as exc:
        raise HTTPException(404, "Bulunamadı.") from exc


def _get_conv(db: Session, conv_id: str, user: AuthenticatedUser, scopes: ScopeSet, request: Request, *,
              write: bool = False) -> Conversation:
    conv = db.get(Conversation, _uuid(conv_id))
    if conv is None:
        raise HTTPException(404, "Konuşma bulunamadı.")
    ok = can_write_conversation(scopes, conv) if write else can_view_conversation(scopes, conv)
    if not ok:
        deny(user, request, "conversation_scope", target_id=str(conv.id))
    return conv


@router.get("/conversations", response_model=list[ConversationOut], summary="Konuşma listesi")
def list_conversations(status: str = Query("active", pattern="^(active|archived|all)$"),
                       user: AuthenticatedUser = Depends(current_user), scopes: ScopeSet = Depends(scopes_dep),
                       db: Session = Depends(db_dep), mine_only: bool = True) -> list[dict]:
    q = select(Conversation)
    if status != "all":
        q = q.where(Conversation.status == status)
    if not scopes.is_owner or mine_only:
        cond = Conversation.owner_id == user.id
        if scopes.conversation_ids:
            cond = cond | Conversation.id.in_(scopes.conversation_ids)
        q = q.where(cond)
    rows = db.execute(q.order_by(Conversation.updated_at.desc()).limit(500)).scalars()
    return [conversation_to_dict(c) for c in rows]


@router.post("/conversations", response_model=ConversationOut, status_code=201, summary="Yeni konuşma")
def create_conversation(body: ConversationCreateIn, user: AuthenticatedUser = Depends(current_user),
                        db: Session = Depends(db_dep)) -> dict:
    conv = Conversation(owner_id=user.id, title=(body.title or NEW_TITLE).strip() or NEW_TITLE)
    db.add(conv)
    db.flush()
    audit.record(db, user.actor, "conversation.create", target_type="conversation", target_id=conv.id)
    db.commit()
    return conversation_to_dict(conv)


@router.get("/conversations/{conv_id}", response_model=ConversationOut)
def get_conversation(conv_id: str, request: Request, user: AuthenticatedUser = Depends(current_user),
                     scopes: ScopeSet = Depends(scopes_dep), db: Session = Depends(db_dep)) -> dict:
    return conversation_to_dict(_get_conv(db, conv_id, user, scopes, request))


@router.patch("/conversations/{conv_id}", response_model=ConversationOut, summary="Yeniden adlandır / arşivle")
def patch_conversation(conv_id: str, body: ConversationPatchIn, request: Request,
                       user: AuthenticatedUser = Depends(current_user), scopes: ScopeSet = Depends(scopes_dep),
                       db: Session = Depends(db_dep)) -> dict:
    conv = _get_conv(db, conv_id, user, scopes, request, write=True)
    changes = {}
    if body.title is not None:
        changes["title"] = {"from": conv.title, "to": body.title}
        conv.title = body.title.strip()
    if body.status is not None and body.status != conv.status:
        changes["status"] = {"from": conv.status, "to": body.status}
        conv.status = body.status
        conv.archived_at = datetime.now(timezone.utc) if body.status == "archived" else None
    conv.updated_at = datetime.now(timezone.utc)
    event = "conversation.archive" if "status" in changes else "conversation.rename"
    audit.record(db, user.actor, event, target_type="conversation", target_id=conv.id, details=changes)
    db.commit()
    return conversation_to_dict(conv)


@router.get("/conversations/{conv_id}/messages", response_model=list[MessageOut], summary="Mesaj geçmişi")
def list_messages(conv_id: str, request: Request, user: AuthenticatedUser = Depends(current_user),
                  scopes: ScopeSet = Depends(scopes_dep), db: Session = Depends(db_dep)) -> list[dict]:
    conv = _get_conv(db, conv_id, user, scopes, request)
    msgs = db.execute(select(Message).where(Message.conversation_id == conv.id).order_by(Message.created_at)).scalars()
    out = [message_to_dict(db, m) for m in msgs]
    audit.record(db, user.actor, "conversation.view", target_type="conversation", target_id=conv.id,
                 details={"messages": len(out)})
    db.commit()
    return out


def _chat_service(settings: Settings, provider: LLMProvider) -> ChatService:
    return ChatService(settings, provider)


@router.post("/conversations/{conv_id}/messages", response_model=ChatTurnOut, summary="Mesaj gönder (tam yanıt)")
def post_message(conv_id: str, body: MessageIn, request: Request, user: AuthenticatedUser = Depends(current_user),
                 scopes: ScopeSet = Depends(scopes_dep), db: Session = Depends(db_dep),
                 settings: Settings = Depends(settings_dep), provider: LLMProvider = Depends(provider_dep)) -> dict:
    conv = _get_conv(db, conv_id, user, scopes, request, write=True)
    if conv.status == "archived":
        raise HTTPException(409, "Arşivlenmiş konuşmaya mesaj eklenemez; önce arşivden çıkarın.")
    atts = [_uuid(a) for a in body.attachment_ids]
    user_msg, assistant = None, None
    for ev in _chat_service(settings, provider).handle(conv.id, user, body.content, atts):
        if ev["event"] == "user_message":
            user_msg = ev["data"]
        elif ev["event"] == "assistant_message":
            assistant = ev["data"]
    return {"user_message": user_msg, "assistant_message": assistant}


@router.post("/conversations/{conv_id}/messages/stream", summary="Mesaj gönder (SSE akışı)",
             response_description="text/event-stream: user_message, status, token, assistant_message, done")
def post_message_stream(conv_id: str, body: MessageIn, request: Request,
                        user: AuthenticatedUser = Depends(current_user), scopes: ScopeSet = Depends(scopes_dep),
                        db: Session = Depends(db_dep), settings: Settings = Depends(settings_dep),
                        provider: LLMProvider = Depends(provider_dep)) -> StreamingResponse:
    conv = _get_conv(db, conv_id, user, scopes, request, write=True)
    if conv.status == "archived":
        raise HTTPException(409, "Arşivlenmiş konuşmaya mesaj eklenemez; önce arşivden çıkarın.")
    atts = [_uuid(a) for a in body.attachment_ids]
    conv_id_val = conv.id
    db.close()

    def gen():
        try:
            for ev in _chat_service(settings, provider).handle(conv_id_val, user, body.content, atts):
                yield f"event: {ev['event']}\ndata: {json.dumps(ev['data'], ensure_ascii=False, default=str)}\n\n"
        except Exception as exc:  # pragma: no cover - surfaced to the client
            payload = {"code": "internal_error", "message": "Beklenmeyen bir hata oluştu; ayrıntı sunucu günlüğünde."}
            import logging

            logging.getLogger(__name__).exception("Akış hatası: %s", exc)
            yield f"event: error\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.post("/conversations/{conv_id}/summarize", summary="Yapılandırılmış konuşma özeti oluştur (doğrulanmamış)")
def summarize(conv_id: str, request: Request, user: AuthenticatedUser = Depends(current_user),
              scopes: ScopeSet = Depends(scopes_dep), db: Session = Depends(db_dep),
              settings: Settings = Depends(settings_dep), provider: LLMProvider = Depends(provider_dep)) -> dict:
    conv = _get_conv(db, conv_id, user, scopes, request)
    res = ChatService(settings, provider).summarize(conv.id, user)
    if res is None:
        raise HTTPException(503, "Özet oluşturulamadı (Ollama erişilemiyor veya konuşma boş).")
    return res


def _get_message(db: Session, message_id: str, user, scopes, request) -> Message:
    m = db.get(Message, _uuid(message_id))
    if m is None:
        raise HTTPException(404, "Mesaj bulunamadı.")
    _get_conv(db, str(m.conversation_id), user, scopes, request)
    return m


@router.get("/messages/{message_id}/sources", summary="Yanıtın kaynak kökeni (açık talep; denetlenir)")
def message_sources(message_id: str, request: Request, user: AuthenticatedUser = Depends(current_user),
                    scopes: ScopeSet = Depends(scopes_dep), db: Session = Depends(db_dep)) -> dict:
    m = _get_message(db, message_id, user, scopes, request)
    rows = db.execute(select(MessageSource).where(MessageSource.message_id == m.id).order_by(MessageSource.rank)).scalars()
    items = [source_to_dict(s) for s in rows]
    audit.record(db, user.actor, "sources.revealed", target_type="message", target_id=m.id,
                 details={"count": len(items), "via": "api"})
    db.commit()
    return {"message_id": str(m.id), "answer_mode": m.answer_mode, "sources": items}


@router.get("/messages/{message_id}/calculation", summary="Ayrıntılı çözüm (hesap izi)")
def message_calculation(message_id: str, request: Request, user: AuthenticatedUser = Depends(current_user),
                        scopes: ScopeSet = Depends(scopes_dep), db: Session = Depends(db_dep)) -> dict:
    m = _get_message(db, message_id, user, scopes, request)
    calc_id = (m.metadata_ or {}).get("calculation_id")
    if not calc_id:
        raise HTTPException(404, "Bu mesaja bağlı hesap yok.")
    c = db.get(Calculation, uuid.UUID(calc_id))
    return {"id": str(c.id), "calc_type": c.calc_type, "status": c.status, "engine_version": c.engine_version,
            "result": c.result, "inputs": c.inputs, "llm_draft": c.llm_draft, "comparison": c.comparison,
            "mismatch": c.mismatch, "created_at": c.created_at.isoformat()}
