# Canonical ingestion and retrieval (DAYANERA.ai)

DAYANERA stays the system of record: documents, versions, pages, chunks, provenance,
verification status, access scopes, audit, intent routing, the calculation engine and the
grounding/answer policy. Parsers are replaceable layers that feed its canonical entities.

## Data flow

```
iso booklets/*.pdf ──watcher──▶ documents / document_versions (immutable raw copy, SHA-256, source path+mtime)
      │                                    corpus_status = candidate
      ▼ ingestion job (app/ingestion/pipeline.py)
 PDF validation (header, open, encryption, xref repair, EOF)             extractors/pdf.py
 native text per page (PyMuPDF) + Symbol-font Greek recovery            parsing/symbols.py
   └ rebuilt text layer for glyph-positioned PDFs / RapidOCR for pages without text (draft)
 ISO structure blocks: clauses, headings, tables (rows), equations,     parsing/iso_layout.py
   figure keys, TOC, rotated tables, page furniture
 [optional] Docling layer out of process (PDF_PARSER=hybrid|docling)    parsing/docling_adapter.py
 structure-aware chunks with lineage                                    chunker.py
 document_pages + document_chunks (tsvector + GIN built on insert)
 index verification (every chunk has lexemes) ─▶ ingestion_status = indexed | failed
 quality gates ─▶ corpus_status = extracted | needs_review | failed    quality.py
      │
      ▼ owner approval (API / CLI), audited, bound to the extraction fingerprint   services/corpus.py
 corpus_status = verified  ─▶  the ONLY state retrieval and the calculation engine use
```

Re-indexing keeps `verified` only if the new extraction has the same fingerprint (pipeline
version, parser versions, ordered chunk hashes); any change drops the version out of the
verified corpus until it is approved again. A new file version always needs approval.

## Chunk lineage (document_chunks)

`document_id, version_id, page_id, chunk_index, page_start, page_end, clause, heading,
content_type (text|table|formula|list|note|definition|front_matter), standard_code,
extraction_method, extraction_confidence, confidence_status, source_hash (raw file SHA-256),
content_hash, parser, parser_version`. Chunks never cross clause or page boundaries
(page-exact citations); tables are separate chunks (split by rows, caption repeated);
a formula stays with its lead-in sentence and legend. Model-generated text (e.g. Docling
formula LaTeX) is stored as `draft_extraction`, like OCR, and is never evidence unreviewed.

## Quality gates (app/ingestion/quality.py)

| gate | fail / review when |
|---|---|
| pdf_valid | review: xref repaired, EOF marker missing (blocking errors stop extraction) |
| text_coverage | review < 0.9 of non-blank pages with text, fail < 0.5 |
| ocr_pages | review: any OCR page with ≥ 200 chars (short cover pages are listed only) |
| page_quality | review: page score < 0.9 (printable ratio, U+FFFD, OCR confidence) |
| uncertain_symbol_glyphs | review: Symbol/Math-Pi glyphs without a reliable Unicode meaning (ISO 53: "•"=∞, "<"=≤) |
| standard_code | review: missing, non-canonical, number/part/edition year differs from the file name |
| structure | review: no ISO clause structure (page-window chunks only) |
| critical_content | review: missing critical tokens/clauses/tables/equations of known standards (αP, αFP, ρfP, mn, mt, da, df, FD, FE, …) |
| chunk_lineage | **fail**: no chunks, or a chunk without lineage |
| fulltext_index | **fail**: a chunk with text but no lexemes |
| model_generated_content | review: layout/formula model output present |
| traceability | review: < 95 % of text chunks found verbatim on their cited page |
| content_coverage | review: < 97 % of substantive page lines reach the page's chunks |

## Retrieval (app/services/retrieval.py)

1. query plan: ISO codes, Turkish→English concepts (glossary), symbols (αP↔aP), clause
   numbers ("madde 5.4", "4.2.4"), equation numbers ("Eşitlik (2)"), labels ("Table 2")
2. exact channel: symbols / clause numbers as exact `simple` lexemes, clause column, numbered formulas
3. lexical channel: `tsvector` + GIN (english + simple; clause/heading weight A)
4. metadata filter: verified-corpus area, active document, active indexed **approved** version,
   verified/user-confirmed chunks, user scope, requested standard code
5. reciprocal rank fusion (k = 60) of the channels
6. deterministic rerank: term coverage (the relevance gate score) + ordering-only boosts:
   exact symbols, symbol-keyed table rows, standard code, clause, equation, heading phrase,
   heading title = asked term, requested structure (table/formula), intent subtype, front-matter penalty;
   symbols are resolved through the SAME standard's symbol table (αP → "Pressure angle")
7. grounding + citation policy and answer generation are unchanged (chat.py, grounding.py)

No dense/vector channel: the pilot reaches Recall@4 0.98 / MRR 0.93 / 100 % grounding
lexically, pgvector is not installed in the local `postgres:16-alpine` image, and no embedding
model is available locally. Add it only when an evaluation shows lexical recall is insufficient
(e.g. paraphrased questions); the channel would plug into the same RRF join.

Formula lookup vs. calculation: a formula question without numeric inputs is
`standards_formula_lookup` (retrieval + FORMULA_RULE: quote, never rebuild or compute);
only questions with parsed numeric inputs reach the deterministic calculation engine.

