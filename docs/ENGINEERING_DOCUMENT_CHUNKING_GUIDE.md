# Engineering document chunking — maintainer guide

For the people who will run, debug and change DAYANERA's PDF/booklet ingestion after this work.
It explains **what the pipeline does, why, where the code is, and how to check it**. All numbers
quoted here were measured on DAYANERA's own corpus (the 12 PDFs in `iso booklets/`) and are
reproducible with the commands in this guide. The full measured comparison is in
`docs/chunking/benchmark.md`; the implementation report is `docs/ENGINEERING_CHUNKING_IMPLEMENTATION_REPORT.md`.

Commands are for Windows PowerShell and are run from `backend\` unless stated otherwise
(`.venv\Scripts\python ...`). On Linux use `.venv/bin/python`.

---

## 0. The pipeline at a glance

```
PDF (iso booklets\*.pdf)                     watcher -> documents / document_versions (raw copy, SHA-256)
 │
 ├─ validate + classify pages                extractors\pdf.py        validate_pdf, is_degenerate
 ├─ pass 1: text lines of every page         parsing\iso_layout.py    page_lines   (PyMuPDF rawdict: glyph boxes,
 │                                                                                  baselines, fonts, Symbol→Greek)
 ├─ learn running headers/footers            parsing\iso_layout.py    repeated_furniture
 ├─ pass 2 per page:
 │   ├─ table grids                          parsing\iso_layout.py    page_tables (PyMuPDF find_tables)
 │   ├─ join inline symbols/subscripts       parsing\iso_layout.py    merge_inline_fragments
 │   ├─ structure blocks                     parsing\iso_layout.py    detect_blocks: headings, clauses, tables,
 │   │                                                                equations, "where" legends, figures, notes
 │   └─ equation reconstruction              parsing\formula.py       reconstruct: 2-D layout -> raw/plain/LaTeX
 ├─ OCR fallback (page without text layer)   extractors\pdf.py + ocr.py   RapidOCR, draft_extraction
 ├─ hierarchical document model              structure.py             build_model: sections, units, glossary,
 │                                                                     cross-page continuation, continued tables
 ├─ engineering chunker                      chunker.py               chunk_pages: leaf / parent / child chunks,
 │                                                                     content types, context, metadata
 ├─ token counting (Qwen-calibrated)         tokens.py                estimate_tokens
 ├─ persistence + quality gates              pipeline.py, quality.py  _write_chunks, evaluate (16 gates)
 │                                                                     corpus_status: extracted | needs_review | failed
 ├─ owner approval (human)                   services\corpus.py       corpus-approve -> verified
 ├─ retrieval + reranking                    services\retrieval.py    search: exact + lexical channels, RRF, boosts
 ├─ parent-context expansion                 services\retrieval.py    build_context: variables, LaTeX, parent window
 └─ Qwen answer + grounding check            services\chat.py, inference\prompts.py (FORMULA_RULE)
