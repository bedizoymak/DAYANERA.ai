"""Chat orchestration: routes each message through the answer policy.

Modes: Genel sohbet (general) · Doğrulanmış kaynak cevabı (verified_source) ·
Hesap sonucu (calculation) · Taslak çıkarım (draft_extraction) ·
Doğrulanamadı (unverified). Verified answers are validated BEFORE display, so
only general chat streams tokens; other modes stream status updates.
"""
from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from app.calc.engine import CalculationEngine
from app.calc.rules import RULES
from app.calc.types import CalcRequest
from app.core.config import Settings
from app.db.models import (
    Conversation,
    Document,
    DocumentPage,
    DocumentVersion,
    Message,
    MessageAttachment,
    MessageSource,
)
from app.db.session import session_scope
from app.domain.enums import REFUSAL_PHRASE
from app.inference.base import ChatMessage, GenerationOptions, LLMProvider, ProviderError
from app.inference.prompts import general_messages, summary_messages, verified_messages
from app.services import audit, memory
from app.services.access import can_view_document, load_scopes
from app.services.auth import AuthenticatedUser
from app.services.calculations import (
    DbEvidenceResolver,
    InputSpecIn,
    format_result_text,
    llm_draft,
    llm_map_request,
    resolve_inputs,
    run_calculation,
)
from app.services.glossary import annotate_first_use
from app.services.grounding import validate_answer
from app.services.intent import Intent, classify
from app.services.inventory import active_corpus_codes, format_inventory, list_active_documents, missing_codes
from app.services.notes import NotesService
from app.services.retrieval import Passage, plan_query, search
from app.services.serialize import message_to_dict

log = logging.getLogger(__name__)

NEW_TITLE = "Yeni sohbet"

# Structured refusal reasons (metadata.refusal.reason). The message content of
# every refusal remains exactly REFUSAL_PHRASE (master spec §1 rule 3).
REFUSAL_REASONS = ("no_passages", "low_relevance", "unsupported_numbers", "model_refused", "invalid_citation",
                   "empty_answer", "unsupported_calculation", "calculation_refused")


_PARAPHRASED_REFUSAL = re.compile(
    r"(\bbilgi\s+(?:yok|bulunmu?yor|bulunmamaktadır|verilmemiş)"
    r"|(?:pasaj|kaynak)\w*\s+(?:\w+\s+){0,4}?(?:yer\s+alm(?:ıyor|amaktadır)|bulunm(?:uyor|amaktadır)|içerm(?:iyor|emektedir)|yok\b)"
    r"|yanıtlanamaz|cevaplanamaz|yanıt\s+veremem|cevap\s+veremem|doğrulayamıyorum|doğrulanamıyor|doğrulanamamaktadır"
    r"|\bnot\s+(?:mentioned|provided|found|contained|specified)\b|\bdoes\s+not\s+contain\b|\bno\s+information\b)",
    re.IGNORECASE)


def looks_like_refusal(answer: str) -> bool:
    """A model that declines in its own words must still yield the exact refusal phrase."""
    return bool(_PARAPHRASED_REFUSAL.search(answer))


def normalize_refusal_reason(reason: str) -> str:
    base = (reason or "").split(":", 1)[0]  # e.g. "invalid_citation:[7]" -> "invalid_citation"
    base = {"unsupported_formula": "unsupported_calculation"}.get(base, base)
    return base if base in REFUSAL_REASONS else "model_refused"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _msgs(dicts: list[dict]) -> list[ChatMessage]:
    return [ChatMessage(role=d["role"], content=d["content"]) for d in dicts]


