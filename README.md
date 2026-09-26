# DAYANERA.ai — local-first engineering assistant (beta)

DAYANERA.ai is a private, Turkish-language engineering assistant for a small internal team.
The beta runs entirely on one Windows laptop and is reachable **only** at `127.0.0.1`.
Its first engineering scope is gear design, production, measurement and quality. That scope is
grounded in the ISO PDFs in the `iso booklets` folder.

- Ordinary Turkish chat goes through local **Qwen2.5 14B Instruct (q4_K_M)** on native Windows **Ollama**.
- Technical answers come only from active, indexed ISO passages. If the sources cannot verify a claim,
  the reply is exactly **`Bu kaynak setinde doğrulayamadım`**.
- A deterministic, unit-aware calculation engine computes all numbers. Qwen may only draft a result,
  and the engine validates it. Any mismatch is shown to the user and written to the audit log.
- Local Docker PostgreSQL is the only authoritative database. Supabase is prepared but disabled, and nothing is synced.
- No online AI provider, cloud embedding or web search is called. The OpenAI and Claude adapters exist but are hard-disabled.

> Hard boundaries are enforced in code: settings refuse non-loopback hosts, a non-local database, a remote Ollama URL
> and enabled cloud providers. `scripts/start-local.ps1` verifies every listening socket after startup.

## Architecture

```text
Browser (React + Vite, http://127.0.0.1:5173)
   │  same-origin /api proxy (HttpOnly session cookie, CSRF header)
   ▼
FastAPI "DAYANERA Core API" (http://127.0.0.1:8000/api/v1, OpenAPI JSON at /api/v1/openapi.json)
   ├─ api/routes        auth · system · conversations/stream · documents · extractions · calculations
   │                    memory · audit · users/scopes · notes · retrieval
   ├─ services          chat orchestration · intent · retrieval (PostgreSQL FTS) · grounding · memory
   │                    notes · audit · access (roles/scopes) · system status · calculations
   ├─ calc              deterministic engine (no LLM dependency) + evidence catalog + ISO 286 table parser
   ├─ ingestion         watcher · DB job queue · PDF/Office/text/image/audio/video/archive extractors
   │                    local OCR (RapidOCR/ONNX) · local speech-to-text (faster-whisper) · versioned raw storage
   ├─ inference         LocalOllamaProvider (localhost only) · OpenAIProvider/ClaudeProvider (disabled)
   └─ db                SQLAlchemy 2 models ↔ database/migrations/sql/*.sql (Alembic, Supabase-compatible)
   ▼
Docker PostgreSQL 16 (127.0.0.1:54329, volume dayanera_pgdata)      Ollama (127.0.0.1:11434, native service)
data/   raw document versions · media · OCR/transcript cache · logs · backups · models   (gitignored)
agent-notes/   DAYANERA's Markdown recommendations only                                    (gitignored)
```

Repository layout:

| Path | Content |
|---|---|
| `backend/` | FastAPI app (`app/`), `requirements*.txt`, `requirements.lock.txt`, `alembic.ini` |
| `frontend/` | React + Vite + TypeScript UI, Vitest tests, Playwright smoke test (`e2e/`) |
| `database/migrations/` | Alembic environment + portable SQL (`sql/0001_initial.sql`) |
| `database/seed/` | Idempotent reference data (knowledge areas) |
| `scripts/` | `bootstrap`, `start-local`, `stop-local`, `test-all`, `reindex`, `reset-local`, `fetch-local-models`, `git-check-private-data` |
| `tests/backend/` | pytest unit + API/DB integration tests |
| `tests/e2e/` | live end-to-end smoke tests (real Qwen) |
| `docs/` | OpenAPI export, safe implementation-report template |

## Prerequisites (Windows 11)