```

Everything is deterministic. **No language model writes or repairs text, formulas or LaTeX during
ingestion.** Anything the geometry cannot explain is marked `needs_review` and shown to a human.

---

## 1. What was wrong with the previous approach

The previous chunker (`chunker.py` before this change, commit `b69692f`) was already
structure-aware in a simple way, but it lost engineering context in eight measurable ways:

| problem | effect | how it is fixed now |
|---|---|---|
| fixed 1 200-character cap with a 150-character sliding overlap | long clauses cut mid-sentence; p95 509 tokens, max 933 | token-based limits, splits only at sentence/line boundaries |
| **every page break ended a chunk** (`page_start == page_end` was an invariant) | a clause, a sentence or a table continued on the next page became two unrelated chunks; `Table 11 (continued)` lost caption and header | document model across pages; chunks may cite `s. 25–26` |
| displayed equations linearised glyph by glyph | ISO 21771 Eq. (19) came out as `db = d cos α t = z mt cos α t = z m cos n cos β α t = tan2 2 α z m n + n cos β d b, is given by (19)`; **0/13** formulas in exact form | 2-D reconstruction from glyph geometry + drawn bars/radicals: **13/13** exact, LaTeX for every one of them |
| inline symbols split onto separate "lines" | `angle,\nα\nyt:`; symbols unsearchable | `merge_inline_fragments` rejoins `αyt` on its text row |
| no heading path | a chunk knew its clause number and last heading only | `heading_path` = every ancestor heading (96 % of chunks) |
| running headers/footers of BSI prints kept | `BS EN ISO 1101:2017`, page numbers and `© … All rights reserved\x08` (hidden backspace) inside notes | learned per document from repetition at a fixed position; control characters removed |
| the "clause number + title" merge also ran inside tables | ISO 286-2 cells `3` and `6` ("above 3 up to 6") fused into `3 6` | never applied inside a table box |
| no formula/variable/table metadata, no parent/child | the model received an 800-character window that could cut an equation away from its definitions | formula payloads (raw, plain, LaTeX, variables), table payloads, parent windows, context expansion |

The old pipeline also stored chunk `content_type` from the mix of blocks it happened to hold,
so a long clause with one equation became `formula` although most of it was prose.

## 2. Which parsers are used

| job | tool | status |
|---|---|---|
| text layer, glyph positions, fonts, drawings (fraction bars, radicals, `|z|` rules), table grids | **PyMuPDF 1.28.2** (`pymupdf`, already a dependency) | primary |
| document structure (clauses, captions, legends, tables, equations) | **DAYANERA's own deterministic ISO layout** (`parsing/iso_layout.py`) | primary |
| equations | **DAYANERA's geometry reconstructor** (`parsing/formula.py`) | primary |
| pages without a text layer (scans) | **RapidOCR** (ONNX Runtime, CPU; models inside the wheel) | fallback only |
| table structure / layout model | **Docling 2.130** in a separate venv (`PDF_PARSER=hybrid`) | optional, off by default |
| pdfplumber, PaddleOCR, MinerU, Tesseract | not installed | not used (see §3) |

## 3. Why these parsers

* **PyMuPDF** gives everything the ISO layout needs in one pass: per-character boxes and baselines
  (`rawdict`), font names (Symbol-font Greek recovery, math-font detection), vector drawings
  (fraction bars, radical signs, absolute-value rules) and a table grid finder. On clean digital
  ISO PDFs it is exact and fast (the 12 corpus documents, 679 pages incl. cached OCR pages: ~70 s).
  *Licence*: AGPL-3.0 or Artifex commercial — see `docs/RAG_INGESTION.md` (legal review needed
  for a network-served deployment).
* **Docling** was evaluated before (see `docs/RAG_INGESTION.md`, parser comparison): its layout
  model found 40 % of ISO 53 headings (PyMuPDF layout 100 %), its formula model recognised 1 of
  3 ISO 53 equations (token F1 0.18, `=` rendered as `\Box`) and did not finish ISO 21771 in 80
  minutes on CPU. It is kept as an optional table adapter only. Its formula output would be
  model-generated text; DAYANERA's rule is that such output is a draft that needs review.
* **pdfplumber** would duplicate what PyMuPDF already provides (chars, lines, tables) with a
  second PDF parser to maintain; it is not installed and not needed.
* **RapidOCR** is the ONNX port of the PaddleOCR detection/recognition models: PaddleOCR quality
  without the heavy PaddlePaddle runtime. It only runs on pages whose text layer has < 40
  characters, results are cached per file hash and page, and OCR text is always
  `draft_extraction` (never evidence until a person confirms it). **Clean digital PDFs are never OCRed.**
* No new dependency was added by this work.

## 4. The exact extraction pipeline (`extractors/pdf.py: extract_pdf`)

1. `validate_pdf`: header, open, encryption, xref repair, EOF → blocking errors stop here.
2. **Pass 1** — `iso_layout.page_lines(page)` for every page: PyMuPDF `rawdict`; each span becomes
   a `Seg` (text, tight ink box, font size, **baseline of its first real glyph**, font, bold).
   Symbol-font letters are mapped to Greek (`parsing/symbols.py`), control characters removed.
3. `repeated_furniture(pages)`: lines in the top 11 % / bottom 11 % of the page that recur at the
   same position on ≥ 30 % of pages (min. 3) → this document's running headers/footers.
4. **Pass 2** per page:
   * degenerate text layer (one glyph per "word") → `rebuild_from_chars`, no blocks;
   * `page_tables` (grids) → `merge_inline_fragments` → page text → `detect_blocks`.
5. Pages with < 40 characters → RapidOCR (cached in `data/indexes/ocr-cache/<sha256>/`).
6. Uncertain glyphs (Symbol/Math-Pi/ISOams fonts without Unicode meaning) are counted per page.
7. Optional Docling merge (`PDF_PARSER=hybrid|docling`).

The page text stored in `document_pages.text` is the raw text layer (with inline symbols joined);
it is what citations and traceability checks refer to.

## 5. The exact chunking strategy (`structure.py` + `chunker.py`)

1. **Document model** (`structure.build_model`): headings open sections at their clause depth;
   every other block becomes a *unit* of the current section (paragraph, formula, legend,
   table, figure, note, example, list, caption, toc). Continuations across pages are merged
   (§12). Symbol tables and legends build the **symbol glossary**.
2. **Pieces** (`chunker._section_pieces`): each formula unit becomes a FORMULA piece
   (lead-in + formula + legend + "(See Figure n.)"); each table becomes one TABLE piece, or a
   table overview + TABLE_ROW_GROUP pieces; figures become FIGURE_CAPTION pieces; consecutive
   narrative units are packed up to `soft_target` tokens (never across a formula/table/figure);
   an oversized narrative unit is split at sentences, then lines, then words.
3. **Chunks** (`chunker._emit_section`):
   * a section with ≤ `leaf_section_max` tokens, ≤ 3 formulas and no table/figure becomes
     **one `leaf` chunk** — the complete knowledge unit (heading + explanation + equations +
     legends). Example: ISO 21771 4.3.10 (161 tokens) = heading, "The base diameter, db, is
     given by", Eq. (19) and (20);
   * otherwise the section becomes **`parent`** window(s) (≤ `parent_max` tokens, never mixing two
     clauses) with **`child`** chunks (the pieces). A table with row groups is itself the parent
     of its row groups.
   * a leaf is typed by its defining content (formula, table, definition, normative, procedure) or else by
     the kind that holds most of its text (`chunker.leaf_kind`) — one NOTE does not make a clause a note;
   * outside a real clause (the document root: cover and title pages) nothing is packed across a page
     break, because nothing says two such pages belong together.
4. **Content type** of every chunk (§11 of the task list): FORMULA, FORMULA_CONTEXT,
   VARIABLE_DEFINITION, TABLE, TABLE_ROW_GROUP, FIGURE_CAPTION, DEFINITION,
   NORMATIVE_REQUIREMENT, PROCEDURE, EXAMPLE, NOTE, REFERENCE, GENERAL_TEXT, FRONT_MATTER,
   SECTION (parents). Stored in lower case (`formula`, `table_row_group`, …). Rules are in
   `chunker.classify`: e.g. "shall/must" → normative_requirement; any paragraph of a section
   that has equations → formula_context; clause 3 "Terms and definitions" → definition.
5. **Context** (structural overlap, §20): `doc code › heading path` plus, for formulas and
   symbol-keyed tables, the meaning of each symbol (`db: base diameter`) and table columns.

## 6. How headings are detected (`iso_layout._heading`)

A line is a heading when it is a clause number (+ title on the same baseline, merged by
`_merge_baseline_numbers`) **and** the number advances plausibly from the last accepted clause
(`plausible_next`: next sibling, first child, or up to 2 steps; a bold titled heading may skip up to 5).
Bold titles are preferred; unnumbered bold "Foreword/Introduction/Contents/Bibliography" and
"Annex X (informative) Title" are headings too. Contents pages (dot leaders) never produce headings.
The plausibility rule is what keeps figure keys ("1 Datum line") and table-of-contents entries out.

## 7. How clauses are detected

Clause identity is the heading number (`4.3.10`, `A.2`, `Annex A`). A numbered line *with running
text* ("5.1 The characteristics …") opens an untitled sub-clause. `Section.path` (heading texts from
the root) is stored per chunk as `heading_path`; `clause` is the nearest numbered clause.

## 8. How equations are detected (`iso_layout.detect_blocks` + `parsing/formula.py`)

1. Anchor: a line `(n)`, `(A.12)` in the right 40 % of the page = equation number.
2. Band: glyph runs left of the number, within ±45 pt vertically, bounded by the nearest prose
   line / heading / "where" / text label above and below and by the midpoint to neighbouring
   equation numbers. Vertical positions come from glyph baselines, not PDF line boxes (BSI prints
   report line boxes several lines tall).
3. Primitives in the band (`formula.primitives`): horizontal strokes = fraction bars, short vertical
   strokes in pairs = `|z|`, a stroked/filled path with diagonal segments = radical sign; white fills
   are masks.
4. `formula.reconstruct`: atoms (functions, numbers, Greek, letters, operators; tall bracket pieces
   `⎛⎜⎝` collapsed) → radicals → absolute values → fractions (longest bar first, recursively) →
   sub-/superscripts by size and baseline offset → one main baseline.
5. **Safety checks** — any failure ⇒ `needs_review`, **no LaTeX**, the chunk shows the PDF glyph line:
   a full-size glyph off the baseline, a glyph cut by a fraction bar, a bar with glyphs above and below
   that no fraction explains, an unknown glyph, a glyph of a math font without Unicode meaning
   (ISOamsr `W` = ⩾), a raised `o` after a number (a degree sign stored as a letter), glyphs lost,
   the region truncated at the window edge, OCR input, or LaTeX that fails `latex_problems`.
6. `glyphs_conserved` proves every output character is a glyph of the PDF (plus structure characters
   from drawn primitives).

Measured on the corpus (current code): ISO 21771 156/190 equations reconstructed, 34 flagged;
ISO 1328-1 22/26; ISO 4468 6/6; ISO 53 2/3 (Eq. (3) has the ambiguous degree glyph).
Inline equations inside running text ("a pitch p = π m") are recorded when their left side is a
symbol of the document's glossary (`chunker.inline_equations`).

## 9. How variable definitions are associated

In priority order, each formula payload gets `variables = [{symbol, description, unit, source}]`:
1. the **"where" legend** directly after the equation — it belongs to the whole run of consecutive
   equations before it (`structure._add`, legend branch); parsed by `iso_layout.parse_variable`
   (`d is the reference diameter, mm;`);
2. the **document glossary**: rows of symbol tables (`Symbol | Description | Unit | Used in`,
   e.g. ISO 21771 3.1, 189 symbols; ISO 53 Table 1) and legends of other clauses;
3. the **lead-in sentence** ("The base diameter, db, is given by") is copied into the formula chunk;
   when it starts with "It/This/Hence …" the previous sentence is taken as well.
`lhs` (the quantity the equation defines) is the symbol left of the first `=` (`cos αt = …` → `αt`).

## 10. How LaTeX is generated and preserved

* LaTeX is produced **only** by `formula.latex(tree)` from the reconstructed tree: Greek →
  `\alpha`, functions → `\cos`, `inv` → `\operatorname{inv}`, fractions → `\frac{}{}`, roots →
  `\sqrt{}`, `|z|` → `\lvert z \rvert`, subscripts `_{…}`, decimal comma `0{,}25`.
* Every formula keeps three forms in `document_chunks.formula` (JSON list):
  `raw` (glyph runs as the text layer stores them — provenance), `plain` (normalised one-line form
  in the document's own symbol spelling, used for search and shown in the chunk text) and `latex`
  (or `null`), plus `status` (`exact` | `reconstructed` | `needs_review`), `confidence`, `reasons`,
  `structures`, `symbols`, `lhs`, `variables`, `lead_in`, `legend`.
* Retrieval passes `LaTeX (19): …` lines to Qwen; `FORMULA_RULE` (`inference/prompts.py`) tells the
  model to copy that LaTeX **verbatim** into `$$…$$`, or to quote the text line when there is no LaTeX.
  The chat UI renders `$…$`/`$$…$$` with KaTeX (`frontend/src/components/MessageView.tsx`).
* The corpus approval fingerprint covers the LaTeX (`meta_hash`), so a changed LaTeX needs re-approval.

## 11. How tables are handled

* Grid: PyMuPDF `find_tables` cell boxes, filled with DAYANERA's cleaned text lines (Greek
  recovered, watermark glyphs removed, subscripts joined). If > 20 % of lines find no cell, the old
  line-geometry rows are used (`method: lines`).
* Header rows: leading rows whose non-empty cells are ≥ 50 % bold (max. 3); column names join the
  header rows, merged header cells name every column they span.
* Units: unit notes above the grid ("Deviations in micrometres"), unit tokens in header cells.
* Footnotes: `a …` lines directly below the grid. Caption and number: "Table n —" or "Table n (continued)".
* Stored in `table_data` (JSON): number, caption, columns, header_rows, rows (cells), units,
  unit_note, footnotes, continued_pages, row_range (row groups), alignment_uncertain.
* Chunks: a table ≤ `table_leaf_max` tokens is one chunk; a larger one is a table overview (caption,
  unit note, header, row count) with TABLE_ROW_GROUP children of ≈ `table_group_target` tokens that
  **each repeat caption, unit note and header rows**.
* A header cell like `1 2` means the grid merged two columns that the PDF separates only by spacing
  (ISO 286-2): the table is flagged `alignment_uncertain` → chunk `needs_review`, gate `table_structure`.

## 12. How multi-page content is handled

* A paragraph that ends without closing punctuation continues into the next page's first paragraph
  when that starts in lower case (or with `(`, `,`, `–`): one unit, `pages = [n, n+1]`.
* `Table n (continued)` rows are appended to Table n (its repeated header rows are dropped); an
  uncaptioned grid of the same width at the top of the next page is treated the same way.
* A clause's section simply continues over the page break (the page never opens a section).
* Chunks store `page_start` and `page_end`; the locator reads `s. 25–26`; `page_id` points at the
  first page. The traceability gate checks verbatim lines against the concatenated page range.

## 13. How the OCR fallback works

`extract_pdf`: a page whose text layer (after cleaning) has < 40 characters is rendered at
`OCR_DPI` (200) and read by RapidOCR, at most `OCR_MAX_PAGES_PER_DOCUMENT` pages. Results are cached
(`data/indexes/ocr-cache/<sha256>/page-0001.json`). OCR pages get `method = ocr`, their chunks are
`draft_extraction` (never evidence until confirmed) and `validation_status = needs_review`
(`ocr_text`). Pages without layout blocks (OCR, rebuilt text layers) get a text-only structure
pass (`structure.text_blocks`: plausible numbered headings, notes, captions, equation-number
lines), so scanned documents are chunked by clause, not by page window. OCR equations never get LaTeX.

## 14. How metadata works

`document_chunks` columns (migration `database/migrations/sql/0004_engineering_chunks.sql`):

| column | meaning |
|---|---|
| `document_id`, `version_id`, `page_id`, `chunk_index` | identity (existing) |
| `page_start`, `page_end`, `locator` | page provenance (`s. 25` / `s. 25–26`) |
| `clause`, `heading`, `heading_path[]` | clause and full heading path |
| `content_type` | engineering type (§5) |
| `chunk_role`, `parent_id` | `leaf` / `parent` / `child` and the parent chunk |
| `context` | structural overlap (doc › path, symbol meanings, table columns) — indexed, weight C |
| `equation_numbers[]`, `table_numbers[]`, `figure_numbers[]`, `symbols[]`, `units[]` | labels |
| `formula` (JSON), `table_data` (JSON) | formula and table payloads (§10, §11) |
| `token_count`, `chunker_version` | Qwen-calibrated size, `engineering-chunker/1` |
| `validation_status` (`ok` / `needs_review`), `meta` (JSON: key, child_keys, review_reasons, language, document_title) | chunk validation |
| `standard_code`, `source_hash`, `content_hash`, `meta_hash`, `parser`, `parser_version`, `extraction_method`, `extraction_confidence`, `confidence_status` | provenance (existing + `meta_hash`) |

Version-level (`document_versions.metadata`): `chunking` (config, counts, formula statistics) and
`symbol_glossary`; `quality_report` holds the gates and the approval fingerprint.
Document title, standard number, edition year and language are on `documents` /
`document_versions` and in `meta` — they are not duplicated in columns.

## 15. How parent/child chunks work

* Parents are **context windows** — never search hits (`retrieval._SEARCHABLE`).
* A child's `parent_id` points at its window; the parent's `meta.child_keys` lists the children;
  gate `chunk_structure` (blocking) checks both directions and page order.
* `build_context` gives the two best hits the rest of their parent window when the character budget
  allows (`ÜST BAĞLAM (aynı madde)`), with the child's own text replaced by `[…]`. When the window does
  not fit, `focused_parent` keeps the lines that name the question's symbols, labels or concepts first
  (then the lines around the hit), in document order with `…` for gaps — e.g. the ISO 14104 note
  "Rehardened areas (FE) appear as white etching areas … (FD) …" reaches the model for "Class FD / FE".

## 16. How embeddings are generated

**They are not.** There is still no dense/vector channel, deliberately:
* the benchmark reaches Hit@4 0.98 / Recall@4 0.97 over all 52 answerable questions and Hit@4 0.92 on the
  held-out questions lexically (`docs/chunking/benchmark.md`);
* no embedding model is installed locally (`ollama list` has only Qwen chat models) and the local
  `postgres:16-alpine` has no pgvector.
The chunks are prepared for one: `context` + `text` is the natural embedding input (heading path
and symbol meanings included). To add it: pick a local model (e.g. an Ollama embedding model),
add `embedding vector(n)` in a new migration, compute it in `pipeline._write_chunks`, add a
`dense` channel to `retrieval.search` that joins the existing reciprocal-rank fusion, and re-run
`chunking_benchmark` — keep it only if the held-out numbers improve.

## 17. How retrieval works (`services/retrieval.py: search`)

1. `plan_query`: ISO codes, glossary concepts (Turkish → English), symbols (`αP` ↔ `aP`), clause and
   equation numbers, labels (`Table 2`, `Class FD`, `deviation H`).
2. Channels over **leaves and children** of verified, active, approved versions: exact
   (`simple` lexemes of symbols, clause numbers, label phrases like `deviation <-> h`, equation
   numbers in formula chunks) and lexical (`tsvector`: clause+heading weight A, context weight C,
   text weight D).
3. Reciprocal rank fusion (k = 60), then the deterministic rerank (§18).
4. `build_context` (§15) for the chat; `validate_answer` checks the answer against exactly what the
   model saw (`prompt_text`).

## 18. How reranking works

`order = term_coverage + 0.15 × fused_rank_norm + Σ boosts` (ordering only; the relevance gate
uses `term_coverage` alone). Boosts (`metadata_boosts`):

| boost | when | value |
|---|---|---|
| `exact_tokens` | typed symbol / label found | ≤ 0.3 |
| `standard_code` | chunk of the named standard | 0.25 |
| `clause`, `equation` | asked clause / equation number | 0.2 |
| `content_type` / `requested_structure` | intent subtype kind; asks for a table/formula | 0.05–0.2 |
| **`wanted_kind`** | question kind (formula / table / definition, `question_kind`) matches the chunk's engineering type | 0.05–0.25 |
| **`defines_quantity`** | a formula whose left-hand symbol means what the question names ("base diameter" → `db = …`) | 0.3 |
| `symbol_row` | a table row keyed by the asked symbol | 0.15 |
| `heading_phrase`, `defines_term` | question phrase in the **heading path** / the clause title | 0.1 / 0.2 |
| **`informative_annex`** | chunk in an informative annex, annex not asked for | −0.05 |
| `front_matter` | contents / foreword | −0.2 |

`wanted_kind` for tables has full weight only when the question itself says table/value/tolerance/…
(or names a label); when only the intent classifier says "value lookup" it is multiplied by 0.4 —
lists and sentences answer such questions too.

After ranking, **`diversify`** keeps at most `MAX_SIBLINGS = 2` children of the same parent in the top-k
(further siblings only fill free slots): otherwise the row groups of one big table can take every slot
and push out the clause that answers the question (found by the strict regression
"ISO 4468 hob kalite sınıfları nelerdir?").

## 19. Chunk size decisions

Limits are in **Qwen tokens**, estimated by `tokens.estimate_tokens`, a linear model fitted on 214
DAYANERA chunks against exact counts of the local `qwen3:14b` (`app.evaluation.token_calibration`):
mean error 5.4 %, p95 15 %. The "4 characters per token" rule is off by 23.5 % on average and by
60 % at p95: formula chunks cost 0.48 tokens/char and table row groups 0.55, prose ~0.26.

Values (`chunker.ChunkingConfig`), chosen by the size experiment
(`python -m app.evaluation.chunking_benchmark sweep`, results in `docs/chunking/size_sweep.md`):

| setting | value | why |
|---|---|---|
| `soft_target` | 160 | packing target for paragraph groups; 4 passages fit the 3 000-char context budget |
| `hard_max` | 350 | no retrieval chunk is larger (except an indivisible unit, flagged `oversize_unit`) |
| `min_useful` | 25 | narrative pieces smaller than this join a neighbour **of the same section** |
| `leaf_section_max` | 280 | a clause up to this size stays one knowledge unit; removing this rule (`no_section_leaf`) was the worst configuration |
| `parent_max` | 700 | a parent window must fit the model context together with its child |
| `table_leaf_max` / `table_group_target` | 280 / 200 | tables are the most token-dense content |
| `lead_in_max` | 90 | the copied introducing sentence stays short |

Different content types therefore have different effective sizes: a formula leaf is typically
60–200 tokens, a table row group ≤ 200, narrative groups ≤ 160 (hard 350). Small complete units
(one-sentence clauses, figure captions, notes) are intentionally **not** merged with unrelated
neighbours — see the "tiny chunks" line of the benchmark.

## 20. Overlap decisions

There is **no sliding character overlap**. Overlap is structural:
* every chunk carries `context` (doc code › heading path);
* formula chunks carry their lead-in sentence (a copy of the end of the preceding paragraph) and
  the meanings of their symbols; table row groups repeat caption, unit note and header rows;
* parents hold the full window, children the pieces; the chat expands children with their parent.
The only duplicated source text is therefore the lead-in sentence and the table header — exactly
what a reader needs to understand the unit.

## 21. Failure modes (and what you will see)

| failure | symptom | where flagged |
|---|---|---|
| equation with unusual layout (multi-line, nested big brackets, stray glyph) | chunk shows the raw glyph line, no LaTeX | `formula.status = needs_review`, gate `formula_extraction` |
| equation drawn as an image | `no_glyphs` | same |
| math-font glyph without Unicode (ISOamsr `W` = ⩾, Math-Pi `<` = ≤, Symbol `•` = ∞) | wrong character in text | gate `uncertain_symbol_glyphs`, formula `uncertain_font_glyphs` |
| table grid merges columns (no vertical rules) | cells like `3 6`, header `1 2` | `alignment_uncertain`, gate `table_structure` |
| symbol split across a line break (`α` / `t,`) | token `αt` missing in that sentence | not flagged — known limitation |
| lower-case one-letter labels ("deviation h") | not recognised as a label | known limitation |
| scanned page | OCR text, draft | gate `ocr_pages`, `validation_status` |
| heading missed (unusual numbering, non-bold titles) | clause merged into previous section | gate `structure`, `inspect-document` |
| running header not repeated often enough (< 3 pages / < 30 %) | header text in chunks | `inspect-document`, gate `content_coverage` |

## 22. How to debug a bad PDF

```powershell
cd backend
# 1. what the extractor sees (nothing is written anywhere)
.venv\Scripts\python -m app.cli inspect-document --file "..\iso booklets\6.pdf" --page 25 --preview 300
# 2. only the equations and their status / LaTeX
.venv\Scripts\python -m app.cli inspect-document --file "..\iso booklets\6.pdf" --type formula --preview 0
# 3. the header shows layout statistics: repeated_furniture, inline_merges, formulas_needs_review,
#    tables_grid vs tables_lines, legends, figures, uncertain_symbol_glyphs
```
Then look at the rendered page (the document page preview in the UI, or
`GET /api/v1/documents/{id}/versions/{vid}/preview?page=n`). Typical causes: a heading number that
does not advance plausibly (§6), a running header that is not in the header band, an equation
number that is not in the right 40 % of the page, a Symbol font without mapping.

## 23. How to inspect generated chunks

```powershell
# stored chunks of the active version (read-only)
.venv\Scripts\python -m app.cli inspect-document --code "ISO 21771" --clause 4.3
# export (text previews truncated to 80 characters by default; --full only locally, copyrighted text)
.venv\Scripts\python -m app.cli inspect-document --code "ISO 21771" --export ..\data\exports\iso21771-chunks.jsonl
```
Columns: key, role, parent, content type, page range, tokens, heading path, equation/table/figure
numbers, units, formula status + LaTeX, review reasons. The API
`GET /api/v1/documents/{id}/versions/{vid}/chunks` returns the same metadata.

## 24. How to re-ingest one document

```powershell
# 1. dry run: extract + chunk in memory, all gates, old vs new counts; nothing is written
.venv\Scripts\python -m app.cli reingest-document --code "ISO 21771"
# 2. apply (refused if a blocking gate fails; refused if the version is VERIFIED unless --allow-revoke)
.venv\Scripts\python -m app.cli reingest-document --code "ISO 21771" --apply --by admin
.venv\Scripts\python -m app.cli reingest-document --code "ISO 21771" --apply --allow-revoke --by admin
# 3. review and approve again
.venv\Scripts\python -m app.cli corpus-status
.venv\Scripts\python -m app.cli corpus-approve --code "ISO 21771" --by admin --note "formulas 12-20 checked against pages 24-25"
```
`--apply` replaces pages and chunks inside a savepoint, re-runs every gate on what was written
(full-text index included) and **rolls back to the old chunks** if a blocking gate fails. Stored
chat citations (`message_sources.chunk_id`) are re-pointed to the new chunk holding the same excerpt
on the same page; their own version/page/excerpt provenance never changes. Every step is audited
(`ingestion.reingested`, `corpus.verification_revoked`).

## 25. How to validate a new document

1. Put the PDF in `iso booklets\` (the watcher registers it; or `python -m app.cli reindex`).
2. `corpus-status` → outcome and the gates that need attention.
3. `inspect-document --code "<code>"`: headings complete? equations with LaTeX? tables with
   headers? no running headers in chunks?
4. For every `needs_review` gate: open the listed pages and compare (formulas against the rendered
   page, uncertain glyphs, tables with `alignment_uncertain`).
5. Approve with a note saying what was checked. Only then is the document evidence for answers
   and calculations.
6. For a standard that matters, add its critical tokens/equations to `quality.EXPECTED` and a few
   questions to `app/evaluation/chunking_ground_truth.py`.

## 26. How to change chunking safely

1. Change code, then run the unit + integration tests:
   `backend\.venv\Scripts\python -m pytest tests/backend/test_engineering_chunking.py tests/backend/test_canonical_ingestion.py`
   (from the repository root), then the whole suite `-m "not live"` and the real-corpus tests `-m corpus`.
   The corpus tests look for the original long file names ("ISO 21771, 1, 2007…"); if the PDFs in
   `iso booklets` were renamed, point them at a folder with such names:
   `$env:DAYANERA_CORPUS_DIR = "C:\path\to\named-copies"`.
2. Run the benchmark: `.venv\Scripts\python -m app.evaluation.chunking_benchmark run` (old baseline
   worktree vs your tree, throwaway databases; ~3 min) and compare `docs/chunking/benchmark.md`,
   **especially the held-out rows**. A change that improves the tuning set but not the held-out set
   is probably over-fitted.
3. For size changes run `... chunking_benchmark sweep`.
4. Bump `CHUNKER_VERSION` (chunker) / `FORMULA_VERSION` (formula) / `LAYOUT_VERSION` (pdf.py) /
   `PIPELINE_VERSION` (quality.py) when output changes: fingerprints change and approvals must be
   renewed — that is intended.
5. Re-ingest with `reingest-document` (dry run first), review, approve.

## 27. Settings you should NOT change casually

| setting | where | why |
|---|---|---|
| `ChunkingConfig` values | `chunker.py` | measured (§19); `hard_max` must equal `quality.HARD_MAX_TOKENS` (a test checks it) |
| `tokens.COEF` | `tokens.py` | calibrated against Qwen; re-calibrate with `app.evaluation.token_calibration` |
| `EQ_WINDOW`, `HEADER_BAND`/`FOOTER_BAND`, `REPEAT_SHARE` | `iso_layout.py` | measured on the corpus (header/footer positions, tallest equations) |
| `plausible_next` | `iso_layout.py` | keeps figure keys and TOC lines out of the clause tree |
| formula safety checks | `formula.py` | they are what prevents invented formulas |
| `retrieval_min_score` (0.4), `retrieval_min_coverage` (0.5) | `.env` / `config.py` | relevance gate calibrated on real questions; lowering it makes the system answer from wrong passages |
| `retrieval_max_context_chars` (3000), `OLLAMA_NUM_CTX` (4096) | `.env` | prompt must fit the local model |
| the tsvector weights | migration 0004 | change only with a new migration + benchmark |
| `DAYANERA_CHUNKING_CONFIG` | environment | exists only for `chunking_benchmark sweep`; never set it for the running system |

---

### Rollout on an existing installation

```powershell
# stop the backend first (its worker would ingest with the old code), then back up
docker exec dayanera-postgres pg_dump -U dayanera -Fc dayanera > ..\data\backups\before-0004.dump
cd backend
.venv\Scripts\python -m app.cli migrate            # 0004_engineering_chunks (reversible: alembic downgrade 0003_self_maintenance)
.venv\Scripts\python -m app.cli reingest-document --code "ISO 21771"   # dry run per document, or:
.venv\Scripts\python -m app.cli reindex            # all documents (verified ones fall out of the verified corpus until re-approved)
.venv\Scripts\python -m app.cli corpus-status
```
Downgrade `0004` deletes parent rows and maps the new content types back to the 0002 set; re-run
`reindex` afterwards to return fully to the old chunking.
