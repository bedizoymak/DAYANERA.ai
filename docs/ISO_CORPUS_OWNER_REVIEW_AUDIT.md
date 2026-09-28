# DAYANERA.ai ISO Corpus Owner-Review Audit

**Audit date:** 2026-09-28  
**Scope:** 12 active engineering ISO document versions in the production owner-review workflow  
**Mode:** Read-only review; no approval, rejection, revocation, database update, or code change was performed.

## 1. Live runtime verification

| Check | Observed result | Assessment |
|---|---|---|
| Application | `https://dayanera.ai.lan/` loaded | PASS |
| Authenticated role | `Yönetici / owner_admin` | PASS |
| Database | Running; local Docker PostgreSQL at `127.0.0.1:54329` | PASS |
| Migration | `0004_engineering_chunks` | PASS |
| Ollama | Running; `qwen3:14b-q4_K_M` | PASS |
| Folder watcher | Running; 12 unchanged active ISO files on the latest scan | PASS |
| Corpus banner | `INDEXED_UNAPPROVED` / indexed, approval pending | PASS; expected safety state |
| Active corpus | 12 documents, 4,045 indexed chunks | PASS |
| Owner-approved versions | 0 | PASS; no approval state was changed |
| Evidence-eligible chunks | 0 | PASS; retrieval must remain blocked until approval |
| Review UI | Owner-only `ISO korpusu` tab visible; 12 versions listed | PASS |
| Status split | 2 `onay bekliyor`; 10 `inceleme gerekli` (`needs_review`) | PASS; matches the expected production state |

The system-status page also reported healthy local services, `draft_extraction: 181`, `unverified: 18`, and `verified_source: 500` page-state counts. The latter are extraction/source-page states, not owner approval; the authoritative owner-approved-version count remained zero.

## 2. Review method and evidence limits

I used the owner review queue, document archive, version detail panels, page selectors, rendered page previews, and the downloaded active PDFs reached through the archive UI. Representative pages were sampled for document starts, clause text, page transitions, tables, figures/captions, notes/footnotes, formulas, OCR pages, and flagged pages.

The SHA-256 shown by the UI was independently compared with the SHA-256 of each PDF downloaded through the UI. All 12 matched. The UI does not expose a separate `source_hash_match` field; the match result below is therefore the UI fingerprint versus the downloaded active-version bytes.

Every review-note field was blank before this audit. No review note was entered because entering one would be a persisted production mutation.

## 3. All active documents

`pymupdf` is the parser shown by the live UI for every active version. “OCR” below means OCR participation in the extracted corpus, not necessarily that the entire PDF was image-only.

