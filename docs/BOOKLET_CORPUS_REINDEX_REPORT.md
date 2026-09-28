# DAYANERA booklet corpus re-index report

Execution: 2026-09-28 07:07 Europe/Istanbul
Code: `7ccaeb3` (`claude/engineering-chunking`, engineering chunker commits included)
Database migration: `0004_engineering_chunks`
Database: configured local PostgreSQL database from `.env` (`127.0.0.1:54329/dayanera`; no credentials recorded here)

## Result

All 12 PDFs currently present in `iso booklets` were discovered and re-indexed through the engineering pipeline. Every document completed with `ingestion_status = indexed`; no document failed or was silently skipped.

The re-index is intentionally not owner-approved yet. The active versions are `extracted` or `needs_review`, so the production retrieval layer correctly excludes them until an owner administrator reviews and approves them. The verified-corpus warning therefore remains visible; it was not suppressed.

This is the safe outcome for uncertain OCR, math-font glyphs, and wide-table extraction. Existing document records, historical versions, source hashes, pages, chunks, and message-source rows were retained.

## Before and after snapshot

The first column is the snapshot captured before re-indexing. “Active watched” means the 12 PDFs in `iso booklets`; “all stored” includes retained historical versions and logically deleted documents.

| metric | before | after |
|---|---:|---:|
| booklet PDFs on disk | 12 | 12 |
| active watched document records | 12 | 12 |
| active document records including attachments | 14 | 14 |
| all document records | 33 | 33 |
| active watched current chunks | 1,683 | 4,045 |
| active current chunks including attachments | 1,741 | 4,103 |
| all stored chunks, including retained history | 5,646 | 8,008 |
| active verified chunks | 0 | 0 |
| active needs-review chunks | 629 | 3,870 |
| active extracted chunks | 1,112 | 233 |
| active failed chunks | 0 | 0 |
| active watched formula-related chunks | 116 legacy formula chunks | 733 engineering formula-related chunks |
| active watched table-related chunks | 203 legacy table chunks | 1,230 engineering table/table-row chunks |
| active watched figure chunks | 0 | 533 |
| active watched formulas with LaTeX | not available in legacy chunks | 194 |
| active indexed versions | 14 | 14 |
| active versions with failed ingestion | 0 | 0 |
| active chunks missing generated `tsv` | 0 | 0 |
| embedding/vector tables | 0 | 0 |

DAYANERA currently uses PostgreSQL full-text search (`tsvector` + GIN), not dense embeddings. The generated `tsv` index is populated for every active chunk. No embedding service or vector index was silently invented during this operation.

## Discovered and processed manifest

All hashes below were recomputed from the PDFs on disk and matched the active watched document version.

