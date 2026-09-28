# DAYANERA.ai production corpus approval and user-level QA

**Date:** 2026-09-28  
**Environment:** production-like local LAN (`https://dayanera.ai.lan/`)  
**Actor:** `owner_admin`  
**Scope:** all 12 active ISO engineering document versions, approval audit, live status, and normal end-user chat

## Executive result

All 12 active ISO versions were approved individually through **İnceleme kuyruğu → ISO korpusu**. No bulk action, SQL mutation, source replacement, or fingerprint bypass was used. The exact owner note entered on every version was:

> Owner-authorized corpus approval for production user-level QA. Remaining extraction/retrieval defects will be evaluated through controlled QA and corrected individually.

The post-approval system status was:

| Check | Result |
|---|---:|
| Corpus state | `hazır` |
| Active indexed chunks | 4,045 |
| Owner-approved active versions | 12 |
| Evidence-eligible chunks | 3,411 |
| Database / Ollama / watcher | healthy / running |
| Migration | `0004_engineering_chunks` |
| Local model | `qwen3:14b-q4_K_M` |

The source fingerprint audit from the preceding owner-review report remains applicable: all 12 active PDF fingerprints matched the bytes downloaded through the UI. Approval changed review state and audit metadata only; source bytes were not changed.

## Approval audit trail

The UI audit log recorded one successful `corpus.verified` event per active document:

| Audit event | Time | Document |
|---:|---|---|
| 881 | 11:29:26 | ISO 1101:2017 |
| 882 | 11:29:38 | ISO 1328-1:2013 |
| 883 | 11:29:38 | ISO 14104:2017 |
| 884 | 11:29:39 | ISO 21771:2007 |
| 885 | 11:29:46 | ISO 2490:2007 |
| 886 | 11:29:47 | ISO 286-1:2010 |
| 887 | 11:29:48 | ISO 286-2:2010 |
| 888 | 11:29:57 | ISO 4468:2020 |
| 889 | 11:29:57 | ISO 53:1998 |
| 890 | 11:29:58 | ISO 54:1996 |
| 891 | 11:30:06 | ISO/TR 10064-1:1992 |
| 892 | 11:30:06 | ISO/TR 10828:2015 |

The version identifiers and full fingerprint evidence are in [ISO_CORPUS_OWNER_REVIEW_AUDIT.md](ISO_CORPUS_OWNER_REVIEW_AUDIT.md).

## QA method

I used the normal browser chat flow after approval. Each question was typed into the user `Mesaj` field, sent with `Gönder`, and—where an answer was returned—expanded with `Kaynak ver`. No API, database, or internal retrieval shortcut was used for the user-level matrix.

- 45 unique corpus questions across all 12 approved standards.
- 1 corpus-outside safety question (`ISO 9001:2015`).
- 21/45 corpus questions produced a directly source-backed answer in the first matrix.
- 24/45 corpus questions were retained as `REVIEW` because they were refused, only partially answered, or cited a different standard than the question named.
- The corpus-outside question correctly returned the unverified/general-chat path without a fabricated ISO citation.

The `REVIEW` count is not treated as permission to weaken the evidence gate. Several cases are expected safe refusal for OCR-draft or weakly extracted material; others are retrieval/query-sensitivity findings.

### Matrix by document

| Document | Questions | Directly source-backed | Review / partial | Result |
|---|---:|---:|---:|---|
| ISO 53:1998 | 6 | 6 | 0 | PASS |
| ISO 21771:2007 | 6 | 3 | 3 | PASS with retrieval sensitivity |
| ISO 4468:2020 | 4 | 3 | 1 | PASS with one unsupported variant |
| ISO 14104:2017 | 3 | 2 | 1 | PASS with one unsupported variant |
| ISO 1101:2017 | 4 | 1 | 3 | Canonical regression passed one review case |
| ISO 1328-1:2013 | 4 | 3 | 1 | PASS with one unsupported variant |
| ISO 286-1:2010 | 4 | 1 | 3 | Calculation/source-label review required |
| ISO 286-2:2010 | 3 | 0 | 3 | Source-routing review required |
| ISO 2490:2007 | 4 | 2 | 2 | Canonical table regression passed |
| ISO 54:1996 | 2 | 0 | 2 | OCR evidence remains gated |
| ISO/TR 10064-1:1992 | 2 | 0 | 2 | OCR/version-identity evidence remains gated |
| ISO/TR 10828:2015 | 3 | 0 | 3 | OCR/formula evidence remains gated |
| **Corpus total** | **45** | **21** | **24** | **Conditional QA pass** |