| ISO / active filename | Version identifier | Status; pages / chunks | OCR | Source hash result | Quality-gate and spot-check summary | Classification |
|---|---|---|---|---|---|---|
| **ISO 1101:2017** — `GEOMET~1.PDF` | v1; document `43301480-6b38-4215-abe1-6af96af3df78`; version `2838ca4a-e04f-4284-aa93-71979923233b` | `inceleme gerekli`; 158 / 781 | Yes, 2 draft pages (156–157) | `84223efeb6b07a69a69e91b50052995df23d774059622210a3faa44b28a0fafe`; MATCH | Gates pass except `content_coverage: review` on pp. 5, 6, 7, 22, 24, 25, 30, 31, 40, 49, 65, 67. Tables pass; structure/chunk lineage pass. Samples: title/contents, pp. 22 and 24 figures and clauses, pp. 30–31 and 65–67 cross-page clauses, blank pp. 156–157. Figure captions were attached to the expected pages; symbol-heavy figures warrant owner visual confirmation. | **MANUAL_REVIEW_REQUIRED** |
| **ISO 1328-1:2013** — `CYLIND~3.PDF` | v1; document `671705a8-6774-4a2c-aec5-d3e06eb6f564`; version `ead2fd31-b9ec-481c-8432-da60ac70dec4` | `inceleme gerekli`; 62 / 269 | Yes, 3 draft pages | `af52cc43ee828b6b15c6f35e993b07f777db2859ebd886a2db529402cb62f5ba`; MATCH | `pdf_valid: review` (repaired/EOF warning); `content_coverage: review` on pp. 5, 14, 17, 18, 21, 22, 25, 31, 33–36; formula gate review for D.2, E.1, F.4, F.6 on pp. 47, 49, 52, 53. Table 2, definitions, Fig. 15, and formula pages were sampled. On p. 34 the rendered square-root/exponent formulas are materially less unambiguous in extracted text; p. 35 has similar operator/symbol reconstruction risk. | **REPROCESS_REQUIRED** |
| **ISO 14104:2017** — `Gears_ Surface temper etch inspection after grinding, -- Confirmed, 2017 -- BSI British Standards Institution -- isbn13 9780580928536 -- 582ccca6bd272159b62324186c00f111 -- Anna’s Archive.pdf` | v2; document `76c0eb0d-08bf-4daa-b697-931fbf87a8ab`; version `240c51d0-37cf-463c-bdfb-d00fd81debd1` | `onay bekliyor`; 26 / 150 | No | `cc1f804ec046532d2b93d269048748776554751b6d67050db4c2f9ceadc2e19a`; MATCH | All displayed gates pass, including critical content, structure, chunk lineage, traceability, coverage, and page quality. Samples: title pp. 1–3, procedure/table page 11, Tables 2–3 on p. 13, blank p. 25, publisher page 26. Rendered tables preserved headers, rows, units, and continuation within the sampled pages. No formula gate or material OCR/math issue observed. | **APPROVAL_CANDIDATE** |
| **ISO 21771:2007** — `Gears -- Cylindrical involute gears and gear pairs -- -- ISO_TC 60_SC 1 Nomenclature and wormgearing -- ISO 21771, 1, 2007 -- ISO -- 929effa0984e353cd9cbd222a012e3b1 -- Anna’s Archive.pdf` | v2; document `7511eb9c-66fe-491d-8fde-6cc580dbe886`; version `d1e736ca-3fb5-483f-bc19-4c83fc9b8027` | `inceleme gerekli`; 88 / 514 | No | `14656d6cef48862511b09e9d4de1ed9451e126062b2d4fd30075c2c1bc0b7b5b`; MATCH | `uncertain_symbol_glyphs: review` on pp. 32, 37, 65, 67–70. Formula extraction review covers many equations on pp. 22, 32–33, 37–38, 41, 46–47, 61–62, 67–68. Samples include lead formula p. 22, gear-ratio clause p. 32, active diameters pp. 37–38, sliding-speed formula p. 46, root-diameter formulas pp. 61–62, span-measurement formulas pp. 67–68. Rendered equations are legible, but the extracted text reorders fractions, subscripts, and operators and the Symbol font has no Unicode mapping. | **REPROCESS_REQUIRED** |
| **ISO 2490:2007** — `Solid (monobloc) gear hobs with tenon drive or axial keyway, -- ISO_TC 60_SC 1 Nomenclature and wormgearing -- ISO 2490, 3, 2007 -- ISO -- 1c93af78a020fa7e1afa5c309dd84d73 -- Anna’s Archive.pdf` | v2; document `adbf84b6-6b5b-43ad-a9a3-5c31e538d86e`; version `2869b595-8c9d-439f-9ded-ea25c4b16243` | `onay bekliyor`; 16 / 83 | Yes, 1 draft page | `29c2ddbcef4ea20c9f3cbe926fbfb912a638d74fc402e19e6e3d96dc49332182`; MATCH | All displayed gates pass. Samples: title/scope p. 1, contents p. 3, nominal-dimensions Table 2 p. 8, Annex A p. 9, final/copyright pages 15–16. The rendered Table 2 has stable headers and rows; the OCR-involved page is a copyright/end page rather than an engineering table. No formula extraction warning. | **APPROVAL_CANDIDATE** |
| **ISO 286-1:2010** — `GEOMET~3.PDF` | v1; document `27b3c4e8-4104-4671-aa39-ed0dd02a8b57`; version `9ac3c1e1-1b9d-412b-9828-085c86bdc6fa` | `inceleme gerekli`; 46 / 366 | Yes, 1 draft page | `6ec05a0e6c9bae7fd9621b9800c96ac3af165744f8e80ff29c42aeb317634514`; MATCH | `uncertain_symbol_glyphs: review` on pp. 27, 30, 40. Other structure, table, coverage, and lineage gates pass; one OCR page is p. 45. Samples: Table 2 p. 27, Table 4 p. 30, tolerance-class example p. 40. The tables are broadly readable, but the flagged Symbol-font pages contain engineering signs/deviations that require visual owner confirmation. | **MANUAL_REVIEW_REQUIRED** |
| **ISO 286-2:2010** — `Geometrical product specifications (GPS) -- ISO code system -- ISO_TC 213 Dimensional and geometrical product -- ISO 286, 2, 2010 -- ISO -- eb27628c85c13265f902c6aa3a69df93 -- Anna’s Archive.pdf` | v2; document `167824b4-7f5a-4c09-aef2-19c2acbce2d0`; version `d24c4ccf-8db0-41dc-8a42-0963a989cc80` | `inceleme gerekli`; review card reports 60 source pages; detail extraction reports 58 / 981 | Yes, 2 draft pages | `f8e09fa36bb22a5807278f0a5ab794b6c9ecfb7ae1f767582630887496009b61`; MATCH | `uncertain_symbol_glyphs: review` on pp. 8–9; `chunk_structure: review` on pp. 12–13, 15. Table structure and coverage pass. The detail page exposes extracted page labels 1–5, 7–58, 60, so source pages 6 and 59 require explicit provenance confirmation even though the source-page count in the review card is 60. Samples: figures pp. 8–9, class maps pp. 12–13, Table 3 p. 15, Annex A p. 51, GPS matrix p. 56. | **MANUAL_REVIEW_REQUIRED** |
| **ISO 4468:2020** — `Gear hobs - Accuracy requirements -- ISO_TC 60_SC 1 Nomenclature and wormgearing -- ISO 4468, 3, 2020 -- ISO -- ab635bfaae822175d68a0e713aecce07 -- Anna’s Archive.pdf` | v2; document `d4c9c641-abf5-4722-8601-8eebbaed7757`; version `c22259e5-87c3-496a-a188-45f686fcaac1` | `inceleme gerekli`; 44 / 245 | No | `a040f23ea233f12d6f94e316e0c7cf4f60ac9171bb7d7118307f21bff8a3bf6d`; MATCH | `pdf_valid: review` (PDF repaired or EOF marker missing). All other displayed gates pass, including formula and table extraction. Samples: contents p. 3, continued accuracy table pp. 31–32, Table A.2 p. 37, multi-thread table/formula p. 42, end matter p. 44. Continued-table headings were retained in the extracted text; formulas are present, but the repaired-PDF condition needs owner confirmation before approval. | **MANUAL_REVIEW_REQUIRED** |
| **ISO 53:1998** — `Cylindrical gears for general and heavy engineering — -- ISO_TC 60 Gears -- ISO 53, 2, 1998 -- ISO -- afd908ee77591aaf9c0441a5fd915414 -- Anna’s Archive.pdf` | v2; document `065a790f-774b-4ddc-9419-2a06c326002c`; version `c3269da1-4ec1-4e8d-8e3d-9649b75d152d` | `inceleme gerekli`; 10 / 33 | Yes, 1 draft page | `39273ac106560f88adfd886d5d7acd303d6367c60c1e430ac4baf448ec4702a9`; MATCH | `uncertain_symbol_glyphs: review` on pp. 3 and 6; formula extraction review for Formula (3) on p. 6. Samples: scope/definitions p. 3, symbols/table p. 4, Formulae (2)–(3) and root-filleting note p. 6, Table A.1 p. 7, final page p. 10. Rendered Formula (3) is visually legible and the table is usable, but the extracted formula requires manual source comparison before approval. | **MANUAL_REVIEW_REQUIRED** |
| **ISO 54:1996** — `Cylindrical gears for general engineering and for heavy -- ISO_TC 60 Gears -- ISO 54, 2, 1996 -- ISO -- f9d37d4c67cf6c44ffbbf5422e34705e -- Anna’s Archive.pdf` | v2; document `798f4523-00c1-47c0-8995-5ac408845cb3`; version `e16848d4-40a6-4f02-bfb0-2247b7d93762` | `inceleme gerekli`; 5 / 6 | Yes, all 5 pages draft OCR | `0859a3b254e2640042dc3281dd122bf872aa969675577c54e7205857da0847e2`; MATCH | `ocr_pages: review`; average OCR confidence 0.973 with short/empty OCR pages 4–5 and all pages marked draft. Page samples show merged words, missing spaces, and OCR artifacts such as `ISO54`, `InternationalStandard`, and compressed clause text. Table 1 p. 4 is extractable but not reliable enough for engineering use without reprocessing. | **REPROCESS_REQUIRED** |
| **ISO/TR 10064-1:1992** — `Code of inspection practice - Part 1_ Inspection of -- ISO_TC 60 Gears -- ISO_TR 10064, 1, 1991 -- ISO -- dd832a64c1bdc1d6657d54540f456f77 -- Anna’s Archive.pdf` | v2; document `de40fe0b-3556-480e-90ae-978ecc774272`; version `321ad935-2a5a-475f-b260-72d88b3bfa7b` | `inceleme gerekli`; 64 / 136 | Yes, all 64 pages draft OCR | `7470aaa0737c6139b82ec63dab558a5d34a5b43303e61b66b4f177feddb0f19d`; MATCH | `standard_code: review`: UI text says 1992 while filename says 1991. `structure: review`: ISO clause structure was not extracted; page-window chunks used. OCR review covers pp. 1–12, with all pages draft. Page 1 has severe character/spacing corruption; p. 14 sample table/figure text is noisy; p. 39 has OCR substitutions in technical symbols. This is a source/version identity problem as well as an extraction problem. | **BLOCKED** |
| **ISO/TR 10828:2015** — `Worm gears -- Worm profiles and gear mesh geometry -- ISO_TC 60_SC 1 Nomenclature and wormgearing -- ISO_TR 10828, 2, 2015 -- ISO -- a59208c8c0f52a99c6b8eab502bd63c9 -- Anna’s Archive.pdf` | v2; document `82e71651-2c35-4f84-99de-0358348bb581`; version `0b1e78b2-d24c-4156-81bd-6ec5f28a33bd` | `inceleme gerekli`; 102 / 481 | Yes, all 102 pages draft OCR | `c4215e3fd2360e512d7ae182dff3d3173521d66634aaf6acbd64853f8dc65a9e`; MATCH | `ocr_pages: review` average 0.963; short/empty OCR pages 90 and 102. `structure: review` uses page-window chunks. `content_coverage: review` on pp. 3, 4, 18, 20, 22, 24, 26, 28, 30, 32, 34, 35. Formula extraction review covers roughly 30 equations across pp. 11–18, 27–28, 34–35. Samples show merged words, missing spaces, OCR substitutions in Greek/special symbols, and formulas whose extracted linear order is unsafe even when the rendered page is legible. | **REPROCESS_REQUIRED** |