class ChatService:
    def __init__(self, settings: Settings, provider: LLMProvider):
        self.settings = settings
        self.provider = provider

    # ------------------------------------------------------------------
    def handle(self, conversation_id: uuid.UUID, user: AuthenticatedUser, content: str,
               attachment_ids: list[uuid.UUID]) -> Iterator[dict[str, Any]]:
        content = content.strip()
        with session_scope() as db:
            conv = db.get(Conversation, conversation_id)
            scopes = load_scopes(db, user)
            msg = Message(conversation_id=conv.id, author_user_id=user.id, role="user", content=content)
            db.add(msg)
            db.flush()
            for doc_id in attachment_ids:
                doc = db.get(Document, doc_id)
                if doc is None or not can_view_document(scopes, doc):
                    continue
                db.add(MessageAttachment(message_id=msg.id, document_id=doc.id,
                                         version_id=doc.current_version_id or self._latest_version_id(db, doc.id)))
            if conv.title == NEW_TITLE and content:
                conv.title = (content[:60] + ("…" if len(content) > 60 else "")).replace("\n", " ")
            conv.updated_at = _now()
            audit.record(db, user.actor, "chat.message", target_type="conversation", target_id=conv.id,
                         details={"message_id": str(msg.id), "role": "user", "chars": len(content),
                                  "attachments": [str(a) for a in attachment_ids]})
            db.flush()
            user_msg_id = msg.id
            user_payload = message_to_dict(db, msg)
        yield {"event": "user_message", "data": user_payload}

        intent = classify(content)
        handler = {
            "sources_only": self._sources_only,
            "memory_command": self._memory_command,
            "note_command": self._note_command,
            "corpus_inventory": self._corpus_inventory,
            "calculation": self._calculation,
            "technical": self._technical,
            "general": self._general,
        }[intent.kind]
        final: dict[str, Any] | None = None
        for ev in handler(conversation_id, user, user_msg_id, intent, attachment_ids):
            if ev["event"] == "assistant_message":
                final = ev["data"]
            yield ev
        if final is not None:
            self._maybe_summarize(conversation_id, user)
        yield {"event": "done", "data": {}}

    # ------------------------------------------------------------------
    @staticmethod
    def _latest_version_id(db, doc_id):
        return db.execute(select(DocumentVersion.id).where(DocumentVersion.document_id == doc_id)
                          .order_by(DocumentVersion.version_number.desc())).scalars().first()

    def _save_assistant(self, conversation_id, user: AuthenticatedUser, reply_to, content: str, mode: str | None, *,
                        status: str = "complete", error_code: str | None = None, show_sources: bool = False,
                        metadata: dict | None = None, model: str | None = None, latency_ms: int | None = None,
                        sources: list[dict] | None = None, db=None) -> dict[str, Any]:
        def _do(db):
            m = Message(conversation_id=conversation_id, author_user_id=None, role="assistant", content=content,
                        answer_mode=mode, status=status, error_code=error_code, show_sources=show_sources,
                        provider="local_ollama" if model else None, model=model, latency_ms=latency_ms,
                        reply_to_message_id=reply_to, metadata_=metadata or {})
            db.add(m)
            db.flush()
            for s in sources or []:
                db.add(MessageSource(message_id=m.id, **s))
            conv = db.get(Conversation, conversation_id)
            conv.updated_at = _now()
            audit.record(db, user.actor, "chat.message", target_type="conversation", target_id=conversation_id,
                         details={"message_id": str(m.id), "role": "assistant", "answer_mode": mode, "status": status})
            if show_sources and sources:
                audit.record(db, user.actor, "sources.revealed", target_type="message", target_id=m.id,
                             details={"count": len(sources)})
            db.flush()
            return message_to_dict(db, m)

        if db is not None:
            return _do(db)
        with session_scope() as s:
            return _do(s)

    def _error(self, conversation_id, user, reply_to, exc: ProviderError) -> dict[str, Any]:
        audit.record_independent(user.actor, "provider.error", outcome="failure", target_type="conversation",
                                 target_id=conversation_id, details={"code": exc.code})
        return self._save_assistant(conversation_id, user, reply_to, exc.user_message, "unverified", status="error",
                                    error_code=exc.code)

    # ------------------------------------------------------------------
    def _sources_only(self, conversation_id, user, user_msg_id, intent: Intent, _atts) -> Iterator[dict]:
        with session_scope() as db:
            prev = db.execute(
                select(Message).where(Message.conversation_id == conversation_id, Message.role == "assistant")
                .order_by(Message.created_at.desc()).limit(20)
            ).scalars().all()
            target = None
            for m in prev:
                has = db.execute(select(func.count()).select_from(MessageSource)
                                 .where(MessageSource.message_id == m.id)).scalar()
                if m.answer_mode in ("verified_source", "calculation") and has:
                    target = m
                    break
                if m.answer_mode in ("general", "unverified", "draft_extraction"):
                    break
            if target is None:
                last_mode = prev[0].answer_mode if prev else None
                text = ("Önceki yanıt genel sohbetti veya doğrulanamadı; dayandığı doğrulanmış bir ISO kaynağı yok."
                        if last_mode else "Bu konuşmada kaynak gösterilecek doğrulanmış bir yanıt yok.")
                payload = self._save_assistant(conversation_id, user, user_msg_id, text, "general",
                                               metadata={"reason": "no_sources"}, db=db)
            else:
                rows = db.execute(select(MessageSource).where(MessageSource.message_id == target.id)
                                  .order_by(MessageSource.rank)).scalars().all()
                copies = [{"document_id": r.document_id, "version_id": r.version_id, "chunk_id": r.chunk_id,
                           "rank": r.rank, "score": r.score, "page_number": r.page_number, "locator": r.locator,
                           "excerpt": r.excerpt, "excerpt_start": r.excerpt_start, "excerpt_end": r.excerpt_end,
                           "document_title": r.document_title, "standard_code": r.standard_code,
                           "version_number": r.version_number, "confidence_status": r.confidence_status,
                           "cited": r.cited} for r in rows]
                lines = ["Önceki yanıtın dayandığı yerel kaynaklar:"]
                for r in rows:
                    lines.append(f"{r.rank}. {r.standard_code or r.document_title} — {r.locator} (sürüm {r.version_number})")
                payload = self._save_assistant(conversation_id, user, user_msg_id, "\n".join(lines), target.answer_mode,
                                               show_sources=True, sources=copies,
                                               metadata={"sources_for_message_id": str(target.id),
                                                         **({"calculation_id": target.metadata_.get("calculation_id")}
                                                            if target.metadata_.get("calculation_id") else {})},
                                               db=db)
        yield {"event": "assistant_message", "data": payload}

    def _memory_command(self, conversation_id, user, user_msg_id, intent: Intent, _atts) -> Iterator[dict]:
        text = intent.memory_text or ""
        kind = "preference" if memory.PREFERENCE_HINT.search(text) else "fact"
        with session_scope() as db:
            item = memory.create_item(db, user, kind=kind, title=text[:80], content=text, status="user_confirmed",
                                      structured={"origin": "chat_command"}, source_conversation_id=conversation_id,
                                      source_message_id=user_msg_id)
            reply = (f"Kaydettim ({'tercih' if kind == 'preference' else 'bilgi'}, kullanıcı onaylı hafıza): “{text}”. "
                     "Bu kayıt doğrulanmış ISO kaynağı değildir; hafıza sayfasından düzenleyebilirsiniz.")
            payload = self._save_assistant(conversation_id, user, user_msg_id, reply, "general",
                                           metadata={"memory_item_id": str(item.id)}, db=db)
        yield {"event": "assistant_message", "data": payload}

    def _note_command(self, conversation_id, user, user_msg_id, intent: Intent, _atts) -> Iterator[dict]:
        text = intent.note_text or ""
        title = re.split(r"[.\n]", text, maxsplit=1)[0][:80] or "Öneri"
        with session_scope() as db:
            note = NotesService(self.settings).create(
                db, user.actor, title=title, author=f"DAYANERA ({user.username} isteğiyle)", status="öneri",
                fields={"recommendation": text, "rationale": "Kullanıcı sohbet üzerinden öneri notu istedi.",
                        "affected_areas": "Belirtilmedi.", "expected_benefit": "Belirtilmedi.",
                        "risks": "Öneri henüz incelenmedi; kaynak kod değişikliği ayrı MCP/ajan iş akışıyla yapılır."})
            reply = (f"Öneri notu oluşturuldu: `agent-notes/{note.filename}` (durum: öneri). "
                     "DAYANERA kaynak kodu değiştirmez; not yalnızca inceleme içindir.")
            payload = self._save_assistant(conversation_id, user, user_msg_id, reply, "general",
                                           metadata={"note_filename": note.filename}, db=db)
        yield {"event": "assistant_message", "data": payload}

    def _corpus_inventory(self, conversation_id, user, user_msg_id, intent: Intent, _atts) -> Iterator[dict]:
        """List active documents visible to the user. Deterministic, no LLM call (Step 2 Order A)."""
        t0 = time.perf_counter()
        with session_scope() as db:
            scopes = load_scopes(db, user)
            docs = list_active_documents(db, scopes)
            text = format_inventory(docs)
            elapsed = int((time.perf_counter() - t0) * 1000)
            verified = sum(1 for d in docs if d.verified_corpus)
            audit.record(db, user.actor, "corpus.inventory", target_type="conversation", target_id=conversation_id,
                         details={"verified_documents": verified, "other_documents": len(docs) - verified})
            payload = self._save_assistant(
                conversation_id, user, user_msg_id, text, "general",
                metadata={"kind": "corpus_inventory", "document_count": len(docs),
                          "verified_document_count": verified, "timings_ms": {"inventory": elapsed}},
                db=db)
        yield {"event": "assistant_message", "data": payload}

    # ------------------------------------------------------------------
    def _refusal_meta(self, user, question: str, reason: str, available_codes: list[str] | None = None) -> dict:
        """Structured, non-content refusal detail (Step 2 Order B). Content stays REFUSAL_PHRASE."""
        if available_codes is None:
            with session_scope() as db:
                available_codes = active_corpus_codes(db, load_scopes(db, user))
        requested, missing = missing_codes(question, available_codes)
        return {"reason": normalize_refusal_reason(reason), "codes_requested": requested, "codes_missing": missing}

    def _refuse(self, conversation_id, user, user_msg_id, reason: str, details: dict | None = None, *,
                question: str = "", available_codes: list[str] | None = None, event: str = "answer.refused",
                model: str | None = None, latency_ms: int | None = None) -> dict:
        refusal = self._refusal_meta(user, question, reason, available_codes)
        audit.record_independent(user.actor, event, outcome="info", target_type="conversation",
                                 target_id=conversation_id,
                                 details={**(details or {}), "reason": refusal["reason"],
                                          "codes_missing": refusal["codes_missing"]})
        return self._save_assistant(conversation_id, user, user_msg_id, REFUSAL_PHRASE, "unverified",
                                    metadata={**(details or {}), "reason": refusal["reason"], "refusal": refusal},
                                    model=model, latency_ms=latency_ms)

    def _calculation(self, conversation_id, user, user_msg_id, intent: Intent, _atts) -> Iterator[dict]:
        parsed = intent.calc
        calc_type = parsed.calc_type if parsed else None
        raw_inputs = dict(parsed.inputs) if parsed else {}
        assumptions = list(parsed.assumed_units) if parsed else []
        mapping = "deterministic"
        if not calc_type:
            yield {"event": "status", "data": {"stage": "mapping", "text": "Hesap isteği yerel modelle yapılandırılıyor…"}}
            try:
                calc_type, raw_inputs = llm_map_request(self.provider, intent.question)
                mapping = "qwen"
            except ProviderError:
                calc_type = None
        if not calc_type:
            yield {"event": "assistant_message",
                   "data": self._refuse(conversation_id, user, user_msg_id, "unsupported_formula",
                                        {"calc_refusal": "Bu hesap türü doğrulanmış kaynaklarla desteklenmiyor."},
                                        question=intent.question)}
            return
        specs = {s.key: s for s in RULES[calc_type].inputs}
        raw_inputs = {k: v for k, v in raw_inputs.items() if k in specs}
        # 1) engine pre-run (fast, no persistence) to decide whether a Qwen draft is useful
        with session_scope() as db:
            scopes = load_scopes(db, user)
            inputs = resolve_inputs(db, scopes, {k: InputSpecIn(value=v, unit=u) for k, (v, u) in raw_inputs.items()},
                                    user_msg_id)
            pre = CalculationEngine(DbEvidenceResolver(db, scopes)).run(CalcRequest(calc_type, inputs))
        draft_record = None
        if pre.status == "ok" and self.settings.calc_llm_compare:
            yield {"event": "status", "data": {"stage": "llm_draft", "text": "Qwen taslak hesabı hazırlanıyor…"}}
            try:
                draft_record = llm_draft(self.provider, calc_type, inputs)
            except ProviderError as exc:
                draft_record = {"error": exc.code, "message": exc.user_message}
        yield {"event": "status", "data": {"stage": "engine", "text": "Deterministik hesap motoru doğruluyor…"}}
        with session_scope() as db:
            scopes = load_scopes(db, user)
            calc, result, comparison = run_calculation(
                db, user, scopes, calc_type, inputs, provider=None, compare_with_llm=False,
                conversation_id=conversation_id, draft_record=draft_record, extra_assumptions=assumptions)
            content = format_result_text(result, comparison)
            mode = "calculation" if result.status == "ok" else "unverified"
            sources = []
            for i, ev in enumerate(result.evidence, start=1):
                sources.append({"document_id": uuid.UUID(ev["document_id"]), "version_id": uuid.UUID(ev["version_id"]),
                                "chunk_id": None, "rank": i, "score": 1.0, "page_number": ev["page_number"],
                                "locator": ev["locator"], "excerpt": ev["excerpt"][:1500], "excerpt_start": 0,
                                "excerpt_end": len(ev["excerpt"]), "document_title": ev["document_title"],
                                "standard_code": ev["standard_code"], "version_number": ev["version_number"],
                                "confidence_status": ev["confidence_status"], "cited": True})
            metadata = {"calculation_id": str(calc.id), "calc_type": calc_type, "calc_status": result.status,
                        "mismatch": calc.mismatch, "detail_open": intent.wants_detail, "mapping": mapping}
            if result.status == "refused":
                refusal = self._refusal_meta(user, intent.question, "calculation_refused",
                                             active_corpus_codes(db, scopes))
                metadata.update(reason=refusal["reason"], refusal=refusal)
                audit.record(db, user.actor, "answer.refused", outcome="info", target_type="calculation",
                             target_id=calc.id, details={"reason": refusal["reason"],
                                                         "codes_missing": refusal["codes_missing"]})
            payload = self._save_assistant(
                conversation_id, user, user_msg_id, content, mode,
                show_sources=intent.wants_sources and result.status == "ok",
                sources=sources if result.status == "ok" else [],
                metadata=metadata, db=db)
            calc.message_id = uuid.UUID(payload["id"])
            if result.status == "ok" and sources:
                audit.record(db, user.actor, "source.used", target_type="calculation", target_id=calc.id,
                             details={"sources": [{"doc": str(s["document_id"]), "page": s["page_number"]} for s in sources]})
        yield {"event": "assistant_message", "data": payload}

    # ------------------------------------------------------------------
    def _previous_user_question(self, conversation_id, user_msg_id) -> str | None:
        with session_scope() as db:
            m = db.execute(select(Message).where(Message.conversation_id == conversation_id, Message.role == "user",
                                                 Message.id != user_msg_id)
                           .order_by(Message.created_at.desc()).limit(1)).scalars().first()
            return m.content[:300] if m else None

    def _technical(self, conversation_id, user, user_msg_id, intent: Intent, _atts) -> Iterator[dict]:
        yield {"event": "status", "data": {"stage": "retrieval", "text": "Doğrulanmış ISO kaynakları aranıyor…"}}
        t_start = time.perf_counter()
        timings: dict[str, int] = {}

        def ms(t0: float) -> int:
            return int((time.perf_counter() - t0) * 1000)

        prev_q = self._previous_user_question(conversation_id, user_msg_id)
        with session_scope() as db:
            scopes = load_scopes(db, user)
            available = active_corpus_codes(db, scopes)
        # Order C.1: every ISO code the question names is absent -> refuse now (no retrieval, no LLM)
        requested, missing = missing_codes(intent.question, available)
        if requested and len(missing) == len(requested):
            timings.update(retrieval=0, total=ms(t_start))
            yield {"event": "assistant_message",
                   "data": self._refuse(conversation_id, user, user_msg_id, "no_passages",
                                        {"short_circuit": "codes_missing", "timings_ms": timings},
                                        question=intent.question, available_codes=available)}
            return
        t0 = time.perf_counter()
        min_cov = self.settings.retrieval_min_coverage
        with session_scope() as db:
            scopes = load_scopes(db, user)
            plan = plan_query(intent.question)
            passages = search(db, scopes, plan, top_k=self.settings.retrieval_top_k, min_coverage=min_cov)
            if not passages and prev_q:
                plan = plan_query(intent.question, context=prev_q)
                passages = search(db, scopes, plan, top_k=self.settings.retrieval_top_k, min_coverage=min_cov)
        timings["retrieval"] = ms(t0)
        plan_info = {"concepts": plan.concepts, "codes": plan.codes, "literals": plan.literals}
        if not passages:
            timings["total"] = ms(t_start)
            yield {"event": "assistant_message",
                   "data": self._refuse(conversation_id, user, user_msg_id, "no_passages",
                                        {"plan": plan_info, "timings_ms": timings},
                                        question=intent.question, available_codes=available)}
            return
        # Order C.2: relevance gate on the absolute passage score, before any generation
        best = max(p.score for p in passages)
        if best < self.settings.retrieval_min_score:
            timings["total"] = ms(t_start)
            yield {"event": "assistant_message",
                   "data": self._refuse(conversation_id, user, user_msg_id, "low_relevance",
                                        {"plan": plan_info, "best_score": best,
                                         "min_score": self.settings.retrieval_min_score, "timings_ms": timings},
                                        question=intent.question, available_codes=available)}
            return
        # focused excerpts around the matched concepts keep the CPU prompt small
        budget = self.settings.retrieval_max_context_chars
        used: list[Passage] = []
        total = 0
        for p in passages:
            if total >= budget:
                break
            p.focus(plan, size=min(900, max(400, budget - total)))
            used.append(p)
            total += len(p.text)
        yield {"event": "status", "data": {"stage": "generation", "text": "Yerel model kaynaklara dayalı yanıt hazırlıyor…"}}
        pdicts = [{"standard_code": p.standard_code, "title": p.document_title, "locator": p.locator, "text": p.text}
                  for p in used]
        t0 = time.perf_counter()
        try:
            res = self.provider.chat(
                _msgs(verified_messages(intent.question, pdicts, intent.wants_detail,
                                        prev_q if prev_q and len(intent.question) < 60 else None)),
                # Order C.3: short answers are capped at 250 tokens (detail mode keeps 900)
                GenerationOptions(temperature=0.1, num_predict=900 if intent.wants_detail else 250))
        except ProviderError as exc:
            yield {"event": "assistant_message", "data": self._error(conversation_id, user, user_msg_id, exc)}
            return
        timings["generation"] = ms(t0)
        yield {"event": "status", "data": {"stage": "validation", "text": "Yanıt kaynak pasajlarına göre doğrulanıyor…"}}
        t0 = time.perf_counter()
        extra = [f"{p.standard_code or ''} {p.document_title} {p.locator}" for p in used]
        g = validate_answer(res.content, [p.text for p in used], intent.question, extra_allowed=extra)
        if g.accepted and looks_like_refusal(g.text):
            g.accepted, g.text, g.reason = False, REFUSAL_PHRASE, "model_refused"
        timings["validation"] = ms(t0)
        timings["total"] = ms(t_start)
        retrieved_meta = [{"chunk_id": str(p.chunk_id), "document_id": str(p.document_id), "version_id": str(p.version_id),
                           "page": p.page_number, "score": p.score} for p in used]
        if not g.accepted:
            payload = self._refuse(conversation_id, user, user_msg_id, g.reason or "model_refused",
                                   {"retrieved": retrieved_meta, "plan": plan_info,
                                    "unsupported_numbers": g.unsupported_numbers, "timings_ms": timings},
                                   question=intent.question, available_codes=available,
                                   event="answer.grounding_failed", model=res.model, latency_ms=res.latency_ms)
            yield {"event": "assistant_message", "data": payload}
            return
        g.text = annotate_first_use(g.text)  # English term on first use of specialist terms
        cited = set(g.cited)
        sources = [{"document_id": p.document_id, "version_id": p.version_id, "chunk_id": p.chunk_id, "rank": i,
                    "score": p.score, "page_number": p.page_number, "locator": p.locator, "excerpt": p.text,
                    "excerpt_start": p.excerpt_start, "excerpt_end": p.excerpt_end, "document_title": p.document_title,
                    "standard_code": p.standard_code, "version_number": p.version_number,
                    "confidence_status": p.confidence_status, "cited": (i in cited) or not cited}
                   for i, p in enumerate(used, start=1)]
        with session_scope() as db:
            payload = self._save_assistant(conversation_id, user, user_msg_id, g.text, "verified_source",
                                           show_sources=intent.wants_sources, sources=sources,
                                           metadata={"retrieved": retrieved_meta, "plan": plan_info,
                                                     "detail_open": intent.wants_detail, "timings_ms": timings},
                                           model=res.model, latency_ms=res.latency_ms, db=db)
            audit.record(db, user.actor, "source.used", target_type="message", target_id=payload["id"],
                         details={"sources": [{"doc": str(s["document_id"]), "version": str(s["version_id"]),
                                               "page": s["page_number"], "score": s["score"]} for s in sources]})
        yield {"event": "assistant_message", "data": payload}

    # ------------------------------------------------------------------
    def _attachment_context(self, db, attachment_ids, scopes) -> tuple[list[str], bool, list[str]]:
        ctx, draft_used, pending = [], False, []
        for doc_id in attachment_ids:
            doc = db.get(Document, doc_id)
            if doc is None or not can_view_document(scopes, doc):
                continue
            ver_id = doc.current_version_id or self._latest_version_id(db, doc.id)
            ver = db.get(DocumentVersion, ver_id) if ver_id else None
            if ver is None:
                continue
            if ver.ingestion_status in ("queued", "processing"):
                pending.append(doc.original_filename)
                continue
            pages = db.execute(select(DocumentPage).where(DocumentPage.version_id == ver.id)
                               .order_by(DocumentPage.page_number).limit(8)).scalars().all()
            if not pages:
                reason = (ver.extraction_summary or {}).get("stored_only_reason") or "İçerik çıkarılamadı."
                ctx.append(f"[{doc.original_filename}] Yalnızca arşivlendi: {reason}")
                continue
            body = "\n".join(p.text for p in pages)[:4000]
            label = "TASLAK ÇIKARIM (OCR/döküm)" if any(p.confidence_status == "draft_extraction" for p in pages) \
                else "yerel metin çıkarımı (doğrulanmamış)"
            draft_used = draft_used or "TASLAK" in label
            ctx.append(f"[{doc.original_filename} — {label}]\n{body}")
        return ctx, draft_used, pending

    def _wait_for_attachments(self, attachment_ids) -> Iterator[dict]:
        if not attachment_ids:
            return
        deadline = time.monotonic() + 90
        announced = False
        while time.monotonic() < deadline:
            with session_scope() as db:
                pending = 0
                for doc_id in attachment_ids:
                    doc = db.get(Document, doc_id)
                    vid = doc.current_version_id or self._latest_version_id(db, doc.id) if doc else None
                    ver = db.get(DocumentVersion, vid) if vid else None
                    if ver is not None and ver.ingestion_status in ("queued", "processing"):
                        pending += 1
            if not pending:
                return
            if not announced:
                yield {"event": "status", "data": {"stage": "attachments", "text": "Ek dosya(lar) yerel olarak işleniyor…"}}
                announced = True
            time.sleep(1.0)

    def _general(self, conversation_id, user, user_msg_id, intent: Intent, attachment_ids) -> Iterator[dict]:
        yield from self._wait_for_attachments(attachment_ids)
        with session_scope() as db:
            scopes = load_scopes(db, user)
            hist = db.execute(select(Message).where(Message.conversation_id == conversation_id,
                                                    Message.id != user_msg_id, Message.status == "complete")
                              .order_by(Message.created_at.desc()).limit(10)).scalars().all()
            history = [{"role": m.role, "content": m.content[:1500]} for m in reversed(hist) if m.role in ("user", "assistant")]
            notes = memory.relevant_notes(db, user, intent.question)
            att_ctx, draft_used, pending = self._attachment_context(db, attachment_ids, scopes)
            user_text = db.get(Message, user_msg_id).content
        yield {"event": "status", "data": {"stage": "generation", "text": "Yerel model yanıtlıyor…"}}
        tokens: list[str] = []
        t0 = time.perf_counter()
        try:
            for piece in self.provider.stream_chat(_msgs(general_messages(history, user_text, notes, att_ctx)),
                                                   GenerationOptions(temperature=0.5)):
                tokens.append(piece)
                yield {"event": "token", "data": {"text": piece}}
        except ProviderError as exc:
            yield {"event": "assistant_message", "data": self._error(conversation_id, user, user_msg_id, exc)}
            return
        content = "".join(tokens).strip() or "…"
        if draft_used:
            content += ("\n\n_Not: Ek dosyadan aktarılan bilgiler **Taslak çıkarım**dır (OCR/döküm). Hesapta veya "
                        "doğrulanmış bilgi olarak kullanmak için İnceleme kuyruğunda onaylayın._")
        if intent.wants_sources:
            content += "\n\n_Bu yanıt genel sohbettir; doğrulanmış bir ISO kaynağına dayanmadığı için gösterilecek kaynak yok._"
        if pending:
            content += f"\n\n_Henüz işlenmekte olan ekler: {', '.join(pending)}._"
        payload = self._save_assistant(conversation_id, user, user_msg_id, content,
                                       "draft_extraction" if draft_used else "general",
                                       metadata={"draft_used": draft_used, "attachments_pending": pending},
                                       model=getattr(self.provider, "model", None),
                                       latency_ms=int((time.perf_counter() - t0) * 1000))
        yield {"event": "assistant_message", "data": payload}

    # ------------------------------------------------------------------
    def _maybe_summarize(self, conversation_id, user: AuthenticatedUser) -> None:
        n = self.settings.summary_every_n_messages
        if n <= 0:
            return
        with session_scope() as db:
            count = db.execute(select(func.count()).select_from(Message)
                               .where(Message.conversation_id == conversation_id)).scalar()
        if count and count % n == 0:
            threading.Thread(target=self.summarize, args=(conversation_id, user), daemon=True).start()

    def summarize(self, conversation_id, user: AuthenticatedUser) -> dict | None:
        """Create a structured, unverified conversation summary (raw messages are kept)."""
        import json

        with session_scope() as db:
            msgs = db.execute(select(Message).where(Message.conversation_id == conversation_id)
                              .order_by(Message.created_at)).scalars().all()
            transcript = "\n".join(f"{m.role}: {m.content[:800]}" for m in msgs)
            last_id = msgs[-1].id if msgs else None
        if not transcript:
            return None
        try:
            res = self.provider.chat(_msgs(summary_messages(transcript)),
                                     GenerationOptions(temperature=0.1, json_mode=True, num_predict=500))
        except ProviderError:
            return None
        try:
            data = json.loads(res.content)
        except json.JSONDecodeError:
            data = {"ozet": res.content[:2000]}
        with session_scope() as db:
            item = memory.create_item(db, user, kind="conversation_summary", title="Konuşma özeti",
                                      content=str(data.get("ozet", ""))[:4000], status="unverified",
                                      structured={**data, "covers_until_message_id": str(last_id),
                                                  "message_count": len(msgs)},
                                      source_conversation_id=conversation_id)
            return {"id": str(item.id), "summary": item.content}