## Parser selection (pilot: ISO 53:1998, ISO 21771:2007)

`python -m app.evaluation.parser_comparison --docling-dir data/eval/docling_noformula`
(every variant through the real pipeline in a throwaway database; 21 labelled Turkish
queries + 2 negatives, `app/evaluation/pilot_ground_truth.py`, formulas verified against the rendered pages).

| metric | before (page windows) | **pymupdf + ISO layout** | hybrid (+Docling tables) | Docling layout |
|---|---|---|---|---|
| Recall@4 / Recall@10 | 0.69 / 0.90 | **0.98 / 1.00** | 0.98 / 1.00 | 0.71 / 0.71 |
| Precision@4 | 0.24 | **0.31** | 0.31 | 0.25 |
| MRR | 0.52 | **0.93** | 0.93 | 0.62 |
| answer grounding (context holds the answer) | 71 % | **100 %** | 100 % | 57 % |
| negatives abstained | 2/2 | 2/2 | 2/2 | 2/2 |
| ISO 53 headings / clauses | 0 / 0 | **100 % / 100 %** | 100 % / 100 % | 40 % / 35 % |
| ISO 21771 headings / clauses | 0 / 0 | **100 % / 100 %** | 100 % / 100 % | 98 % / 100 % |
| table rows exact (53 / 21771) | 0 / 0 | **100 % / 100 %** | 93 % / 100 % | 93 % / 100 % |
| equations detected (53 / 21771) | 0 / 0 | **3/3 / 8/8** | 3/3 / 8/8 | 0 / 0 |
| formula token F1 (53 / 21771) | 0 / 0 | **0.92 / 0.90** | 0.92 / 0.90 | – |
| exact formula (2-D math) | 0 | 0 | 0 | 0 |
| symbol accuracy (53) | 82 % | **100 %** | 100 % | 91 % |
| traceability to source page | 100 % | **100 %** | 100 % | 44 % / 73 % |
| ingest time (both) | 0.9 s | 9 s | 9 s + Docling 178 s | 9 s + Docling 178 s |

Docling formula enrichment (ISO 53): 1/3 equations detected, token F1 0.18, "=" rendered as
`\Box`, 96.5 s; on ISO 21771 it did not finish within 80 min on CPU. MinerU was not needed:
native extraction passed, no pilot page needs OCR, tables are exact; it is AGPL-3.0 and heavy.
Evaluate MinerU/Docling OCR only for the scanned documents (ISO 54, ISO/TR 10064-1,
ISO/TR 10828) if their human OCR review proves too costly.

## Tools and licences

| tool | role | licence |
|---|---|---|
| PyMuPDF 1.28.2 (existing dependency) | text layer, geometry, tables | **AGPL-3.0 or Artifex commercial** — legal review needed for a network-served app |
| RapidOCR / ONNX Runtime (existing) | OCR fallback (draft) | Apache-2.0 / MIT |
| PostgreSQL 16 tsvector + GIN (existing) | lexical + exact retrieval | PostgreSQL |
| Docling 2.130 (optional, separate venv) | layout/tables adapter, evaluated | MIT; models Apache-2.0 (layout), CDLA-Permissive-2.0 (TableFormer, CodeFormula) |
| RAGFlow, Haystack | design references only (deep-doc chunking, title weighting, RRF joiner, retrieval diagnostics) | Apache-2.0 (not installed) |
| MinerU | not used | AGPL-3.0 |

## Runbook

```powershell
# 0. stop the backend (its worker would process jobs with the old code) and back up
docker exec dayanera-postgres pg_dump -U dayanera -Fc dayanera > data\backups\before-0002.dump
# 1. schema (reversible: `alembic downgrade 0001_initial` (run in backend/) drops only the 0002 objects)
cd backend; .venv\Scripts\python -m app.cli migrate
# 2. re-process every document through the canonical pipeline (all start as 'candidate')
.venv\Scripts\python -m app.cli reindex
.venv\Scripts\python -m app.cli corpus-status
# 3. pilot approval (a written note is required for needs_review)
.venv\Scripts\python -m app.cli corpus-approve --code "ISO 21771" --by admin --note "pilot"
.venv\Scripts\python -m app.cli corpus-approve --code "ISO 53" --by admin --note "s.3 •=∞, s.6 <=≤ checked"
# 4. later stages only after the pilot tests pass; scanned documents: confirm OCR pages in the
#    review queue first, then approve. Dry run of all gates without a database:
.venv\Scripts\python -m app.evaluation.corpus_audit
```

Tests: `pytest -m "not live and not corpus"` (synthetic), `pytest -m corpus` (real PDFs,
pilot + stage 2), `app.evaluation.parser_comparison` (metrics; outputs under data/eval).

## Not for production

`geminis work/supabase_gear_standards_migration.sql` (parallel `public.documents` model, hard-coded
project/Drive IDs, marks everything `indexed`), `geminis work/supabase_ingest.py` (service-role REST
upsert of metadata only) and every `geminis work/chunks_*.json` (comparison artefacts: page counts
wrong for 8/12 files, ISO 1101 cites 70/158 pages, ISO 4468 19/44, scanned TRs summarised into
6 chunks). The PDFs in that folder are byte-identical to `iso booklets`.