## 4. Explanation of every `needs_review` version

- **ISO 1101:** the state is driven by content-coverage review on symbol/figure-heavy pages, not by a failed hash or parser. The sampled clause text is coherent, but the owner should visually confirm the flagged pages before approval.
- **ISO 1328-1:** the PDF repair warning, OCR pages, coverage review, and formula warnings overlap on high-value tolerance equations. The rendered page is readable, but the extracted square-root/exponent/operator structure is not safe to trust without reprocessing or a complete formula-by-formula comparison.
- **ISO 21771:** the Symbol font has no Unicode mapping on seven pages and many gear-geometry equations are flagged. This is material because the document’s engineering value is formula-heavy.
- **ISO 286-1:** the main tables sampled well, but Symbol-font mapping warnings occur in engineering deviation tables and a worked example. A visual owner check is required.
- **ISO 286-2:** the source hash is valid and tables are present, but Symbol-font and chunk-structure warnings remain. The review card reports 60 source pages while the detail extraction reports 58 extracted pages and omits labels 6 and 59; that page coverage must be reconciled.
- **ISO 4468:** formulas and tables pass, but the source PDF required repair or lacks an EOF marker. The table continuation sample is coherent; the PDF condition remains unexplained for approval purposes.
- **ISO 53:** only one formula is flagged, and the rendered formula is visually legible. It is a reasonable manual-review candidate, but the formula should be transcribed/checked against the page before approval.
- **ISO 54:** all content is OCR-derived and the extracted text has merged words and spacing loss. Hash integrity does not cure semantic OCR risk; reprocessing is required.
- **ISO/TR 10064-1:** the standard year in the text and filename disagree, the entire document is OCR draft, and clause structure was not extracted. This is blocked until the exact source/version identity and extraction are resolved.
- **ISO/TR 10828:** the full document is OCR draft, structure is page-windowed, and many formulas are flagged. The sampled rendered pages are visually readable, but the extracted formula stream is not engineering-safe.

