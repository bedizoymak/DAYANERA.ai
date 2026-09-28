# Engineering document chunking — implementation report

Scope: DAYANERA's PDF/booklet ingestion and chunking for ISO standards and engineering booklets
(equations, tables, figures, definitions, symbol lists), and the retrieval that consumes the chunks.
Baseline: commit `b69692f` (self-maintenance work). Maintainer guide:
[ENGINEERING_DOCUMENT_CHUNKING_GUIDE.md](ENGINEERING_DOCUMENT_CHUNKING_GUIDE.md). Machine-readable
results: [`docs/chunking/`](chunking/).

## 1. Architecture before

```
PDF -> PyMuPDF "dict" lines (Symbol-font Greek recovered) -> per-page ISO layout blocks
    (headings, clause numbers, table rows from line geometry, equation = glyphs left of "(n)")
    -> chunk_document: per page, cut at clause boundaries, 1 200-char cap + 150-char sliding window,
       page break = chunk break, content_type from the mix of blocks
    -> document_chunks (clause, heading, content_type, lineage) -> tsvector -> gates -> approval
    -> retrieval: exact + lexical channels, RRF, boosts -> chat: 800-char focus window per passage
```

What it lost (measured, §5): exact formula form (0/13), heading paths (0 %), equations separated
from their clause/lead-in (3 of 15 benchmark equations), chunks up to 1 288 Qwen tokens (p95 889),
table continuations, BSI running headers and page numbers inside content, table cells fused by the
"clause number + title" merge, inline Greek symbols split onto separate lines.

## 2. Architecture after

```
PDF -> pass 1: PyMuPDF rawdict lines/segments (per-glyph baselines & boxes, fonts)
    -> repeated_furniture (document-level running headers/footers)
    -> pass 2: table grids -> merge_inline_fragments -> detect_blocks
         headings/clauses (plausible numbering), grid tables (header rows, units, footnotes, continued),
         equations -> formula.reconstruct (2-D geometry: fractions, roots, |z|, scripts; raw/plain/LaTeX;
         needs_review when anything is unexplained), "where" legends, figures + keys, notes/examples
    -> OCR fallback (RapidOCR) only for pages without a text layer; text-only structure for them
    -> structure.build_model: sections (heading path) + units, cross-page continuation, continued tables,
       legends -> formula runs, lead-in sentences, symbol glossary
    -> chunker.chunk_pages: leaf (whole small clause) | parent window + children (formula / table /
       table row group / figure / paragraph group), content types, structural context, token limits
    -> pipeline: parents before children, metadata columns (migration 0004), meta_hash, version-level
       chunking summary + glossary; 16 quality gates (+ chunk_structure, formula_extraction, table_structure)
    -> retrieval: leaves & children only; label phrases in the exact channel; context scoring;
       wanted_kind / defines_quantity / informative_annex boosts
    -> build_context: variables + LaTeX + parent window -> Qwen (FORMULA_RULE: copy the LaTeX)
```

No model is used anywhere in ingestion. No new dependency was added.

## 3. Files changed

| file | change |
|---|---|
| `backend/app/ingestion/parsing/iso_layout.py` | rawdict segments, repeated furniture, inline-fragment merge, grid tables (header rows, units, footnotes, continued, alignment flag), baseline-based equation bands, legends, figure keys, table-safe clause merge, key-state reset |
| `backend/app/ingestion/parsing/formula.py` (new) | deterministic 2-D equation reconstruction, raw/plain/LaTeX, safety checks, glyph conservation |
| `backend/app/ingestion/structure.py` (new) | hierarchical document model, cross-page continuation, legends/lead-ins, glossary, OCR text structure |
| `backend/app/ingestion/chunker.py` | rewritten: engineering chunker (leaf/parent/child, content types, context, limits, inline equations); `chunk_page` kept |
| `backend/app/ingestion/tokens.py` (new) | Qwen-calibrated token estimator |
| `backend/app/ingestion/extractors/pdf.py`, `extractors/base.py` | two-pass extraction, layout statistics, math-font suspects; `Block.data`/`bbox` |
| `backend/app/ingestion/pipeline.py` | chunk persistence with hierarchy/metadata, `meta_hash`, chunking summary + glossary, `_persist_extraction`, **safe `reingest_version`** with savepoint rollback and citation re-mapping |
| `backend/app/ingestion/quality.py` | page-range traceability, furniture-aware coverage, gates `chunk_structure` (blocking), `formula_extraction`, `table_structure`; fingerprint covers structure |
| `backend/app/ingestion/inspect.py` (new), `backend/app/cli.py` | `inspect-document`, `reingest-document` |
| `backend/app/services/retrieval.py` | parents excluded from search, new columns, context scoring, `question_kind`, `wanted_kind`, `defines_quantity`, label phrases, `informative_annex`, sibling cap (`diversify`), `build_context` + query-focused parent context (`focused_parent`) |
| `backend/app/services/chat.py`, `backend/app/inference/prompts.py` | context expansion in the chat, grounding over what the model saw, FORMULA_RULE (copy LaTeX) |
| `backend/app/services/corpus.py` | approval fingerprint = text + structure hash |
| `backend/app/api/routes/documents.py`, `retrieval.py` | chunk/passage metadata in the API |
| `backend/app/db/models.py`, `database/migrations/sql/0004_engineering_chunks{,.down}.sql`, `database/migrations/versions/0004_engineering_chunks.py` | schema (reversible) |
| `backend/app/evaluation/chunking_benchmark.py`, `chunking_bench_driver.py`, `chunking_ground_truth.py`, `token_calibration.py` (new) | benchmark, size sweep, token calibration |
| `tests/backend/test_engineering_chunking.py`, `engineering_pdf.py` (new); `test_canonical_ingestion.py` (updated) | tests |
| `frontend/src/test/math.test.tsx` | LaTeX answer rendering |
| `docs/…` | this report, the guide, `docs/chunking/*.json|md`, README and RAG_INGESTION pointers |