| Tool | Tested version | Install |
|---|---|---|
| Git | 2.53 | `winget install Git.Git` |
| Docker Desktop | 29.6 (Compose v5) | `winget install Docker.DockerDesktop` |
| Python | 3.12 (via `py -3.12`) | `winget install Python.Python.3.12` |
| Node.js LTS | 24.14 | `winget install OpenJS.NodeJS.LTS` |
| Ollama | 0.34.4 | `winget install Ollama.Ollama` |
| Microsoft Edge | preinstalled | used by the Playwright smoke test |

## Setup

```powershell
cd C:\Users\Bediz\Documents\DAYANERA.ai
powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1        # checks tools, creates .env, venv, npm ci
ollama pull qwen2.5:14b-instruct-q4_K_M                                # ~9 GB, once
powershell -ExecutionPolicy Bypass -File scripts\fetch-local-models.ps1 # optional: speech-to-text model (~480 MB), once
```

`bootstrap.ps1` creates `.env` from `.env.example` if `.env` is missing. It replaces the PostgreSQL placeholder password
with a random local one and adapts paths to the checkout. It never overwrites an existing `.env`.
The beta admin credentials (`INITIAL_ADMIN_USERNAME` / `INITIAL_ADMIN_PASSWORD`, beta defaults from the contract) live only
in the untracked `.env`. Change them before any wider use.

## Start / stop

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start-local.ps1   # one command: DB → migrations → seed → Ollama check → API → UI
# open http://127.0.0.1:5173 and log in as "admin"
powershell -ExecutionPolicy Bypass -File scripts\stop-local.ps1    # stops UI/API and the DB container (data kept)
```

- `start-local.ps1 -Dev` uses the Vite dev server with hot reload. `-Open` opens the browser. `-Rebuild` forces a UI build.
- Logs are written to `data\logs\` (`backend.log`, `backend.stderr.log`, `frontend.*.log`).
- API contract: `http://127.0.0.1:8000/api/v1/openapi.json`. The export is in `docs/openapi.json`; regenerate it with `python -m app.cli export-openapi` from `backend\`.
  The interactive Swagger UI is disabled because it would load scripts from a public CDN.

## Database