### Full question matrix

`PASS` means the live answer exposed a matching local ISO source. `REVIEW` means no claim was accepted as production evidence, or the source/answer needed further review.

| ID | Document | User question (short form) | Initial result | Evidence / finding |
|---|---|---|---|---|
| ISO53-01 | ISO 53 | Standard basic rack pressure angle | PASS | s. 6–7; 20° |
| ISO53-02 | ISO 53 | Pitch relation | PASS | s. 4–6; `p = πm` |
| ISO53-03 | ISO 53 | `haP`, `cP`, `hfP` values | PASS | s. 5, 7 |
| ISO53-04 | ISO 53 | `sP = eP` relation | PASS | s. 5; `sP = eP = πm/2` |
| ISO53-05 | ISO 53 | Type D dedendum | PASS | s. 7; `1,4m` |
| ISO53-06 | ISO 53 | `haP` and `hfP` ratios | PASS | s. 5–7 |
| ISO21771-01 | ISO 21771 | Backlash definition and purpose | REVIEW | Initial refusal; canonical combined regression passed on s. 44 |
| ISO21771-02 | ISO 21771 | Normal backlash `jbn` | PASS | s. 44 |
| ISO21771-03 | ISO 21771 | Three backlash types | REVIEW | Initial refusal; no source was accepted |
| ISO21771-04 | ISO 21771 | `mn` / `mt` relation | PASS | s. 18–19, 25–27 |
| ISO21771-05 | ISO 21771 | Helix-angle relationship in pair | REVIEW | Source returned, but answer did not cleanly satisfy requested pair relation |
| ISO21771-06 | ISO 21771 | Reference diameter / normal module relation | PASS | s. 18–19, 25, 61 |
| ISO4468-01 | ISO 4468 | Hob accuracy-grade names | PASS | s. 4–5, 43 |
| ISO4468-02 | ISO 4468 | Test 9B applicability | PASS | s. 10–12, 21 |
| ISO4468-03 | ISO 4468 | Annex A single-start solid hob | PASS | s. 34, 36–37 |
| ISO4468-04 | ISO 4468 | Outside-diameter recommendation | REVIEW | No verified source returned |
| ISO14104-01 | ISO 14104 | Class FD and FE | PASS | s. 18–22 |
| ISO14104-02 | ISO 14104 | Rehardened area after grinding | PASS | s. 15, 17–22 |
| ISO14104-03 | ISO 14104 | Surface-hardness measurement approach | REVIEW | No verified source returned |
| ISO1101-01 | ISO 1101 | Geometrical tolerance zone | REVIEW | Explicit refusal |
| ISO1101-02 | ISO 1101 | Median feature | PASS | s. 39, 40, 46, 58 |
| ISO1101-03 | ISO 1101 | Feature-control frame / indicator | REVIEW | No verified source returned |
| ISO1101-04 | ISO 1101 | Datum / datum plane role | REVIEW | No verified source returned |
| ISO1328-01 | ISO 1328-1 | `Fα` vs `ffα` | PASS | s. 10–14, 30, 37 |
| ISO1328-02 | ISO 1328-1 | `Fβ` vs `ffβ` | PASS | s. 12–14, 31, 39 |
| ISO1328-03 | ISO 1328-1 | Single-pitch deviation `fpt` | PASS | s. 17, 34, 47 |
| ISO1328-04 | ISO 1328-1 | Accuracy-grade expression | REVIEW | No verified source returned |
| ISO2861-01 | ISO 286-1 | 50 mm IT7 | REVIEW | Calculation card exposed table evidence but not a complete numeric answer |
| ISO2861-02 | ISO 286-1 | Fundamental deviation | PASS | s. 27, 30 |
| ISO2861-03 | ISO 286-1 | Tolerance-unit `i` formula | REVIEW | No verified source returned |
| ISO2861-04 | ISO 286-1 | Hole-basis H deviation | REVIEW | Source-backed figures/rules, but initial assertion check was incomplete |
| ISO2862-01 | ISO 286-2 | 50 mm H7 | REVIEW | Calculation cited ISO 286-1 rather than the named Part 2 source |
| ISO2862-02 | ISO 286-2 | H7 EI / ES | REVIEW | No verified source returned |
| ISO2862-03 | ISO 286-2 | Table size ranges | REVIEW | No verified source returned |
| ISO2490-01 | ISO 2490 | Scope/module range | PASS | s. 5–8; 0.5–40 |
| ISO2490-02 | ISO 2490 | Table 2 m=2 full row | REVIEW | Initial compound table query refused |
| ISO2490-03 | ISO 2490 | Table 2 m=5.5 values | PASS | s. 8; 100 mm / 32 mm etc. |
| ISO2490-04 | ISO 2490 | Coarse tolerance reference | REVIEW | No verified source returned |
| ISO54-01 | ISO 54 | Scope and module series | REVIEW | No verified source returned; all pages remain OCR draft |
| ISO54-02 | ISO 54 | m=6.5 series | REVIEW | Reproduced canonical query still refused |
| ISOTR10064-01 | ISO/TR 10064-1 | Title and purpose | REVIEW | No verified source returned; all pages remain OCR draft |
| ISOTR10064-02 | ISO/TR 10064-1 | Accuracy/measurement coverage | REVIEW | No verified source returned |
| ISOTR10828-01 | ISO/TR 10828 | Axial pitch / lead angle | REVIEW | No verified source returned; OCR/formula gate remains |
| ISOTR10828-02 | ISO/TR 10828 | Reference lead angle | REVIEW | No verified source returned |
| ISOTR10828-03 | ISO/TR 10828 | Type A / Type N profiles | REVIEW | Explicit refusal |
| UNSUPPORTED-01 | Corpus outside | ISO 9001 quality principles | PASS-SAFE | No fabricated citation; unverified/general-chat path |