| standard | filename | document ID | pages | active chunks | formulas | tables | figures | status | SHA-256 |
|---|---|---|---:|---:|---:|---:|---:|---|---|
| ISO 1101:2017 | `GEOMET~1.PDF` | `43301480-6b38-4215-abe1-6af96af3df78` | 158 | 781 | 3 | 41 | 293 | needs_review | `84223efeb6b07a69a69e91b50052995df23d774059622210a3faa44b28a0fafe` |
| ISO 1328-1:2013 | `CYLIND~3.PDF` | `671705a8-6774-4a2c-aec5-d3e06eb6f564` | 62 | 269 | 40 | 23 | 50 | needs_review | `af52cc43ee828b6b15c6f35e993b07f777db2859ebd886a2db529402cb62f5ba` |
| ISO 14104:2017 | `Gears_ Surface temper etch inspection after grinding, -- Confirmed, 2017 -- BSI British Standards Institution -- isbn13 9780580928536 -- 582ccca6bd272159b62324186c00f111 -- Anna’s Archive.pdf` | `76c0eb0d-08bf-4daa-b697-931fbf87a8ab` | 26 | 150 | 0 | 0 | 18 | extracted | `cc1f804ec046532d2b93d269048748776554751b6d67050db4c2f9ceadc2e19a` |
| ISO 21771:2007 | `Gears -- Cylindrical involute gears and gear pairs -- -- ISO_TC 60_SC 1 Nomenclature and wormgearing -- ISO 21771, 1, 2007 -- ISO -- 929effa0984e353cd9cbd222a012e3b1 -- Anna’s Archive.pdf` | `7511eb9c-66fe-491d-8fde-6cc580dbe886` | 88 | 514 | 302 | 27 | 99 | needs_review | `14656d6cef48862511b09e9d4de1ed9451e126062b2d4fd30075c2c1bc0b7b5b` |
| ISO 2490:2007 | `Solid (monobloc) gear hobs with tenon drive or axial keyway, -- ISO_TC 60_SC 1 Nomenclature and wormgearing -- ISO 2490, 3, 2007 -- ISO -- 1c93af78a020fa7e1afa5c309dd84d73 -- Anna’s Archive.pdf` | `adbf84b6-6b5b-43ad-a9a3-5c31e538d86e` | 16 | 83 | 0 | 29 | 2 | extracted | `29c2ddbcef4ea20c9f3cbe926fbfb912a638d74fc402e19e6e3d96dc49332182` |
| ISO 286-1:2010 | `GEOMET~3.PDF` | `27b3c4e8-4104-4671-aa39-ed0dd02a8b57` | 46 | 366 | 1 | 162 | 25 | needs_review | `6ec05a0e6c9bae7fd9621b9800c96ac3af165744f8e80ff29c42aeb317634514` |
| ISO 286-2:2010 | `Geometrical product specifications (GPS) -- ISO code system -- ISO_TC 213 Dimensional and geometrical product -- ISO 286, 2, 2010 -- ISO -- eb27628c85c13265f902c6aa3a69df93 -- Anna’s Archive.pdf` | `167824b4-7f5a-4c09-aef2-19c2acbce2d0` | 60 | 981 | 0 | 890 | 18 | needs_review | `f8e09fa36bb22a5807278f0a5ab794b6c9ecfb7ae1f767582630887496009b61` |
| ISO 4468:2020 | `Gear hobs - Accuracy requirements -- ISO_TC 60_SC 1 Nomenclature and wormgearing -- ISO 4468, 3, 2020 -- ISO -- ab635bfaae822175d68a0e713aecce07 -- Anna’s Archive.pdf` | `d4c9c641-abf5-4722-8601-8eebbaed7757` | 44 | 245 | 6 | 46 | 2 | needs_review | `a040f23ea233f12d6f94e316e0c7cf4f60ac9171bb7d7118307f21bff8a3bf6d` |
| ISO 53:1998 | `Cylindrical gears for general and heavy engineering — -- ISO_TC 60 Gears -- ISO 53, 2, 1998 -- ISO -- afd908ee77591aaf9c0441a5fd915414 -- Anna’s Archive.pdf` | `065a790f-774b-4ddc-9419-2a06c326002c` | 10 | 33 | 2 | 6 | 4 | needs_review | `39273ac106560f88adfd886d5d7acd303d6367c60c1e430ac4baf448ec4702a9` |
| ISO 54:1996 | `Cylindrical gears for general engineering and for heavy -- ISO_TC 60 Gears -- ISO 54, 2, 1996 -- ISO -- f9d37d4c67cf6c44ffbbf5422e34705e -- Anna’s Archive.pdf` | `798f4523-00c1-47c0-8995-5ac408845cb3` | 5 | 6 | 0 | 0 | 0 | needs_review | `0859a3b254e2640042dc3281dd122bf872aa969675577c54e7205857da0847e2` |
| ISO/TR 10064-1:1992 | `Code of inspection practice - Part 1_ Inspection of -- ISO_TC 60 Gears -- ISO_TR 10064, 1, 1991 -- ISO -- dd832a64c1bdc1d6657d54540f456f77 -- Anna’s Archive.pdf` | `de40fe0b-3556-480e-90ae-978ecc774272` | 64 | 136 | 0 | 0 | 0 | needs_review | `7470aaa0737c6139b82ec63dab558a5d34a5b43303e61b66b4f177feddb0f19d` |
| ISO/TR 10828:2015 | `Worm gears -- Worm profiles and gear mesh geometry -- ISO_TC 60_SC 1 Nomenclature and wormgearing -- ISO_TR 10828, 2, 2015 -- ISO -- a59208c8c0f52a99c6b8eab502bd63c9 -- Anna’s Archive.pdf` | `82e71651-2c35-4f84-99de-0358348bb581` | 102 | 481 | 379 | 0 | 22 | needs_review | `c4215e3fd2360e512d7ae182dff3d3173521d66634aaf6acbd64853f8dc65a9e` |

The two active `attachment` records for ISO 53 have the same hash as the watched ISO 53 PDF but are outside the verified ISO area. They were not re-indexed as part of the 12-file watched corpus. The verified-area duplicate-hash check is zero. They should be deduplicated or retained as archive attachments only after an operator decides which source identity is canonical.

## Pre-flight pilots

Safe dry-runs were run before writes for ISO 21771 and ISO 53. Both had no blocking gate failures.

