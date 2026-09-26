# DAYANERA.ai — Claude Code Master Implementation Contract

**Status:** Authoritative build specification

**Project root on target Windows machine:** `C:\Users\Bediz\Documents\DAYANERA.ai`

**Build objective:** Build a fully runnable, local-first beta of DAYANERA.ai from an empty project directory. Do not produce a scaffold, mock-only interface, or a partial demo. Implement the working system, test it, and write the required implementation report before declaring completion.

This document is the implementation contract. Where a choice is not explicitly prescribed, make the smallest robust decision that preserves all requirements below. Do not introduce cloud dependencies, public exposure, or unnecessary services.

---

## 1. Product definition and hard boundaries

DAYANERA.ai is a private engineering assistant for a small internal team (ultimately 10–20 users). The beta runs on one Windows laptop, locally only, using a browser interface that resembles the interaction model of ChatGPT.

The initial engineering scope is **gear design, production, measurement and quality**, grounded in the ISO document corpus located at:

```text
C:\Users\Bediz\Documents\DAYANERA.ai\iso booklets
```

The system must also support ordinary Turkish chat. It must clearly distinguish ordinary base-model conversation from verified engineering output.

### Non-negotiable rules

1. The beta must listen only on `127.0.0.1` / `localhost`. Never bind to `0.0.0.0`; do not expose a LAN, WAN, tunnel, remote-access, public or cloud endpoint.
2. The beta's authoritative runtime data remains in **local Docker PostgreSQL**. Supabase is prepared as a future integration, but there is **no automatic replication, sync, upload or external backup** in beta.
3. In offline technical answers, do not guess. When the active local source set cannot verify a requested technical claim, respond exactly:

   ```text
   Bu kaynak setinde doğrulayamadım
   ```

4. Engineering calculations must be restricted to the active verified ISO corpus and explicit user inputs. Never invent standard values, material values, formula constants or tolerances.
5. Qwen may draft a calculation or explanation, but the deterministic calculation engine must independently calculate/validate the numeric result. If results differ, do not silently choose the LLM output: show the validated result, state there was a mismatch, and retain an audit event.
6. All source code is version-controlled with Git. Chats, documents, document versions, media, indexes/vector data, PostgreSQL volumes/dumps, audit logs, user data, `.env`, generated runtime files and reports must be excluded from Git.
7. DAYANERA does not edit source code. It may create advice Markdown files only in the dedicated recommendations directory. Separate MCP/agent workflows will handle code changes later.
8. Do not call OpenAI, Anthropic, cloud embeddings, web search or any other online AI provider from the beta. The later provider adapter must be designed, but disabled.

---

## 2. Target hardware and operating assumptions

Target beta machine:

- Dell Latitude 7350; Windows 11 Pro
- Intel Core Ultra 5 135U (12 cores / 14 logical processors)
- 32 GB LPDDR5x RAM
- integrated Intel graphics only; no discrete CUDA GPU
- 512 GB NVMe SSD

Performance is secondary to correctness and a reliable local setup. Use CPU-oriented local inference. The selected core model runtime is **Ollama** with **Qwen2.5 14B Instruct, 4-bit quantized**. Make model name and context parameters configurable in `.env`; do not hard-code a model file path.

The laptop hosts both client and backend in beta. Docker is required for PostgreSQL and supporting local services only where it materially reduces setup complexity. Do not containerize Ollama by default; use the native Windows Ollama service and communicate through its local API.

---

## 3. Required technology baseline

Implement a monorepo with this minimum structure (adapt names only where required by tooling):

```text
DAYANERA.ai/
  backend/                 # Python 3.12+, FastAPI
  frontend/                # React + Vite + TypeScript
  database/
    migrations/            # PostgreSQL migrations, Supabase-compatible SQL
    seed/
  docker-compose.yml
  .env.example
  .gitignore
  README.md
  scripts/
  tests/
  agent-notes/             # DAYANERA-generated recommendations only; gitignored
  data/                    # runtime root; entirely gitignored
    documents/
    document-versions/
    media/
    indexes/
    exports/
    logs/
    backups/
  IMPLEMENTATION_REPORT.md # required final report, gitignored
```

### Backend

