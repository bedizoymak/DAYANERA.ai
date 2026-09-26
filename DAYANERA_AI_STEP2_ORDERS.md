# DAYANERA.ai — Step 2 Orders (Post-Beta Test Fixes)

**Status:** Authoritative change order. Supplements, does not replace, `DAYANERA_AI_CLAUDE_CODE_MASTER_SPEC.md`.

**Project root:** `C:\Users\Bediz\Documents\DAYANERA.ai`

**Origin:** Manual browser test of the running beta (2026-09-26) followed by a source review. All rules of the master spec remain in force — in particular §1 rule 3 (exact refusal phrase), local-only operation, no cloud providers, Qwen2.5 14B on CPU.

**Objective:** Fix intent misrouting, make refusals informative without breaking the exact-phrase contract, eliminate wasted LLM time on unanswerable technical questions, and add regression tests. Do not rewrite unrelated subsystems.

---

## 0. Test observations (evidence)

| # | User message | Observed | Diagnosis |
|---|---|---|---|
| T1 | `M10 civata 8.8 kalite, çekme dayanımı ne kadar? sıkma torku kaç Nm` | ~100 s wait, then `Bu kaynak setinde doğrulayamadım` | Correct outcome (ISO 898-1 not in corpus) but ~100 s wasted on generation that grounding then rejected. |
| T2 | `peki elinde hangi ISO standartları var, listeler misin?` | Routed to technical, refusal | **Bug.** Meta/corpus question classified as technical because `has_technical_terms` fires on the single word `iso`/`standart`. Should list active documents. |
| T3 | `ISO 2768 ... m sınıfı 30-120 mm tolerans ne? kaynak ver` | Refusal | Correct outcome (ISO 2768 not in corpus). Latency and uninformative UX are the issue. |
| T4 | `bana kısaca kendini tanıtır mısın` | General mode, correct, streamed very slowly | Expected on CPU; only minor tuning in scope. |

Active corpus at test time (14 documents): ISO 53, 54, 1328-1, 21771, 4468, 2490, TR 10064-1, TR 10828, 14104, 286-1, 286-2, 1101 and two documents with `standard_code = null`.

---

## 1. Order A — New intent: `corpus_inventory`

**Files:** `backend/app/services/intent.py`, `backend/app/services/chat.py`, tests.

1. Add intent kind `corpus_inventory`, checked **before** `technical` (after memory/note commands).
2. Trigger on Turkish/English questions about what documents/standards the system has, e.g.:
   - `hangi (ISO )?standart(lar)?(ın)? var`, `elinde(ki)? (hangi )?(standart|kaynak|doküman|belge)`, `neleri biliyorsun`, `kaynak setinde ne var`, `standartları listele`, `which standards do you have`, `list (the )?documents`.
   - Must NOT trigger when a concrete technical question is present (e.g. `ISO 286'da H7 50 mm ne?`). Rule: inventory pattern matched AND no `ISO <number>` code AND no digits other than inside the pattern.
3. Handler `_corpus_inventory`: query active documents visible to the user's scopes; reply immediately (no LLM call) with a Turkish bullet list: `standard_code — document_title` sorted by code; documents without code listed as `(kod yok) — title`. Mode label: `general` is acceptable; prefer a new label `system_info` if the frontend badge mapping can be extended trivially.
4. Response time target: < 1 s.

## 2. Order B — Informative refusals (keep exact phrase)

**Files:** `backend/app/services/chat.py`, `backend/app/services/grounding.py` (read only), frontend message rendering, tests.

The master spec requires the refusal text to be **exactly** `Bu kaynak setinde doğrulayamadım`. Do not alter `message.content`.