| pilot | candidate chunks | formulas | LaTeX formulas | tables/row groups | cross-page merges | review state |
|---|---:|---:|---:|---:|---:|---|
| ISO 21771:2007 | 514 | 207 | 156 | 27 | 1 | uncertain glyphs and 34 formula extractions need review |
| ISO 53:1998 | 33 | 9 | 2 | 6 | 0 | one ambiguous degree glyph and one formula need review |

The stored ISO 21771 candidate contains equation (19)/(20) in clause 4.3.10, page 25, as a formula chunk with LaTeX and its equation numbers. The ISO 53 candidate contains Table 2 in clause 5.3, page 5, with the header and the `h_fP = 1,25 m` row preserved.

## Integrity checks after re-index

All checks below were run against the active current versions.

| check | result |
|---|---|
| PDF count equals watched active document count | 12 = 12 |
| source hashes match files on disk | 12/12 |
| active versions indexed | 14/14 |
| failed active versions | 0 |
| orphan parent references | 0 |
| cross-version parent references | 0 |
| invalid page ranges | 0 |
| missing generated full-text vectors | 0 |
| orphan message-source chunk links | 0 |
| message-source/version mismatches | 0 |
| verified-area duplicate document hashes | 0 |
| stale retired chunks returned by production retrieval | 0; retrieval is correctly empty until approval |

The database still contains 222 historical `message_sources`; no orphaned or mismatched citation links were found. Re-ingestion preserved document IDs and source hashes for the 12 watched records. Historical versions and their old chunks were not deleted.

## Retrieval and calculation validation

The real production retrieval function was run for:

- “What is the base diameter formula?”
- “What is transverse base pitch?”
- “ISO 53 basic rack dedendum coefficient”
- “How are normal and transverse module related for a helical gear?”
- “ISO 21771 equation 19 base diameter”
- “ISO 53 Table 2 standard basic rack proportions”

Each returned zero eligible passages. This is expected and correct: the retrieval contract requires an active, indexed, owner-approved `corpus_status = verified` version. The candidate chunks exist and were inspected, but exposing them as verified evidence would violate the validation lifecycle.

The live calculation resolver likewise returned the intended refusal, `Bu kaynak setinde doğrulayamadım`, because the required verified source passages are not active. With a controlled evidence stub, the deterministic engine produced the required numerical result:

`m_t=2`, `alpha_t=20`, `d=60`, `d_b=56.3816`, `p_n=6.2832`, `p_t=6.2832`, `p_bt=5.9043`, `h_a=2`, `h_f=2.5`, `h=4.5`, `d_a=64`, `d_f=55`.

This confirms the calculation engine; it is not a claim that the production evidence gate is currently open.

## Warning and activation status

The backend readiness API reports:

- database ready: yes;
- migration: `0004_engineering_chunks`;
- Ollama/model: reachable and available;
- `empty_verified_corpus`: true;
- active verified corpus codes: none;
- pending corpus codes: all 12 watched standards.

The UI warning about an empty verified ISO corpus therefore remains for the correct underlying reason. It must disappear only after owner-admin review and approval, not by changing the warning condition.

## Manual review required

The following remain explicitly reviewable rather than silently promoted:

- ISO 21771: uncertain math-font glyphs and 34 formula extractions; equation (19)/(20) itself is present with LaTeX, but the document has other uncertain formulas.
- ISO 53: ambiguous degree glyph in equation (3) and one formula review item.
- ISO 1328-1, ISO 286-1, ISO 286-2, and ISO 1101: math-font/table/content-coverage review items; ISO 286-2 has wide table groups flagged by `chunk_structure`.
- ISO 54, ISO/TR 10064-1, and ISO/TR 10828: OCR/structure review items; OCR formulas and tables are not promoted to confident LaTeX.
- ISO 4468: PDF validation review remains, although formula extraction passed.
- The two duplicate ISO 53 attachment records need an operator decision about archival/canonical identity.

Approval commands, after a human has reviewed the gates, are documented in [the maintainer guide](ENGINEERING_DOCUMENT_CHUNKING_GUIDE.md). A `needs_review` version requires an owner-admin note; no approval was fabricated during this run.

## Tests

- Focused ingestion/chunking/calculation/API tests: **91 passed**.
- Real-corpus suite against the available correctly named ISO copies: **32 passed**.
- Backend readiness API: **ready**.
- PostgreSQL migration and full-text index integrity: **pass**.

The frontend was not changed by re-indexing; the previously validated frontend test/build results remain documented in the implementation report.

## Recovery

Each document was processed through `reingest_version(..., dry_run=False)`. The implementation uses a savepoint and post-write quality gates per document. A blocking failure rolls back that document and keeps its previous chunks. No rollback was required. Historical versions remain available for operator recovery and citation provenance.