## 5. Defects and risk findings

### Parser/OCR

- `pymupdf` is consistently reported, but OCR participation varies by page.
- ISO 54, ISO/TR 10064-1, and ISO/TR 10828 have broad OCR involvement and visible merged-word/spacing corruption. ISO/TR 10064-1 is the most severe because the OCR affects the whole document and technical symbols.
- Blank/end-matter OCR pages are generally harmless when they are publisher/copyright pages: ISO 1101 pp. 156–157, ISO 14104 p. 25, ISO 2490’s single draft page, ISO 286-1 p. 45, and ISO 286-2’s two OCR pages. They still remain draft and should not be silently treated as verified evidence.

### Formulas and mathematical reconstruction

- ISO 21771 and ISO/TR 10828 have the highest formula risk. Rendered formulas are readable, but extracted text reorders fractions, subscripts, Greek symbols, and operators.
- ISO 1328-1 formula pages show a concrete loss of visual grouping in extracted text: square-root and exponent structure is not reliably represented as linear text.
- ISO 53 Formula (3) is visually readable but remains flagged and needs source-page confirmation.
- ISO 4468’s formula gate passes, but the PDF repair warning means the owner should still verify a representative formula page.

### Tables and continued tables

- No material column/row shift was observed in the rendered samples for ISO 14104 Table 2/3 p. 13 or ISO 2490 Table 2 p. 8.
- ISO 4468’s Table 6 continuation across pp. 31–32 retained the continuation label and usable rows in extracted text; its Table A.2 sample on p. 37 was also structurally readable.
- ISO 53 Table A.1 p. 7 and ISO 54 Table 1 p. 4 were visible in the page extraction; ISO 54’s OCR spacing makes it unsafe without reprocessing.
- ISO/TR 10064-1’s sampled figure/table page p. 14 is not reliable enough to approve because OCR has damaged spacing and symbols.