A separate commit fixes a pre-existing failure of the self-maintenance work:
`registry_fingerprint()` hashed raw bytes, so a Windows CRLF checkout never matched the LF snapshot
(`test_validation_snapshot_is_current`).

## 4. Parser evaluation

| tool | available here | used for | evidence |
|---|---|---|---|
| PyMuPDF 1.28.2 | yes (dependency) | text layer, glyph geometry, drawings, table grids | all numbers below |
| DAYANERA ISO layout + formula geometry | yes (this work) | structure, equations | 13/13 exact formulas, 15/15 complete formula units |
| RapidOCR (ONNX) | yes | pages without text layer | ISO 54, ISO/TR 10064-1, ISO/TR 10828 (all pages scanned) |
| Docling 2.130 | separate venv `data/tools/parser-eval-venv` | optional table adapter | earlier evaluation (`RAG_INGESTION.md`): 40 % ISO 53 headings, formula model 1/3 equations, F1 0.18, > 80 min for ISO 21771 on CPU. Not re-run: CPU cost, and its formula output is model text (draft) |
| pdfplumber | no | – | would duplicate PyMuPDF chars/lines/tables |
| PaddleOCR | no | – | RapidOCR runs the same model family without the PaddlePaddle runtime |
| MinerU | no | – | AGPL-3.0, heavy; not needed for digital PDFs |

Layered strategy: native text layer + geometry → deterministic layout → OCR only where there is no text.

## 5. Chunking algorithm (summary)

Section tree → units → pieces → chunks (details in the guide §5–§12). A clause of ≤ 280 tokens with
≤ 3 equations and no table/figure is one leaf (the knowledge unit of the task statement: heading,
explanation, equation, number, variable definitions, page, clause). Larger clauses become parent
windows (≤ 700) with children; equations, tables, table row groups (header repeated) and figures are
always their own children. Narrative is packed to 160 tokens (hard 350) and split only at
sentence/line boundaries. Overlap is structural (heading path, symbol meanings, lead-in, repeated
table header), never a sliding window.

## 6. Old vs new (measured)

`python -m app.evaluation.chunking_benchmark run` — both pipelines end to end on the same 12 PDFs
(679 pages with text or OCR; 3 documents scanned), each in a throwaway database, same query set, same metric code, token
counts with the same calibrated estimator. Old = baseline commit `b69692f` (chunker **and** retrieval),
new = this work (chunker **and** retrieval). Full table: [`docs/chunking/benchmark.md`](chunking/benchmark.md).

