"""Persistent memory, conversation history and recommendation notes."""
from __future__ import annotations

import json
import os
import re
from datetime import date

import pytest

from conftest import audit_rows


def test_chat_history_is_persisted_and_conversation_lifecycle_audited(admin):
    conv = admin.post("/conversations", {}).json()
    cid = conv["id"]
    admin.post(f"/conversations/{cid}/messages", {"content": "Merhaba, bugün hangi testleri yapıyoruz?"})
    msgs = admin.get(f"/conversations/{cid}/messages").json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "Merhaba, bugün hangi testleri yapıyoruz?"
    title = admin.get(f"/conversations/{cid}").json()["title"]
    assert title.startswith("Merhaba, bugün")  # auto-title from first message
    assert admin.patch(f"/conversations/{cid}", {"title": "Test planı"}).json()["title"] == "Test planı"
    assert admin.patch(f"/conversations/{cid}", {"status": "archived"}).json()["status"] == "archived"
    assert admin.post(f"/conversations/{cid}/messages", {"content": "x"}).status_code == 409
    assert cid in [c["id"] for c in admin.get("/conversations?status=archived").json()]
    assert audit_rows("conversation.rename", target_id=cid) and audit_rows("conversation.archive", target_id=cid)
    assert audit_rows("conversation.view", target_id=cid)
    assert len(audit_rows("chat.message", target_id=cid)) == 2


def test_memory_command_creates_user_confirmed_fact_and_is_searchable(admin):
    cid = admin.post("/conversations", {}).json()["id"]
    a = admin.post(f"/conversations/{cid}/messages",
                   {"content": "hatırla: pinyon malzemesi projede 18CrNiMo7-6 olarak seçildi"}).json()["assistant_message"]
    assert a["answer_mode"] == "general" and "Kaydettim" in a["content"]
    mid = a["metadata"]["memory_item_id"]
    items = admin.get("/memory/items?status=user_confirmed").json()
    item = next(i for i in items if i["id"] == mid)
    assert item["kind"] == "fact" and item["source_conversation_id"] == cid
    res = admin.get("/memory/search?q=pinyon malzemesi").json()
    assert any(m["id"] == mid for m in res["memory"])
    assert any(m["conversation_id"] == cid for m in res["messages"])  # raw chat is searchable too
    assert audit_rows("memory.create", target_id=mid)


def test_memory_update_supersedes_and_keeps_original(admin):
    m = admin.post("/memory/items", {"kind": "preference", "title": "Birim tercihi", "content": "Uzunlukları mm yaz"}).json()
    new = admin.patch(f"/memory/items/{m['id']}", {"content": "Uzunlukları mm, toleransları µm yaz"}).json()
    assert new["id"] != m["id"] and new["supersedes_id"] == m["id"]
    chain = admin.get(f"/memory/items/{new['id']}/history").json()
    assert [c["status"] for c in chain] == ["user_confirmed", "superseded"]
    assert chain[1]["content"] == "Uzunlukları mm yaz"
    assert admin.patch(f"/memory/items/{m['id']}", {"content": "x"}).status_code == 409
    assert audit_rows("memory.update", target_id=new["id"])


def test_memory_is_scoped_by_user(admin, member_factory):
    private = admin.post("/memory/items", {"title": "Gizli yönetici notu", "content": "yalnızca yönetici görmeli zeytinyağı"}).json()
    member = member_factory()
    assert private["id"] not in [i["id"] for i in member.get("/memory/items").json()]
    assert member.get("/memory/search?q=zeytinyağı").json()["memory"] == []
    assert member.patch(f"/memory/items/{private['id']}", {"content": "x"}).status_code == 403
    own = member.post("/memory/items", {"title": "Üye notu", "content": "üye kendi notunu görür"}).json()
    assert own["id"] in [i["id"] for i in member.get("/memory/items").json()]
    assert own["id"] in [i["id"] for i in admin.get("/memory/items").json()]  # owner sees all


def test_general_chat_uses_confirmed_memory_as_unverified_context(admin, fake_llm):
    admin.post("/memory/items", {"kind": "preference", "title": "Hitap", "content": "Bana her zaman 'Usta' diye hitap et"})
    cid = admin.post("/conversations", {}).json()["id"]
    admin.post(f"/conversations/{cid}/messages", {"content": "Selam, nasıl hitap etmelisin?"})
    system = fake_llm.calls[-1][0].content
    assert "Usta" in system and "doğrulanmış teknik kaynak değildir" in system