- Python + FastAPI, typed request/response models, Pydantic settings.
- PostgreSQL with SQLAlchemy 2.x and Alembic migrations.
- Use `psycopg`/async equivalent safely; use parameterized queries only.
- REST API under `/api/v1`; add `/api/v1/health` and `/api/v1/readiness`.
- Keep boundaries explicit: API routes, services, repositories, domain models, ingestion pipeline, inference adapter, calculation engine, audit service.
- Local Qwen adapter calls Ollama only at its configurable localhost URL.
- Build a provider abstraction now: `LocalOllamaProvider`, disabled `OpenAIProvider`, disabled `ClaudeProvider`. Future keys must never be needed for beta startup.

### Frontend

- React + Vite + TypeScript.
- Clean ChatGPT-like desktop browser experience in Turkish: left conversation list, central conversation, message composer, attachment control, source/citation control, account menu.
- Functional, accessible UI; no fake controls.
- Connect only to FastAPI localhost API configured through Vite environment variables.

### Database and Docker

- Docker Compose must start PostgreSQL locally with a named persistent volume and a localhost-only mapped port.
- The database schema must be portable to Supabase/PostgreSQL later. Use normal PostgreSQL features; avoid vendor-locked cloud services.
- Use PostgreSQL full-text search as the baseline retrieval mechanism. If a vector extension is included, it must be local and optional, with a functioning FTS fallback.
- No external database, no Supabase data writes, and no background synchronizer in beta.

### Supabase readiness without beta sync