1. Keep `content == REFUSAL_PHRASE` for all refusals.
2. Add structured `metadata.refusal` to every refusal message: `{ "reason": "no_passages" | "low_relevance" | "unsupported_numbers" | "model_refused" | "invalid_citation" | "empty_answer", "codes_requested": [...], "codes_missing": [...] }`.
3. When the question names an ISO code that is **not** in the active corpus (use `plan.codes` vs active `standard_code` list), set `codes_missing` accordingly.
4. Frontend: under the refusal bubble render a small muted hint (not part of content), Turkish:
   - `codes_missing` non-empty → `İstenen standart yüklü değil: ISO 2768. "hangi standartlar var" yazarak listeyi görebilirsiniz.`
   - `no_passages` / `low_relevance` → `Bu konu yüklü standartlarda bulunamadı.`
   - `unsupported_numbers` → `Model yanıtındaki bazı değerler kaynakta doğrulanamadı.`
5. Existing audit events stay; add `reason` consistently.

## 3. Order C — Stop wasting generation time on unanswerable technical questions

**Files:** `backend/app/services/chat.py`, `backend/app/services/retrieval.py`, `backend/app/core/config.py`, `.env.example`, tests.

Target hardware is CPU-only; the 14B model stays (master spec §2). Reduce wasted calls instead.

1. **Missing-code short-circuit:** if the question references one or more ISO codes and none of them is in the active corpus → refuse immediately (reason `no_passages`, `codes_missing` filled). No retrieval, no LLM.
2. **Relevance gate:** add `RETRIEVAL_MIN_SCORE` (default to be calibrated, start at `0.35`) and `RETRIEVAL_MIN_COVERAGE` if coverage is separable. If the best passage score is below the threshold → refuse immediately with reason `low_relevance`. Calibrate on the test set in Order E and record chosen values in the report.
3. **Generation budget:** reduce technical `num_predict` from 350 to 250 (detail mode stays 900). Keep `temperature=0.1`.
4. Log per-stage timings (retrieval, generation, validation) into message metadata `timings_ms`.
5. Do not change model, context size, or provider.

## 4. Order D — Narrow the technical-term trigger

**Files:** `backend/app/services/glossary.py`, `backend/app/services/intent.py`, tests.

1. The bare words `iso`, `standart`, `standard`, `din` alone must not classify a message as technical. They count only together with an ISO code, a mapped Turkish technical term, a gear/tolerance term, or a number with unit.
2. Keep all existing positive cases passing; add the negative cases listed in Order E.

## 5. Order E — Regression tests

Add to `tests/backend` (pure unit tests, no Ollama required; mock provider where needed):

| Input | Expected intent / behavior |
|---|---|
| `elinde hangi ISO standartları var, listeler misin?` | `corpus_inventory`, no LLM call, lists active codes |
| `hangi standartlar yüklü` | `corpus_inventory` |
| `which standards do you have` | `corpus_inventory` |
| `ISO 286'ya göre 50 mm H7 toleransı nedir?` | `technical` or `calculation` (NOT inventory) |
| `ISO 2768 m sınıfı 30-120 mm tolerans ne? kaynak ver` | refusal, content exact, `codes_missing=["ISO 2768"]`, provider never called |
| `M10 civata 8.8 çekme dayanımı` | refusal via `low_relevance` or `no_passages`, provider never called (with test fixtures lacking bolt content) |
| `ISO iyi bir şey mi` | `general` |
| `bana kısaca kendini tanıt` | `general` |
| existing tests | all still pass |

Also add one e2e smoke (if the e2e harness supports it): send T2 and assert a list response in < 2 s.

## 6. Acceptance gates

- All tests pass (`pytest`), ruff clean.
- Manual check in the browser at `http://127.0.0.1:5173`:
  1. T2 returns the standards list in ~1 s.
  2. T1 and T3 return the exact refusal in a few seconds (not ~100 s) with the correct hint under the bubble.
  3. `ISO 286'ya göre 50 mm H7 toleransı nedir?` still produces a verified answer (`verified_source` or calculation mode).
  4. Ordinary chat unchanged.
- Refusal `content` is byte-identical to `REFUSAL_PHRASE` in every case.

## 7. Report

Append a section `## Step 2` to `IMPLEMENTATION_REPORT.md` with: changed files, new config keys and calibrated values, test results, measured latencies for T1–T4 before/after, and any deviations with reasons. Commit source changes to Git (runtime data remains ignored).