def test_conversation_summary_is_unverified_and_raw_messages_remain(admin, fake_llm):
    cid = admin.post("/conversations", {}).json()["id"]
    admin.post(f"/conversations/{cid}/messages", {"content": "Merhaba, dişli bakım planını konuşalım."})
    fake_llm.responder = lambda m: json.dumps({"ozet": "Kullanıcı bakım planını konuştu.", "konular": ["bakım"],
                                               "kullanici_tercihleri": [], "acik_sorular": []})
    s = admin.post(f"/conversations/{cid}/summarize").json()
    item = next(i for i in admin.get("/memory/items?kind=conversation_summary").json() if i["id"] == s["id"])
    assert item["status"] == "unverified" and item["source_conversation_id"] == cid
    assert len(admin.get(f"/conversations/{cid}/messages").json()) == 2


# ------------------------------------------------------------ notes ------
def test_recommendation_note_created_only_in_agent_notes_with_contract_sections(admin, settings):
    r = admin.post("/notes", {"title": "Dişli hesap modülü", "recommendation": "ISO 6336 mukavemet hesabı eklenmeli.",
                              "rationale": "Kullanıcılar sık soruyor.", "affected_areas": "backend/app/calc",
                              "expected_benefit": "Daha az ret.", "risks": "Standart korpusta yok."})
    assert r.status_code == 201, r.text
    note = r.json()
    assert re.fullmatch(rf"{date.today().isoformat()}_disli-hesap-modulu(-\d+)?\.md", note["filename"])
    path = settings.agent_notes_path / note["filename"]
    text = path.read_text(encoding="utf-8")
    for heading in ("## Öneri", "## Gerekçe", "## Etkilenen alanlar", "## Beklenen fayda", "## Riskler / varsayımlar"):
        assert heading in text
    assert "- **Tarih:**" in text and "- **Yazar/Kaynak:**" in text and "- **Durum:** öneri" in text
    upd = admin.patch(f"/notes/{note['filename']}", {"status": "inceleniyor"}).json()
    assert upd["status"] == "inceleniyor" and "- **Durum:** inceleniyor" in path.read_text(encoding="utf-8")
    assert audit_rows("advice_note.create", target_id=note["filename"])
    assert audit_rows("advice_note.update", target_id=note["filename"])
    assert note["filename"] in [n["filename"] for n in admin.get("/notes").json()]


def test_note_status_must_be_contract_value(admin):
    r = admin.post("/notes", {"title": "Geçersiz durum", "recommendation": "x", "status": "tamam"})
    assert r.status_code == 422


def test_notes_cannot_escape_agent_notes_or_touch_source(admin, settings):
    from app.services.notes import NoteError, NotesService

    svc = NotesService(settings)
    for bad in ("../x.md", "..\\x.md", "2026-01-01_../../backend/app/main.py", "main.py", "2026-01-01_ok.txt"):
        with pytest.raises(NoteError):
            svc._path(bad)
    assert admin.get("/notes/..%2F..%2Fbackend%2Fapp%2Fmain.py").status_code in (404, 422)
    before = {p.name for p in settings.project_root.iterdir()}
    admin.post("/notes", {"title": "../../kaçış denemesi", "recommendation": "x"})
    assert {p.name for p in settings.project_root.iterdir()} == before | {"agent-notes"} or \
        {p.name for p in settings.project_root.iterdir()} == before
    assert all(p.parent == settings.agent_notes_path for p in settings.agent_notes_path.glob("*.md"))


def test_chat_can_create_advice_note(admin, settings):
    cid = admin.post("/conversations", {}).json()["id"]
    a = admin.post(f"/conversations/{cid}/messages",
                   {"content": "öneri notu: OCR sonrası değer kuyruğuna toplu onay eklenmeli"}).json()["assistant_message"]
    fn = a["metadata"]["note_filename"]
    assert os.path.exists(settings.agent_notes_path / fn)
    assert "kaynak kodu değiştirmez" in a["content"]