- Provide `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, `SUPABASE_SECRET_KEY`, and `SUPABASE_ENABLED=false` placeholders in `.env.example`.
- The real `.env` is ignored by Git. Never place actual credentials in committed files, logs, frontend bundles or the implementation report.
- Implement an explicit, owner-only configuration/status endpoint that reports whether future Supabase integration is configured, but it must not transmit project data when `SUPABASE_ENABLED=false` (the default).
- In this Python backend, use a Python Supabase client only if it is needed for the future adapter. Do **not** add Node-only `@supabase/server` to FastAPI.

---

## 4. Authentication, roles and local-only access

Beta starts with one local admin account:

```dotenv
INITIAL_ADMIN_USERNAME=admin
INITIAL_ADMIN_PASSWORD=1234
```

Place the values in the untracked `.env` only and give the same placeholders/default guidance in `.env.example`. On first startup, seed this account idempotently. Although hardening is deferred, do not make passwords appear in APIs, browser storage, logs, audit event payloads, screenshots or reports. Use a minimal local session mechanism suitable for a localhost beta and document it as a future security-hardening item.

Implement roles and data-scope fields now because the future team needs them:

- `owner_admin`: sees all data, documents, memory, audit records and role assignments.
- `member`: sees only conversations, documents and knowledge areas explicitly assigned to them.

In beta, the seeded admin is `owner_admin`. Provide owner UI/API for creating future users and assigning scopes, even if only `admin` is initially present. Do not claim SSO exists; put company account/SSO as an explicit on-premise future phase.

All access, login/session events, chats, document views, document downloads, document deletion, source use, memory updates, advice-file creation, role/scope changes and failed authorization attempts must create immutable-style audit rows. Owner sees all audit rows; other users can see only their own actions when this view becomes available.

---

## 5. Data classification, persistence and Git rules

Create a rigorous `.gitignore`. It must exclude at minimum:

```gitignore
.env
.env.*
!.env.example
data/
agent-notes/
IMPLEMENTATION_REPORT.md
*.log
postgres-data/
pgdata/
__pycache__/
.pytest_cache/
.venv/
node_modules/
dist/
coverage/
```

Keep code, migrations, tests, scripts, README, Docker configuration, `.env.example`, dependency lock files and a safe report template in Git. Never use `git add .` without checking the staged file list; provide a safe `scripts/git-check-private-data.*` preflight helper and documentation.

### Persistent memory

The user does not want the system to forget. Persist raw chats and attachments, structured conversation summaries, explicit facts/preferences, user-confirmed technical values, source links, document lineage and relationships. Memory must be searchable and scoped by user/role. Preserve original raw records rather than replacing them with summaries.

Use distinct confidence/status states:

- `verified_source`: grounded in an active, indexed local source.
- `user_confirmed`: explicitly confirmed by an authorized user.
- `draft_extraction`: generated by OCR/vision/transcription or parsing; never valid technical input until confirmed.
- `unverified`: ordinary model context; never presented as verified engineering source.
- `superseded`, `deleted`, `archived`: historical states, never active technical sources.

Document ingestion and normal chat must store durable records in PostgreSQL and raw artifacts under `data/`.

---

## 6. Document archive, versioning and ingestion

The application accepts and archives **all file types**, including PDFs, Office files, text, images, audio, archives and video. Acceptance/storage is not the same as full semantic analysis.

### Required file lifecycle

1. Detect a file added/changed under the configured project roots, including `iso booklets`, by an application-managed watcher plus a manual reindex action.
2. Copy/store raw bytes with hash, size, MIME type, source path, timestamps and uploader/actor metadata.
3. If a file changes, retain every previous raw version unchanged. The newest version becomes the active version after successful ingestion.
4. If a tracked source disappears, preserve the last raw version, mark it `deleted`, log it, and exclude it from active technical retrieval.
5. Deleting in the UI is a logical deletion/history event; never silently purge a source.
6. Maintain a document-version lineage record with stable document ID, version number, checksum and active status.
7. Only active, successfully indexed documents can support a verified technical answer.

### Local processing capability

- Native-text PDFs/documents: extract text locally.
- Scanned PDFs and small single images: local OCR, page/image previews and local visual extraction.
- Audio: local transcription.
- Archives: extract safely into controlled temporary folders; record member inventory; block zip-slip/path traversal and resource abuse.
- Video: archive and catalogue it locally. For beta, extract only basic metadata/optional audio or representative frames within sensible configurable limits. Heavy video analysis is deliberately deferred to a future API workflow.

Do not pretend a text-only Qwen model understands images, scans, audio or videos natively. Supply these through dedicated local extraction services/pipelines and label their outputs correctly.

### OCR/vision/transcription technical safety

Every extracted value that could affect an engineering result (dimensions, tolerances, tooth counts, material identifiers, drawing values, numeric tables) starts as `draft_extraction` and is visibly labelled **Taslak çıkarım**. It cannot enter calculations, verified memory or active technical facts until an authorized user explicitly confirms it in the UI. Store the confirmation, actor, time and original source/version in audit and provenance records.

---

## 7. ISO knowledge and answer policy

The initial verified engineering corpus is only the active files from `iso booklets`. Build the ingestion/retrieval flow to retain page-level or equivalent locators, source title, document/version ID and excerpt boundaries.

### Response behavior

- Primary language: Turkish.
- On first use of a specialist term, include English in parentheses, e.g. `dişli azdırma (gear hobbing)`. Do not mechanically translate every repeated term.
- Default response style: short practical result. When asked for detail, show assumptions, formula, variables, units, sources and step-by-step solution.
- For a normal greeting or general conversation, Qwen may answer naturally; it must not claim this is ISO-verified knowledge.
- For engineering/gear questions, retrieve active ISO corpus first and classify whether the question is answerable from it.
- When validation is unavailable, return exactly `Bu kaynak setinde doğrulayamadım`. Do not fill the gap with a plausible calculation or web knowledge.
- Sources/citations are hidden by default. Display exact local source citations only when the user explicitly says **“kaynak ver”** (or clear equivalent). Internally retain provenance regardless.

### Retrieval and anti-hallucination requirements

- Answer only from retrieved source passages plus explicit user inputs for verified technical mode.
- Keep the source passage, document/version/page locator and retrieval score with the response record.
- Never cite deleted, superseded, failed-ingestion or draft-extraction data as active evidence.
- Include a visible answer mode marker in UI/API: `Genel sohbet`, `Doğrulanmış kaynak cevabı`, `Hesap sonucu`, `Taslak çıkarım`, or `Doğrulanamadı`.
- Build prompt templates that clearly instruct Qwen about the mode, permitted context and exact refusal phrase.

---

## 8. Deterministic gear calculation engine

Create a separate, typed, unit-aware calculation module. It must have no dependency on the LLM. Qwen may map user language to a calculation request and explain the result, but the calculation engine receives validated structured inputs and produces the authoritative numbers.

### Beta calculation contract

- Inputs must identify units and source/provenance.
- Inputs sourced from OCR/vision/transcription are blocked unless marked `user_confirmed`.
- Inputs that require standard constants/rules must be traceable to an active source passage in the ISO corpus.
- Return results, units, assumptions, formulas/rule identifiers, input provenance, validation diagnostics and error/refusal state.
- Qwen’s independent draft calculation must be compared within defined tolerance where applicable. Persist the comparison; present a mismatch notice rather than concealing it.
- Default UI output: concise result and status. Add `Ayrıntılı çözüm` to reveal inputs, formulas, units, source identifiers and validation trace.
- Do not implement unverified formulas merely to look complete. It is better to reject an unsupported calculation with the required phrase.

Seed the engine with a small, fully tested set of calculations only when their requirements can be grounded in the actual loaded ISO corpus. Tests must include valid calculations, missing source, invalid units, unsupported formula, draft OCR input rejection and Qwen-vs-engine mismatch behavior.

---

## 9. Chat, attachments and UX requirements

Implement a working browser interface at a documented localhost address.

### Required views

1. Login screen.
2. Main chat screen, including conversation creation/list/rename/archive and persisted message history.
3. Composer supporting text and all-file attachment upload.
4. Source/citation display only on explicit request.
5. Answer status/mode badges as described above.
6. Detail expansion for calculations and source context.
7. Document archive: searchable list, filter by state/type/active status, version history, metadata, preview where feasible, download, logical delete and reindex controls.
8. Review queue for `Taslak çıkarım` values: confirm/reject/edit with full provenance.
9. Owner audit log: filterable event list.
10. User/scope administration scaffold for future members.
11. System status: database, Ollama availability/model, watcher state, corpus/index status, local storage usage and future Supabase configuration state. It must not reveal secrets.
12. Recommendation-note list: show Markdown advice files, never use it to alter code.

UI must work without internet. Clearly handle Ollama unavailable, database unavailable, empty corpus, failed ingestion and unsupported media with actionable Turkish messages.

---

## 10. Recommendation Markdown contract

Create and document `agent-notes/` as the only place DAYANERA may write development advice. It is excluded from Git.

Each advice item is a separate file with this naming convention:

```text
YYYY-MM-DD_kisa-konu.md
```

Example: `2026-09-26_disli-hesap-modulu.md`.

Each note must include title, date, author/source, recommendation, rationale, affected areas, expected benefit, risks/assumptions and status (`öneri`, `inceleniyor`, `uygulandı`, `reddedildi`). Creating/editing a note requires an audit record. No autonomous source-code modifications are permitted from this feature.

---

## 11. APIs and future extensibility

Design a private `DAYANERA Core API` as the single interface future browser, Windows and Android apps will consume. It is not a public product API and must remain localhost-only in beta.

At minimum provide documented endpoints/services for:

- auth/session and current user
- health/readiness/system status
- conversations/messages/streaming generation
- attachments
- documents, versions, ingestion and reindexing
- retrieval/source provenance
- draft extraction confirmation
- deterministic calculations
- memory search and user-confirmed facts
- audit log
- users/scopes (owner-only)
- recommendation notes
- future-provider configuration status (no secret return)

Generate an OpenAPI document automatically and keep endpoint tests. Keep transport/API contracts independent from the inference provider and database implementation as much as practical.

---

## 12. Runtime configuration

Supply a complete `.env.example` with safe placeholders, comments and no secrets. Include, at least:

```dotenv
APP_NAME=DAYANERA.ai
APP_ENV=beta_local
APP_HOST=127.0.0.1
APP_PORT=8000
FRONTEND_HOST=127.0.0.1
FRONTEND_PORT=5173