## Controlled post-matrix regressions

| Regression | Result | Observation |
|---|---|---|
| ISO 21771 backlash + `jbn` canonical wording | PASS | Source-backed answer, s. 44 |
| ISO 2490 Table 2 m=2 with explicit dimensions | PASS | Source-backed table answer, s. 8 |
| ISO 1101 median-feature canonical wording | PASS | Source-backed answer, s. 39–58 |
| ISO 286-2 50 mm H7 EI/ES | PARTIAL | Calculation path cited ISO 286-1 pages 8/26/40, not the named Part 2 table; retain as source-routing finding |
| ISO 54 m=6.5 canonical wording | SAFE REFUSAL / OPEN | Still no verified source; do not promote OCR draft evidence |

## Remediation decision

The reproducible findings are evidence-quality and source-routing findings, not a safe reason to weaken the owner/evidence gate:

1. **Kept safe refusal behavior.** ISO 54, ISO/TR 10064-1, and ISO/TR 10828 remain unverified at answer time because their pages are OCR-draft/formula-risk material. Approval does not make draft extraction evidence-eligible.
2. **Confirmed canonical retrieval regressions.** Backlash, ISO 2490 Table 2, and ISO 1101 median-feature retrieval passed when expressed with the standard term and requested evidence explicitly.
3. **Flagged ISO 286 routing.** A Part 2 H7 question is answered through the Part 1 calculation/source path. This needs a source-mapping/calculation-contract correction before it can be called a clean Part 2 citation; no unsafe code or data edit was made during this approval run.
4. **No source bytes or extraction fingerprints were changed.** Reprocessing OCR/formula-heavy documents requires a separate controlled ingestion and owner re-review, because it would invalidate the current approval fingerprint.
5. **Fixed and regression-tested the Windows long-path watcher defect.** Recursive scans now use an extended-length root so a short configured root can still discover descendants beyond `MAX_PATH`; the existing `fs` helper remains conservative for libraries that reject prefixed short paths.

## Verification commands

| Check | Result |
|---|---|
| Backend suite with isolated repo TEMP | `380 passed, 9 skipped` (9 skips are missing optional real ISO fixtures) |
| Frontend Vitest | `23 passed` across 8 files |
| Frontend typecheck | PASS |
| Frontend production build | PASS |
| Live readiness | `hazır`; 4,045 indexed, 12 approved, 3,411 evidence-eligible |

## Release/readiness conclusion

The owner-approved corpus is operationally indexed and evidence-capable (`READY`, 3,411 eligible chunks), with audit events for all 12 approvals. The user-level QA is **conditionally accepted for controlled QA**, not a claim that every approved PDF is extraction-perfect. Production answers continue to be bounded by the evidence gate; the remaining REVIEW cases are explicitly recorded above for reprocessing, source-routing correction, or follow-up owner verification.
