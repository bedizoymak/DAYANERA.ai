"""Recommendation Markdown notes (agent-notes/ only).

DAYANERA may write development advice ONLY as separate Markdown files in the
configured AGENT_NOTES_PATH, named ``YYYY-MM-DD_kisa-konu.md``. This module
has no capability to modify source code; every write is path-checked and
audited.
"""
from __future__ import annotations

import hashlib
import os
import re
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.paths import fs, safe_join, slugify
from app.db.models import RecommendationNote
from app.domain.enums import NOTE_STATUSES
from app.services import audit
from app.services.audit import Actor

FILENAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_[a-z0-9][a-z0-9-]{0,80}\.md$")
SECTIONS = [
    ("recommendation", "Öneri"),
    ("rationale", "Gerekçe"),
    ("affected_areas", "Etkilenen alanlar"),
    ("expected_benefit", "Beklenen fayda"),
    ("risks", "Riskler / varsayımlar"),
]


class NoteError(ValueError):
    pass


def render(title: str, day: str, author: str, status: str, fields: dict[str, str]) -> str:
    out = [f"# {title}", "", f"- **Tarih:** {day}", f"- **Yazar/Kaynak:** {author}", f"- **Durum:** {status}", ""]
    for key, heading in SECTIONS:
        out += [f"## {heading}", "", (fields.get(key) or "Belirtilmedi.").strip(), ""]
    out += ["---", "_Bu dosya DAYANERA.ai tarafından yalnızca öneri amacıyla oluşturuldu; kaynak kodu değiştirmez._", ""]
    return "\n".join(out)


def parse(md: str) -> dict[str, str]:
    data: dict[str, str] = {}
    m = re.search(r"^# (.+)$", md, re.M)
    data["title"] = m.group(1).strip() if m else ""
    for label, key in (("Tarih", "date"), ("Yazar/Kaynak", "author"), ("Durum", "status")):
        mm = re.search(rf"^- \*\*{re.escape(label)}:\*\* (.+)$", md, re.M)
        data[key] = mm.group(1).strip() if mm else ""
    for key, heading in SECTIONS:
        mm = re.search(rf"^## {re.escape(heading)}\n\n(.*?)(?=\n## |\n---|\Z)", md, re.S | re.M)
        data[key] = mm.group(1).strip() if mm else ""
    return data


class NotesService:
    def __init__(self, settings: Settings):
        self.dir = settings.agent_notes_path

    def _path(self, filename: str):
        if not FILENAME_RE.match(filename):
            raise NoteError("Geçersiz not dosya adı (beklenen: YYYY-AA-GG_kisa-konu.md).")
        return safe_join(self.dir, filename)

    def _unique_filename(self, day: str, title: str) -> str:
        base = f"{day}_{slugify(title, 60)}"
        name = f"{base}.md"
        i = 2
        while os.path.exists(fs(self.dir / name)):
            name = f"{base}-{i}.md"
            i += 1
        return name

    def create(self, db: Session, actor: Actor, *, title: str, author: str, status: str = "öneri",
               fields: dict[str, str]) -> RecommendationNote:
        if status not in NOTE_STATUSES:
            raise NoteError(f"Geçersiz durum: {status}")
        if not title.strip():
            raise NoteError("Başlık zorunludur.")
        day = date.today().isoformat()
        os.makedirs(fs(self.dir), exist_ok=True)
        filename = self._unique_filename(day, title)
        content = render(title.strip(), day, author, status, fields)
        path = self._path(filename)
        with open(fs(path), "x", encoding="utf-8") as f:
            f.write(content)
        note = RecommendationNote(filename=filename, title=title.strip()[:300], status=status, author=author[:200],
                                  created_by=actor.user_id, sha256=hashlib.sha256(content.encode()).hexdigest())
        db.add(note)
        db.flush()
        audit.record(db, actor, "advice_note.create", target_type="advice_note", target_id=filename,
                     details={"title": note.title, "status": status, "author": author})
        return note

    def update(self, db: Session, actor: Actor, filename: str, *, status: str | None = None,
               fields: dict[str, str] | None = None, title: str | None = None) -> RecommendationNote:
        path = self._path(filename)
        if not os.path.exists(fs(path)):
            raise NoteError("Not bulunamadı.")
        with open(fs(path), encoding="utf-8") as f:
            current = parse(f.read())
        if status is not None and status not in NOTE_STATUSES:
            raise NoteError(f"Geçersiz durum: {status}")
        new_fields = {k: current.get(k, "") for k, _ in SECTIONS}
        new_fields.update({k: v for k, v in (fields or {}).items() if k in new_fields and v is not None})
        new_title = (title or current["title"]).strip()
        new_status = status or current["status"] or "öneri"
        content = render(new_title, current["date"] or date.today().isoformat(), current["author"] or "bilinmiyor",
                         new_status, new_fields)
        with open(fs(path), "w", encoding="utf-8") as f:
            f.write(content)
        note = db.execute(select(RecommendationNote).where(RecommendationNote.filename == filename)).scalar_one_or_none()
        if note is None:
            note = RecommendationNote(filename=filename, title=new_title, status=new_status, author=current["author"] or "?",
                                      created_by=actor.user_id, sha256="")
            db.add(note)
        note.title, note.status = new_title[:300], new_status
        note.sha256 = hashlib.sha256(content.encode()).hexdigest()
        note.updated_at = datetime.now(timezone.utc)
        db.flush()
        audit.record(db, actor, "advice_note.update", target_type="advice_note", target_id=filename,
                     details={"status": new_status, "changed_fields": sorted((fields or {}).keys())})
        return note

    def list(self, db: Session) -> list[dict]:
        indexed = {n.filename: n for n in db.execute(select(RecommendationNote)).scalars()}
        items = []
        if os.path.isdir(fs(self.dir)):
            for name in sorted(os.listdir(fs(self.dir)), reverse=True):
                if not FILENAME_RE.match(name):
                    continue
                n = indexed.get(name)
                with open(fs(self.dir / name), encoding="utf-8") as f:
                    meta = parse(f.read())
                items.append({"filename": name, "title": meta["title"] or (n.title if n else name),
                              "status": meta["status"] or (n.status if n else ""), "author": meta["author"],
                              "date": meta["date"], "indexed": n is not None})
        return items

    def read(self, filename: str) -> dict:
        path = self._path(filename)
        if not os.path.exists(fs(path)):
            raise NoteError("Not bulunamadı.")
        with open(fs(path), encoding="utf-8") as f:
            md = f.read()
        return {"filename": filename, "markdown": md, **parse(md)}