| metric | old | new |
|---|---|---|
| retrievable chunks (+ parent windows) | 1 683 | 3 615 (+ 430) |
| tokens avg / median / p95 / max | 229 / 141 / 889 / 1 288 | 136 / 138 / 306 / 525 |
| chunks > 350 tokens | 15.9 % | 0.6 % (22 single rows of 20-column ISO 286-2 tables, flagged) |
| page-spanning chunks | 0 % (impossible) | 13 % |
| formula + number in one chunk | 15/15 | 15/15 |
| formula complete unit (number + clause/heading + lead-in in ONE chunk) | 12/15 | **15/15** |
| formula in exact normalised form | 0/13 | **13/13** |
| formula with LaTeX | 0/15 | **15/15** |
| table rows preserved / with caption + header | 8/8 / 8/8 | 8/8 / 8/8 |
| chunks with clause + heading | 91.2 % | 95.5 % |
| chunks with full heading path | 0 % | **95.5 %** |
| page range present | 100 % | 100 % |
| verbatim text traced to the cited page(s) | 99.9 % | 96.9 % (see limitations) |
| **retrieval, all 52 positive questions** Hit@1 / Hit@4 / MRR | 0.789 / 0.962 / 0.859 | **0.923 / 0.981 / 0.946** |
| answer grounding (context holds the answer facts) | 0.962 | 0.981 |
| citation correct @1 (right standard and page) | 0.808 | **0.962** |
| formula context complete (number + lead-in reach the model) | 0.864 | **1.0** |
| negatives abstained | 5/6 | 5/6 |
| **held-out (14 questions written after tuning)** Hit@1 / Hit@4 / MRR | 0.667 / 0.917 / 0.764 | **0.75** / 0.917 / **0.819** |
| held-out citation correct @1 / grounding | 0.667 / 0.917 | **0.833** / 0.917 |
| ingest time, 12 documents (same machine, OCR pages cached) | 55.2 s | 69.8 s |

Reading the numbers honestly:
* The structural gains (formula form, LaTeX, complete units, heading paths, bounded sizes, page ranges)
  hold on every document.
* The retrieval gains are largest on the question set the ranking changes were developed against
  ("tuning": Hit@1 0.825 → 0.975). On the **held-out** questions the gain is smaller (Hit@1 +1
  question of 12, MRR +0.055, citation +2 questions); Hit@4 and grounding are equal. The held-out
  set is small, so treat it as "no regression, modest improvement".
* Traceability is lower because chunks now contain structure-derived lines (normalised formulas) that
  are not verbatim on the page; the gate excludes known derived lines, the remainder are mostly
  inline-symbol merges ("db," joined across spans) and are still above the 95 % gate threshold.
* One negative question ("ISO 1328-1 price of a gear inspection machine") is answered in both
  versions: the corpus has a gear-inspection annex whose words match.

Token calibration (`python -m app.evaluation.token_calibration`, [`docs/chunking/token_calibration.json`](chunking/token_calibration.json)):
fitted estimator mean error 5.4 % / p95 15 % against Qwen3 counts, vs 23.5 % / 60 % for "chars/4".
Size experiment (`… chunking_benchmark sweep`, [`docs/chunking/size_sweep.md`](chunking/size_sweep.md)):
retrieval is flat between soft targets 110–250 and drops for large chunks (390/750) and when small
clauses are not kept whole (`no_section_leaf`); the middle of the plateau was chosen.

## 7. Tests

| suite | before this work | after |
|---|---|---|
| backend `pytest -m "not live and not corpus"` | 327 passed, **1 failed** (pre-existing: `test_validation_snapshot_is_current`, CRLF fingerprint — fixed in a separate commit) | **351 passed** |
| backend real-corpus tests `pytest -m corpus` (real PDFs through the whole pipeline: pilot gates, stage-2 scanned documents, evidence requirements, strict ISO regressions) | not run in the baseline (files renamed, see §11) | **32 passed** (with `DAYANERA_CORPUS_DIR` pointing at copies under the original names) |
| frontend `vitest` | 18 tests (not re-run before the change) | **19 passed** (+ LaTeX answer rendering) |
| frontend `npm run build` (`tsc --noEmit` type check + Vite build) | – | **pass** (bundle-size warning unrelated to this work) |

New test file `tests/backend/test_engineering_chunking.py` (19 tests) on a synthetic engineering standard
(`tests/backend/engineering_pdf.py`, invented text, real PDF geometry): repeated headers/footers and
page numbers suppressed; equation + number + heading path + clause + page + lead-in in one chunk; the
"where" legend linked to the run of equations before it; LaTeX derived from geometry (`d_{b} = d \cos
\alpha_{t}`, `\frac{…}{…}`) with glyph conservation; unexplained layout / uncertain math-font glyphs never
get LaTeX; fraction and subscript reconstruction; heading path on every body chunk; a sentence across a
page break stays one chunk citing `s. 1–2`; a continued table merged with caption, header and units in
every part; hard limit and sentence-boundary splits; parent/child integrity (and a broken link fails the
gate); content types and symbol glossary; gate traceability; DB storage of hierarchy, formulas (LaTeX
survives storage), tables and version metadata; "What is the base circle diameter formula in ISO 97771?"
→ Eq. (19) first, citation `s. 1`, context with `LaTeX (19)` and `db: base diameter` (LaTeX survives
retrieval); a table-value question → the table unit; safe re-ingest (dry run changes nothing, a verified
version is protected, apply replaces chunks, re-points stored citations, audits the revocation);
migration 0004 up → down → up.