### Page boundaries, provenance, figures, and notes

- Cross-page clause samples for ISO 1101, ISO 1328-1, and ISO 21771 retained adjacent clause context; formula pages remain the limiting risk.
- Figure/caption samples on ISO 1101 p. 22, ISO 1328-1 pp. 31 and 53, ISO 21771 p. 22, and ISO/TR 10828 p. 32 were attached to the expected page in the UI.
- Notes/footnotes sampled in ISO 53 p. 6, ISO 21771 p. 13, ISO 2490 p. 8, and ISO 4468 p. 37 remained present in extracted text.
- ISO 286-2 has a material page-count/page-label discrepancy: the review card reports 60 source pages, while the detail extraction reports 58 pages and labels omit 6 and 59. This requires owner reconciliation.
- ISO/TR 10064-1 has a material standard-code discrepancy: UI title/text says 1992 while the active filename says 1991. Hash match confirms the bytes, not the correct standard identity.

## 6. Classification summary

### APPROVAL_CANDIDATE

1. **ISO 14104:2017 v2** — all displayed gates pass; no unresolved formula/OCR/math issue; tables and procedures sampled cleanly.
2. **ISO 2490:2007 v2** — all displayed gates pass; small 16-page document; clean nominal-dimension table; one OCR page is end matter; no formula gate.

### MANUAL_REVIEW_REQUIRED

1. ISO 1101:2017 v1 — content-coverage review on symbol/figure pages.
2. ISO 286-1:2010 v1 — Symbol-font warnings on engineering tables/example pages.
3. ISO 286-2:2010 v2 — Symbol/chunk warnings plus page-count/page-label reconciliation.
4. ISO 4468:2020 v2 — repaired/EOF-warning PDF despite otherwise passing gates.
5. ISO 53:1998 v2 — isolated Formula (3)/Symbol-font review.