POSTGRES_DB=dayanera
POSTGRES_USER=dayanera
POSTGRES_PASSWORD=change-local-password
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=54329
DATABASE_URL=postgresql+psycopg://dayanera:change-local-password@127.0.0.1:54329/dayanera

OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen2.5:14b-instruct-q4_K_M
OLLAMA_REQUEST_TIMEOUT_SECONDS=600

INITIAL_ADMIN_USERNAME=admin
INITIAL_ADMIN_PASSWORD=1234

PROJECT_ROOT=C:\\Users\\Bediz\\Documents\\DAYANERA.ai
ISO_BOOKLETS_PATH=C:\\Users\\Bediz\\Documents\\DAYANERA.ai\\iso booklets
DATA_ROOT=C:\\Users\\Bediz\\Documents\\DAYANERA.ai\\data
AGENT_NOTES_PATH=C:\\Users\\Bediz\\Documents\\DAYANERA.ai\\agent-notes

SUPABASE_ENABLED=false
SUPABASE_URL=
SUPABASE_PUBLISHABLE_KEY=
SUPABASE_SECRET_KEY=
```

Validate paths on startup and provide precise instructions without silently creating unexpected locations outside the project root. All application-controlled data must remain below the configured project root.

---

## 13. Installation, startup and operations

Produce Windows-friendly operational commands/scripts. The primary startup path must be one clear command after prerequisites are installed.

Required deliverables:

- `README.md`: prerequisites (Git, Docker Desktop, Python, Node LTS, Ollama), setup, `.env` creation, model pull, database startup/migration/seed, frontend/backend start, tests, stop/reset instructions and limitations.
- `scripts/bootstrap.ps1`: idempotently checks prerequisites/configuration and prepares local dev environment without downloading secret config.
- `scripts/start-local.ps1`: starts Docker PostgreSQL, applies migrations, seeds the initial admin if needed, checks Ollama/model and starts backend/frontend. It must never bind publicly.
- `scripts/stop-local.ps1`: clean shutdown.
- `scripts/test-all.ps1`: runs backend/frontend/database integration tests and outputs a clear result.
- `scripts/reindex.ps1`: explicit manual full reindex.
- `docker-compose.yml`: localhost-only PostgreSQL, healthcheck, named volume.

Do not automatically delete volumes, documents, messages or logs. Any reset command must be separate, prominently destructive, require a confirmation argument and be documented.

---

## 14. Testing and acceptance gates

Implement meaningful automated tests; do not merely list manual test ideas. The final report must give the exact commands and outcomes.

Minimum gates:

- Backend unit tests for auth/session behavior, authorization scopes, audit creation, memory persistence, ingestion versioning/deletion, source state filtering, calculation validation and provider failure handling.
- API integration tests against temporary/local PostgreSQL for the critical flows.
- Frontend tests for login, chat, attachment, answer mode indicators, source display opt-in and draft confirmation flow.
- At least one end-to-end smoke path: login → ingest an ISO PDF → indexed/active → ask verified question → request `kaynak ver` → citation appears → start calculation → display engine result → audit event exists.
- Negative path: unsupported technical question returns exact refusal phrase; deleted document cannot support answer; draft OCR value is rejected from calculation before confirmation.
- Verify all service bindings are localhost-only.
- Verify `.gitignore` excludes private runtime data and no secrets are in tracked files.
- Verify startup works after restart and data/chat/document history persist.

If a capability cannot be installed or reliably tested on the target laptop, state it truthfully in the report with the precise blocker and a safe next action. Do not fake passing tests.

---

## 15. Required final implementation report

At the end of implementation, create this untracked file at project root:

```text
IMPLEMENTATION_REPORT.md
```

It must contain:

1. date/time, machine/runtime versions and exact selected Qwen/Ollama model;
2. full implemented architecture and repository tree;
3. all completed requirements mapped to sections of this contract;
4. files created/changed and the reason for each group;
5. database schema/migrations and persistent storage locations;
6. startup, shutdown, indexing and test commands actually run;
7. test results, failures, skipped items and exact reasons;
8. installed prerequisites/dependencies and versions;
9. Ollama/model installation state and any performance observations;
10. security/deferred-hardening list (including beta credential limitations) without revealing secrets;
11. Supabase readiness status, explicitly confirming whether any data was sent (expected: no);
12. known limitations, unsupported media-analysis boundaries and next recommended milestones;
13. Git status summary, confirmation that private data is ignored, and any uncommitted intended code changes;
14. concise handoff checklist for a later Codex review.

The report must not include real passwords, API keys, access tokens, private raw documents or unredacted personal data.

---

## 16. Completion definition

Only call the build complete when all of the following are true:

- The local ChatGPT-style application opens at localhost and the `admin` beta user can log in.
- Qwen answers ordinary Turkish chat through local Ollama.
- ISO PDFs can be ingested, versioned, retrieved and used for verified gear answers.
- Unsupported/unverified technical questions use exactly `Bu kaynak setinde doğrulayamadım`.
- Citations are suppressed by default and shown only on request.
- Deterministic calculations validate Qwen-produced drafts and provide detailed steps on request.
- All required persistence, document archive/versioning, memory, audit and draft-extraction confirmation flows function.
- Local Docker PostgreSQL is the only beta authoritative database; Supabase remains configured-but-disabled, with no automatic data transfer.
- Startup/testing/documentation scripts work and the required report is written.
- Source code is Git-safe and private runtime data is not staged.

Begin implementation now. Work carefully through the contract, test as you go, and leave the repository in a running, reviewable state.