Tests changed on purpose (behaviour changed): `test_canonical_ingestion.py` (chunks may span pages; new
content types), `test_api_contract.py` (migration head 0004), `test_pilot_corpus.py::test_pilot_quality_gates`
(ISO 21771 and ISO 53 now also list `formula_extraction` / ISOamsr glyphs for review), `test_git_safety.py`
(new source artefacts), and the corpus tests accept `DAYANERA_CORPUS_DIR`. The private-data guard now
forbids only the root `IMPLEMENTATION_REPORT.md` (its intent), not every file ending in that name.

## 8. Limitations

* **Equations** that the geometry cannot explain stay `needs_review` without LaTeX: ISO 21771 34/190
  (multi-line equations, nested big brackets, symbols set as images), ISO 1328-1 4/26, ISO 53 1/3
  (a degree sign stored as the letter `o` in a Type 3 font). OCR equations never get LaTeX (ISO/TR
  10828: 158). Overlines (T1T2) and matrices are not represented.
* **Symbols split across a line break** (`α` at the end of a line, `yt` on the next) are not joined;
  held-out questions `ho-21771-alphayt` and `ho-21771-pt` miss top-1 for this reason.
* **Lower-case one-letter labels** ("fundamental deviation h") are not recognised as labels
  (`ho-286-2-shaft-h`).
* **Very wide tables** (ISO 286-2, 20 columns): single rows with the repeated header exceed 350 tokens
  (22 chunks, max 525, flagged `oversize_unit`). Some merged header cells remain empty in the column names.
* The benchmark's formula/table ground truth covers 15 equations and 8 table rows of 5 standards;
  the question set is 58 questions (14 held-out). It measures, it does not prove.
* Math fonts without Unicode meaning (ISOamsr `W` = ⩾, Math-Pi `<` = ≤, Symbol `•` = ∞) are flagged,
  never rewritten; the page text keeps the wrong character until a reviewer confirms the page.

## 9. Unresolved document types

* Scanned standards (ISO 54, ISO/TR 10064-1, ISO/TR 10828): OCR text with clause structure, all
  `draft_extraction` + `needs_review`; tables and formulas in scans are not structured.
* Glyph-positioned BSI prints (ISO 14104): readable after the inline merge, but word spacing is
  sometimes lost ("Example ofusing").
* Equations embedded as images; landscape tables rotated on portrait pages (text kept, no grid).
* Non-ISO manuals without numbered clauses fall back to paragraph groups under unnumbered headings
  only when those are bold "Foreword/Introduction…" style headings; generic manuals need a heading
  rule for their own style.

## 10. Recommended future improvements

1. Join symbol fragments across line breaks (look-ahead at line starts that are subscript-sized).
2. Lower-case label recognition with a unit/deviation vocabulary (ISO 286 `h`, `js`, `k`).
3. Multi-line equations: group consecutive right-aligned equation rows that share one number.
4. A per-document formula review screen (rendered crop + plain + LaTeX + approve/correct), so
   reviewers can clear `formula_extraction` items one by one; store confirmations like OCR pages.
5. A dense retrieval channel with a local embedding model, evaluated on the held-out set first.
6. Grow the held-out question set with real user questions (from audit logs, anonymised) and keep it
   untouched by tuning.
7. OCR table structure for scanned standards (RapidOCR table model or a human-reviewed CSV import).

## 11. Rollout status and manual actions

Not applied to the running installation (the backend was running with the old code, and applying a
migration + re-ingestion is an operator decision). Steps: guide §"Rollout on an existing installation".
Found during this work, needing a decision:
* **The verified corpus of the running installation is empty.** The PDFs in `iso booklets` were
  renamed to `1.pdf … 12.pdf`; the watcher treated that as delete + new document, so the six approved
  versions (ISO 21771, 14104, 1101, 286-1, 286-2, 2490) belong to deleted documents. The same bytes
  are active again as new, unapproved documents. Re-ingestion with the new chunker requires new
  approvals anyway.
* **Duplicate split files**: `8-1.pdf`/`8-2.pdf` are the two halves of `8.pdf` (ISO 1101) and
  `12-1.pdf`/`12-2.pdf` of `12.pdf` (ISO/TR 10828); all are indexed as separate documents, so ISO 1101
  exists three times. Remove the split files from `iso booklets` unless they are wanted.
* Review before approval: the `formula_extraction`, `uncertain_symbol_glyphs`, `table_structure` and
  `ocr_pages` pages listed by `corpus-status` for each document.
* PyMuPDF licence (AGPL-3.0) for a network-served deployment — unchanged, pre-existing.