### REPROCESS_REQUIRED

1. ISO 1328-1:2013 v1 — formula reconstruction and PDF/OCR/coverage warnings.
2. ISO 21771:2007 v2 — extensive formula and Symbol-font warnings.
3. ISO 54:1996 v2 — all-page OCR draft with merged-word corruption.
4. ISO/TR 10828:2015 v2 — all-page OCR draft, windowed structure, and extensive formula warnings.

### BLOCKED

1. **ISO/TR 10064-1:1992 v2** — standard year/source identity conflict plus all-page OCR and missing clause structure.

## 7. Recommended first controlled approval candidate

**ISO 2490:2007, active v2** (`version_id: 2869b595-8c9d-439f-9ded-ea25c4b16243`) is the best first controlled approval candidate.

Reasons:

- digitally generated PDF with the active UI fingerprint matching the downloaded bytes;
- 16 pages and 83 chunks, materially smaller and easier to inspect than the other candidates;
- all displayed quality gates pass;
- clear clause hierarchy and a compact, manually checkable nominal-dimensions table;
- no unresolved formula-extraction gate;
- representative rendered pages show stable table headers, rows, units, and footnotes;
- useful retrieval content for a controlled table lookup and citation test.

ISO 14104:2017 v2 is also a candidate, but ISO 2490 is the simpler first test because it is smaller and has a single compact engineering table with no formula risk.

## 8. Acceptance tests to run only after owner approval

Expected answers below are grounded in ISO 2490:2007 v2 and should be checked against the cited page after approval.

| # | Acceptance question | Expected evidence / answer |
|---:|---|---|
| 1 | What does ISO 2490:2007 cover? | Solid (monobloc) gear hobs with tenon drive or axial keyway, nominal dimensions; title page, p. 1. |
| 2 | What module range is stated in the title? | 0.5 to 40 module; p. 1. |
| 3 | For module `m = 2`, what values are listed in Table 2? | The row gives the nominal table values including 65 mm outside diameter, 27 mm bore, 60/75 mm overall lengths, 4 mm minimum hub length, and 14 typical gashes; Table 2, p. 8. The owner should verify the exact column mapping in the rendered table. |
| 4 | For module `m = 5.5`, what are the listed outside diameter, bore, overall lengths, minimum hub length, and typical gashes? | 100 mm, 32 mm, 95/110 mm, 5 mm, and 12 respectively; Table 2, p. 8. |
| 5 | What tolerance class applies to dimensions D, L, and L0? | The “coarse” class of ISO 2768-1; Table 2 footnote a, p. 8. |
| 6 | What does Table 2 footnote b say about ISO 2780? | ISO 2780 gives tenonway dimensions only for bores up to 50 mm; p. 8. |
| 7 | What is Annex A about? | Multiple-thread hobs; Annex A, p. 9 in the UI/source-page mapping. |
| 8 | Which page contains the nominal-dimensions table used for the m = 2 lookup? | Page 8; citation/page verification test. |

No formula test is proposed for ISO 2490 because the candidate does not expose a formula gate in the reviewed evidence. Formula acceptance tests should be added only after a formula-bearing document is separately approved.

## 9. Exact manual owner action required next

1. Do not approve the corpus in bulk; the UI correctly exposes individual-version actions only.
2. Open **İnceleme kuyruğu → ISO korpusu** and manually re-check **ISO 2490:2007 v2** pages 1, 3, 8, 9, 15, and 16, with special attention to Table 2 column labels and the one draft OCR end-matter page.
3. If satisfied, enter an evidence-based review note naming the checked gates/pages and then click **Onayla for ISO 2490:2007 v2 only**. That is the first controlled production mutation; it was intentionally not performed in this audit.
4. Before approving any other version, reprocess the four reprocess-required documents and resolve the ISO/TR 10064-1 year/source conflict. Reconcile ISO 286-2’s 60-versus-58 page presentation before approval.

## 10. Final safety state

At audit completion, the live system still reported **0 owner-approved versions**, **0 evidence-eligible chunks**, and the corpus banner remained **INDEXED_UNAPPROVED**. No approval-state change was made.