- Migrations are plain PostgreSQL SQL in `database/migrations/sql/`, applied by Alembic (`python -m app.cli migrate` from `backend\`).
  They use only core PostgreSQL features, so they stay portable to Supabase/PostgreSQL.
- `python -m app.cli seed` idempotently seeds the knowledge areas and the initial `owner_admin`.
- Full-text search (`tsvector` + GIN, English and simple configurations) is the retrieval baseline. No vector extension is required.
- The audit table is append-only: triggers block `UPDATE`, `DELETE` and `TRUNCATE`.
- Manual backup (stays local, under `data\backups`):

```powershell
docker exec dayanera-postgres pg_dump -U dayanera -d dayanera -Fc -f /tmp/dayanera.dump
docker cp dayanera-postgres:/tmp/dayanera.dump data\backups\dayanera-$(Get-Date -Format yyyyMMdd).dump
```

## Documents, versions and ingestion

- **Verified corpus.** Only the active files in `iso booklets` (knowledge area `iso-disli`) can support a verified answer.
  Uploads and chat attachments are archived and searchable, but they are never verified sources.
- **Watcher.** A polling watcher scans the folder every `WATCHER_INTERVAL_SECONDS` (default 30). It works with Windows paths longer than 260 characters.
  - A changed file becomes a new, immutable raw version. The previous version is marked `superseded` and kept unchanged.
  - A removed file is logically deleted: its raw versions are preserved and it is excluded from retrieval.
  - A file that reappears becomes a new active version.
- **Manual reindex.** Run `scripts\reindex.ps1`, or use the owner buttons in the document archive.
- **Deletion.** Deleting in the UI is always logical and audited. The owner can restore a document.
- **All file types are accepted and archived.** Processing depends on the type:

| Type | Local processing | Resulting status |
|---|---|---|
| PDF with text layer | per-page text. Glyph-positioned text layers are rebuilt into words | `verified_source` in the ISO corpus, otherwise `unverified` |
| Scanned PDF pages, images | local OCR (RapidOCR, CPU), page previews | `draft_extraction` ("Taslak çıkarım") |
| DOCX, XLSX, PPTX, TXT, MD, CSV, JSON, HTML | text extraction | `unverified` |
| Audio | local faster-whisper transcription (offline model) | `draft_extraction` |
| Video | archive, metadata, 3 representative frames, audio transcript within limits | catalogued. Heavy analysis is deferred |
| ZIP, TAR | safe inventory and extraction (zip-slip, symlink, size and ratio guards), text members indexed | `unverified` |
| DOC, XLS, PPT, RTF, 7z, RAR, unknown | archived and catalogued only, with an actionable Turkish reason | `stored_only` |

**Draft-extraction safety.** Values found by OCR or transcription (dimensions, tolerances, tooth counts and so on)
go to the **review queue** ("İnceleme kuyruğu"). An authorized user must confirm or edit them there before they can be
used in calculations, in memory or as evidence. The confirmation, the actor, the time, the original value and the
source version are all audited.

## Answer policy

| Badge | Meaning |
|---|---|
| **Genel sohbet** | Ordinary Qwen conversation. It never claims ISO verification. |
| **Doğrulanmış kaynak cevabı** | Qwen answered only from retrieved active passages. Every number was checked against those passages. |
| **Hesap sonucu** | Deterministic engine result, validated against the sources. |
| **Taslak çıkarım** | Uses OCR or transcript content that has not been confirmed. |
| **Doğrulanamadı** | Exact refusal phrase, or an actionable error. |

- Citations are hidden by default. Type **"kaynak ver"**, or press the *Kaynak ver* button, to show the local document,
  version, page and excerpt. Provenance is always stored internally.
- A verified answer is accepted only if every number in it appears in the retrieved passages or the user's question.
  Otherwise the reply is `Bu kaynak setinde doğrulayamadım`.
- If a question names a standard that is not in the active corpus (for example ISO 6336), the reply is the refusal phrase.

## Deterministic calculations

The rules are implemented only where the loaded corpus contains the defining passages. They live in `backend/app/calc/evidence.py`.

| Type | Source passages |
|---|---|
| `cylindrical_gear_geometry` | ISO 21771:2007 Eq. (1), (2), (13)/(19), (14), (23), (24), (28), (33)–(37). Basic-rack defaults from ISO 53:1998 Table 2 |
| `gear_pair` | ISO 21771:2007 Eq. (52), (54) |
| `iso1328_flank_tolerance` | ISO 1328-1:2013 Formulae (5)–(12), scope limits, 5.2.2 step factor, 5.2.3 rounding |
| `iso286_it_tolerance` | ISO 286-1:2010 Table 1, parsed at runtime from the active source page and structurally validated |

If a required passage is missing, deleted, superseded or unindexed, the engine refuses with the exact phrase.
Draft OCR inputs, invalid units and unsupported formulas are also rejected.
The chat UI shows a concise result. Use **Ayrıntılı çözüm** to see inputs and provenance, formulas, units, source pages,
the step trace and the Qwen comparison.

## Memory, notes and audit

- Raw chats and attachments are kept permanently. Memory items are added alongside them:
  facts, preferences, confirmed values and summaries. Editing an item supersedes it and keeps the old record.
- Type "hatırla: …" in chat to store a user-confirmed fact.
- `agent-notes/YYYY-MM-DD_kisa-konu.md` is the **only** place DAYANERA writes development advice.
  Each note has a title, date, author, recommendation, rationale, affected areas, expected benefit, risks and a status
  (`öneri`, `inceleniyor`, `uygulandı` or `reddedildi`). Creating or editing a note is audited. DAYANERA never changes source code.
- The following all create append-only audit rows: logins and sessions, chats, document views and downloads, deletions,
  source use and reveals, memory updates, notes, role and scope changes, and failed authorization.

## Tests

```powershell
powershell -ExecutionPolicy Bypass -File scripts\test-all.ps1                 # backend + frontend + git + bindings
powershell -ExecutionPolicy Bypass -File scripts\test-all.ps1 -Live -Restart  # + live Qwen E2E, Playwright, restart persistence
```

- `tests/backend` creates a temporary `dayanera_test` database in the local container and uses a fake LLM.
  It never touches the real database or the real corpus. The marked tests cover the following:
  - `corpus` copies the real ISO PDFs into a temporary folder.
  - `slow` covers OCR and transcription.
- `tests/e2e` runs against the running stack with the real model. `frontend/e2e` holds the Playwright test, which uses the installed Edge.

## Git hygiene

Never run `git add .` without checking what is staged. Run the preflight before every commit:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\git-check-private-data.ps1   # or scripts/git-check-private-data.sh
```

The following are ignored: `.env*` (except `.env.example`), `data/`, `agent-notes/`, `IMPLEMENTATION_REPORT.md`, logs,
database dumps, `iso booklets/` (licensed documents), `repositories/` (external clones), virtual environments,
`node_modules` and build output.

## Reset (destructive)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\reset-local.ps1 -ConfirmDestroy "TUM-YEREL-VERIYI-SIL" [-IncludeRuntimeFiles]
```

This deletes the PostgreSQL volume, which holds all chats, memory and the audit log. With `-IncludeRuntimeFiles` it
also deletes `data\` and `agent-notes\`. Without the exact phrase, nothing is deleted.
It never touches `iso booklets`, `.env` or the source code.

## Security notes and deferred hardening

- The beta uses a local PBKDF2 password hash and opaque session tokens. Only the SHA-256 of each token is stored.
  The token travels in an HttpOnly, SameSite=Strict cookie, and state-changing requests need a CSRF header.
  A trusted-host check blocks DNS rebinding. Secrets are redacted in logs.
- Deferred before wider use:
  - replace the default beta admin credential;
  - add a password policy and login rate limiting or lockout;
  - rotate sessions;
  - use HTTPS if the service ever leaves loopback (it must not in beta);
  - add at-rest encryption of `data\` and the database volume;
  - add backup encryption;
  - move to company accounts or SSO (a future on-premise phase — **not implemented**).

## Supabase and future providers

- `SUPABASE_ENABLED=false` by default.
- The owner-only `GET /api/v1/integrations/supabase/status` reports whether values are configured.
  It never returns them and never contacts Supabase.
- There is no replication, sync, upload or external backup, and no Supabase client is installed.
- `OPENAI_ENABLED` and `ANTHROPIC_ENABLED` must stay `false`. Setting either to `true` stops the app from starting in beta.

## Performance and limitations (target laptop, CPU only)

- Measured Qwen 14B q4_K_M throughput is about 8–15 tokens/s for prompt evaluation and about 2.5 tokens/s for generation.
  - General replies take about 1–1.5 minutes.
  - Verified answers take about 2–5 minutes.
  - Chat calculations with a Qwen draft take about 1.5 minutes. The engine result alone is instant, for example from the calculator page.
- Ollama answers one request at a time. OCR of large scanned standards runs in the background, and results are cached.
- RapidOCR sometimes drops spaces between English words. OCR text therefore stays a draft until someone reviews it.
- Legacy Office, 7z and RAR files are archived only. Video is catalogued only. Qwen never receives raw images, audio or video.

## Troubleshooting

| Symptom | Action |
|---|---|
| "Yerel sunucuya ulaşılamıyor" | Run `scripts\start-local.ps1` and check `data\logs\backend.stderr.log`. |
| "Veritabanına ulaşılamıyor" | Start Docker Desktop, then run `docker compose up -d postgres`. |
| "Yerel Ollama servisine ulaşılamadı" | Start Ollama from the Start menu. Check with `ollama list`. |
| The model is missing | `ollama pull qwen2.5:14b-instruct-q4_K_M` |
| Empty-corpus banner | Add PDFs to `iso booklets` and wait for the watcher, or run `scripts\reindex.ps1`. |
| No speech-to-text | `scripts\fetch-local-models.ps1` |
