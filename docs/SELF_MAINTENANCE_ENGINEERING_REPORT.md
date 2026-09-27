# DAYANERA.ai — Self-maintaining engineering knowledge: report

Date: 2026-09-27 · Branch: `claude/funny-fermi-qooox8` · Engine version: `1.0.0`

## 1. Outcome

DAYANERA now keeps a traceable **engineering knowledge system** next to the deterministic engine. A Qwen/engine
disagreement is no longer a dead audit row. It goes through this loop:

```text
validated ISO passages (app/calc/evidence.py REQ)  +  reference implementations (static AST scan)
                 │                                             │
                 └──────────► formula registry ◄───────────────┘   backend/app/knowledge/registry_data/gear_formulas.json
                                   │  normalization (DAYANERA variables, radians, mm)
                                   ▼
                         authority validation  ──► VERIFIED / CANDIDATE / UNVERIFIED / CONFLICT / REJECTED
                                   │                (computed, never declared; external code cannot verify alone)
                                   ▼
Qwen draft ─► deterministic engine ─► comparator ─► mismatch event (calc_mismatch_events)
                                                        │
                                                        ▼
                                  deterministic diagnosis (root-cause class by hypothesis testing)
                                                        │
                                                        ▼
                                  correction candidate  "<formula family>:<root cause>"
                                                        │  CANDIDATE → TESTING
                                                        ▼
                                  regression matrix over the FULL engine (evidence resolution included)
                                                        │
                                   passed ─► VERIFIED        failed ─► REJECTED        blocked ─► CANDIDATE
                                                        │
                                                        ▼
                                  correction memory (engineering_corrections) ─► targeted retrieval into later Qwen drafts
```

This is **not fine-tuning**. Qwen weights are not touched, and Qwen never receives repository code. The engine and the
validated ISO passages remain the numerical authority. Qwen explains, the engine calculates, the comparator validates,
and when they disagree the engine value is shown.

**Mandatory real-world case.** α_n = 20°, z = 30, m_n = 2 mm, β = 0, x = 0. The engine gives d = 60, d_b = 56.3816,
p_n = p_t = 6.2832, p_bt = 5.9043, h_a = 2, h_f = 2.5, h = 4.5, d_a = 64 and d_f = 55 (all in mm). The Qwen draft
(d_b = 53.452, p_bt = 5.345) is detected and classified as `LLM_ARITHMETIC_ERROR`. It produces the general correction
`base_circle:LLM_ARITHMETIC_ERROR`. That correction is verified by a 150-case regression and then retrieved into the next
Qwen draft. See section 11.

## 2. Architecture discovered (Phase 0)

| Concern | Existing implementation (kept, extended) |
|---|---|
| Qwen | `app/inference/ollama.py` (local Ollama only), `app/inference/prompts.py`, provider registry. Cloud adapters are hard-disabled |
| Prompts | `prompts.py`: general, verified-source, calc mapping, **calc draft** (extended with the optional VERIFIED knowledge block) |
| RAG / retrieval | `app/services/retrieval.py` (PostgreSQL FTS, structure-aware plan), `grounding.py` (number-grounded answers) |
| PDF ingestion / chunks | `app/ingestion/*` (PyMuPDF / Docling / OCR), `document_pages`, `document_chunks` with lineage (migration 0002) |
| ISO validation / source registry | verified-corpus lifecycle (`corpus_status`, owner approval), `app/calc/evidence.py` **REQ** = phrase-anchored ISO passage requirements |
| Deterministic gear engine | `app/calc/engine.py`, `app/calc/rules.py` (ISO 21771 geometry, gear pair, ISO 1328-1, ISO 286-1 H/h) |
| Formula definitions | `OutputValue.formula_id/expression` per output. Before this work there was no machine-readable registry |
| Citations / provenance | `EvidenceMatch` (document, version, page, excerpt), `message_sources` |
| Mismatch logging | `app/calc/compare.py` plus `calculations.comparison` / `mismatch` and the `calculation.llm_mismatch` audit row. This was a dead log |
| Audit | append-only `audit_events` (triggers block UPDATE/DELETE) |
| DB / schema | Alembic + portable SQL `database/migrations/sql/000N_*.sql`, optional Supabase sync |
| Tests | pytest against a temporary `dayanera_test` DB with a fake LLM, Vitest, Playwright |
| Deployment | Windows home-server scripts (`scripts/*.ps1`, Caddy). **Not run from this cloud session** |
| Self-maintenance before | none. `agent-notes/` holds human-reviewed advice only |

No parallel system was built. The registry anchors to the existing `REQ` evidence requirements. Validation calls
the existing `RULES`. The regression runs the existing `CalculationEngine` with the existing `DbEvidenceResolver`.
Mismatches extend the existing `run_calculation` path. Lifecycle history goes to the existing audit log.

## 3. Reference repositories

The Windows path `C:\Users\EbruHomePC\Documents\DAYANERA.ai\repositories` does not exist in the cloud container.
Canonical origins were used instead. Only useful repositories were cloned shallowly, to
`/tmp/dayanera-reference-repos/`, outside the DAYANERA tree. Nothing from them is committed.

| Repository | Origin | Commit | Licence | How obtained | Classification |
|---|---|---|---|---|---|
| freecad.gears | github.com/looooo/freecad.gears | `83ec154b` | GPL-3.0 | `--depth 1` | primary gear reference (pygears + pytest) |
| cq_gears | github.com/meadiode/cq_gears | `e73874cf` | Apache-2.0 | `--depth 1` | primary gear reference |
| FreeCAD | github.com/FreeCAD/FreeCAD | `dc53a203` | LGPL-2.1 | `--depth 1 --filter=blob:none --sparse`, only `PartDesign/fcgear`, `InvoluteGearFeature.py`, `TestInvoluteGear.py` (2.6 MB) | secondary gear reference |
| bd_warehouse | github.com/gumyr/bd_warehouse | `eed2da11` | Apache-2.0 | sparse, only `gear.py` and `test_gears.py` | secondary gear reference. Found while checking build123d: build123d has no gear module; its parts warehouse does |
| build123d, cadquery, ezdxf | canonical GitHub | tree listing only | — | `--filter=blob:none --no-checkout` | no gear formulas (cadquery: a DXF fixture; ezdxf: a render example) |
| OCCT, FreeCAD-library, Fusion360GalleryDataset, PaddleOCR, peft, docling, pdfplumber, RapidOCR | canonical GitHub | not cloned | — | — | irrelevant (kernel, binary library, dataset, OCR models, fine-tuning), or already an integrated ingestion dependency |
| CAD-Coder, CADFusion, cad-recode, Text2CAD, Ortho2CAD, freecad-mcp, SolidworksMCP, solidworks-mcp, Solidworks-MCP-Server, xcad | origin not resolvable from the cloud session (local Windows clones) | not cloned | — | — | LLM-CAD research or CAD automation: no validated gear mathematics expected |

The machine-readable version is in `backend/app/knowledge/registry_data/reference_repositories.json`.
`docs/knowledge/reference_scan_manifest.json` holds the scan result: 78/78 anchors `ok` at the recorded commits, and
237 unreviewed vocabulary discoveries (locations only).

## 4. Formulas and algorithms found

Found in executable code, with tests where the upstream project has them:

- **Fundamentals:** transverse module, reference diameter, base diameter, normal/transverse pitch, addendum,
  dedendum, tooth depth, tip/root diameter, ISO 53 basic rack coefficients (h_aP* = 1, h_fP* = 1.25, c_P* = 0.25, ρ_fP* = 0.38).
- **Involute:** involute function, involute polar angle at a radius, involute radius at a roll angle, tooth thickness
  at the reference circle (normal and transverse) and at any diameter, the base-circle-above-root condition.
- **Helical:** normal ↔ transverse module and pressure angle, lead, twist of the transverse section over the facewidth.
- **Pairs:** gear ratio, working pressure angle from the centre distance (ISO Eq. 54), and the profile-shift
  relation inv α_wt = inv α_t + 2 tan α_n (x1+x2)/(z1+z2). Also the working centre distance, and internal-pair variants.
- **Other gears:** planetary ring tooth count, assembly condition and ratio; worm lead angle and lead.
- **CAD geometry:** involute sampling, Higuchi spline approximation (FreeCAD), trochoid undercut, root fillet, helical
  sweep, tooth replication. These are recorded as geometry algorithms and are not numeric rules. No tolerances are exchanged with the engine.

**Not found in any inspected repository:** contact ratio, overlap ratio, span measurement / base tangent length
(Wildhaber), measurement over pins/balls, diametral pitch, minimum tooth count without undercut, backlash
tolerances. None of these was invented. They are listed in section 17.

## 5. Status counts (computed by `app.knowledge.validation`)

| Status | Count | Meaning |
|---|---:|---|
| **VERIFIED** | **23** | DAYANERA ISO evidence requirement **and** engine agreement over the validation matrix (6 157 engine cases, max rel. error < 1e-9): 14 ISO 21771/ISO 53 rules, 8 ISO 1328-1 formulas, the ISO 53 rack constants (vs the engine defaults) |
| **CANDIDATE** | **18** | no DAYANERA ISO anchor. Supported by numerically agreeing implementations: 8 by ≥ 2 independent repositories, 3 also consistent with the VERIFIED engine (pair round trip through ISO Eq. 54), and 1 derived only from VERIFIED rules |
| **UNVERIFIED** | **1** | a freecad.gears CAD tooth-phase convention (implementation convention, not engineering) |
| **CONFLICT** (rule level) | **0** | engine vs normalized registry: none |
| **REJECTED** | **1** | the freecad.gears Newton "derivative" `1/cos α − 1` of inv α (true derivative tan² α) |
| Conflict records (external vs authority) | **10** | see section 12 |
| External observations | 67 SUPPORTS · 10 CONFLICT · 1 not comparable | freecad.gears 27/2 · bd_warehouse 14/0 · cq_gears 19/7 · FreeCAD 7/1 |

The full per-rule table is in `docs/knowledge/formula_registry_validation.json`. A test keeps it current, like `docs/openapi.json`.

## 6. Provenance

Every rule carries its stable ID, domain/family, name (EN/TR), normalized expression, variables with units and
dimensions, assumptions, applicability (external/internal, spur/helical, profile shift), valid ranges, ISO citation
and `REQ` evidence IDs, engine binding(s) and DAYANERA tests. Each external observation records repository, file,
symbol, line, commit, an AST fingerprint, domain restriction, convention, upstream tests and licence.

`GET /api/v1/knowledge/formulas/{id}` answers "Where did this formula come from?" For `gear.cyl.base_diameter` it returns:

- ISO 21771:2007 4.3.5 Eq. (13) and 4.3.10 Eq. (19), resolved **live** in the caller's active verified corpus
  (document, version, page, excerpt);
- the engine: `app.calc.rules.CylindricalGearGeometry.compute`, `backend/app/calc/rules.py` and the line of `_out("d_b", …)`;
- supporting implementations with commit permalinks: freecad.gears `pygears/involute_tooth.py#L119` (GPL-3.0),
  bd_warehouse `gear.py#L160`, cq_gears `spur_gear.py#L92` (β = 0 domain), FreeCAD `fcgear/involute.py#L145` (spur);
- the conflicting implementation: cq_gears `crossed_helical_gear.py#L53`;
- validation evidence (274 engine cases, identities) and the DAYANERA tests.

Other endpoints: `/knowledge/formulas`, `/knowledge/conflicts`, `/knowledge/references` and `/knowledge/summary`.
The owner-only endpoints are `/knowledge/mismatches`, `POST /knowledge/corrections` and `POST /knowledge/corrections/{id}/verify`.

## 7. Self-maintenance implementation

- **Comparator:** the existing `compare()` is unchanged (0.5 % relative tolerance or a per-unit absolute one; fixed
  tolerance for limit sizes).
- **Mismatch event** (`calc_mismatch_events`, migration 0003) stores:
  - the ID, time, calc type, engine version and model;
  - normalized numeric inputs after the engine defaults; **no message text**;
  - assumptions, Qwen values, engine values and per-field absolute/relative error and tolerance;
  - the mismatching fields, formula IDs, authoritative ISO sources (document, version, page) and supporting repositories;
  - the suspected class, the full diagnosis, the correction, the regression status and a disposition.
- **Diagnosis** (`app/knowledge/diagnosis.py`) is deterministic hypothesis testing:
  1. Explicit error models run on the resolved inputs, for example "cos of the degree value as radians",
     "α_n used in the transverse plane", "d = z·m_n (β omitted)", "x omitted", "internal-gear sign" and
     "d·cos² α". There are also generic unit, angle-unit and rounding models.
  2. A hypothesis explains a value only if it reproduces the draft within tolerance **and** differs from the correct
     value. Normal/transverse confusion at β = 0 is therefore *not applicable*, not "ruled out".
  3. When nothing structural reproduces the value but it is physically plausible (0 < d_b < d, …), the class is
     `LLM_ARITHMETIC_ERROR`.
  4. Implied pressure angles and draft invariants (p_bt·z = π·d_b, d_b/d = p_bt/p_t, h = h_a + h_f …) are reported.

  An LLM may *suggest* a class (`llm_suggested_class`). That suggestion is stored separately and is never authoritative.
- **Root-cause classes:** ANGLE_UNIT_ERROR, DEGREE_RADIAN_ERROR, WRONG_PRESSURE_ANGLE, NORMAL_TRANSVERSE_CONFUSION,
  MODULE_CONVERSION_ERROR, WRONG_BASE_CIRCLE_FORMULA, WRONG_BASE_PITCH_FORMULA, PROFILE_SHIFT_OMITTED,
  INTERNAL_EXTERNAL_SIGN_ERROR, HELIX_ANGLE_OMITTED, ROUNDING_ERROR, UNIT_ERROR, FORMULA_SELECTION_ERROR,
  LLM_ARITHMETIC_ERROR, UNKNOWN. Twelve parametrized tests check that each structural class is recognized.
- **Failure isolation:** processing runs in a savepoint. A failure is logged and marked in the comparison, and it never
  fails the calculation (tested).

## 8. Correction memory

`engineering_corrections` holds one row per **general rule**, keyed `<formula family>:<root cause>` (for example
`base_circle:LLM_ARITHMETIC_ERROR`), never per numeric example. A correction has these parts:

- **claims:** one normalized expression per output, by default the VERIFIED registry expression. The regression
  evaluates the claims against the engine, so a wrong claim is rejected, never trusted.
- **invariants:** for example `p_bt·z = π·d_b`.
- **reference values:** for example cos 20° for the ISO 53 α_P, recomputed and checked by the regression.
- **statement:** a Turkish statement rendered from the same VERIFIED rules and ISO citations.

It also records the source (`diagnosis` or `proposal`, from a person or an LLM), the status, the regression result,
the engine version, the registry fingerprint and the occurrence count.

**Lifecycle.** `CANDIDATE → TESTING → VERIFIED | REJECTED`. A blocked run (ISO passage missing) or a claim without a
VERIFIED engine-bound rule returns to `CANDIDATE`. Every transition is an append-only audit row
(`knowledge.correction.status`). A DB check constraint forbids `VERIFIED` without a passed regression, engine version
and registry fingerprint. **Qwen cannot self-certify.** `verify_correction` is the only status writer and it only runs
the deterministic regression. A recurring mismatch reuses the VERIFIED rule (`known_verified_correction`) without
re-learning. Verdicts are re-checked automatically at startup, via `knowledge-reverify`, or on the next mismatch, when
the engine version or the registry changes.

## 9. Regression system (`app/knowledge/regression.py`)

The regression never learns from one example. For each formula family it generates a matrix and runs the **full**
engine, evidence resolution included. It then checks:

1. the claims, evaluated on an environment derived independently of the engine;
2. the invariants on the engine outputs;
3. invariance (d_b and p_bt must not change with x);
4. the comparator: the exact draft passes, and a draft off by more than the tolerance is flagged on that key only;
5. the quoted reference values.

Tolerance: relative 1e-9 for math agreement. Expected values come only from the engine and the VERIFIED registry.

| Family | Matrix | Cases | Checks | Result |
|---|---|---:|---:|---|
| base_circle (d_b, p_bt) | α ∈ {20°, 25°} × m ∈ {1, 2, 3} × z ∈ {17, 20, 30, 40, 80}; + β ∈ {15°, 30°}; + x ∈ {−0.3, 0.5} (twins) | 150 | 1 621 | passed (~110 ms) |
| transverse_conversion | same | 150 | 1 351 | passed |
| transverse_conversion (calc `transverse_module`) | m_n ∈ {0.5, 1, 2, 3, 8} × β ∈ {0°, 10°, 15°, 20°, 25°, 30°, 45°}. Covers the strict-ISO T8 production case (Qwen m_t = 2.142 vs 2.2068) | 35 | — | passed |
| reference_diameter | same | 150 | 810 | passed |
| pitch | same | 150 | 1 470 | passed |
| tooth_depth / tip_root_diameter | same + 10 stub-tooth/k cases | 160 | 1 760 / 1 440 | passed |
| gear_pair | z1 × z2 × m × α × β, a_w = 1.02·a | 72 | 576 | passed |
| iso1328_tolerance | A ∈ {1, 4, 6, 8, 11} × m_n × d × b | 90 | 2 340 | passed |

Internal gears are **not** added to the engine regression, because the engine supports external gears only.
Internal-pair relations stay CANDIDATE in the registry.

## 10. Qwen retrieval integration

`knowledge_for_draft()` runs before every Qwen calculation draft, both in chat and in the API with `compare_with_llm`.
It adds to the **user** message only:

- VERIFIED formula definitions, as display form plus ISO citation;
- VERIFIED, non-stale corrections for that calc type, with the outputs requested (at most 3).

It never adds candidates or repository code. `KNOWLEDGE_CONTEXT_MODE`:

- `targeted` (default) adds only the families that already have a VERIFIED correction. The prompt is therefore
  unchanged until a mismatch has taught something. This matters because CPU prompt evaluation runs at about 10 tok/s.
- `all` adds every VERIFIED formula of the calc type.
- `off` adds nothing.

The draft record stores which formula and correction IDs it saw (`knowledge_context`). The verified-source (ISO
passage) answer path is deliberately unchanged. Its grounding validator accepts only numbers that appear in the
retrieved passages, and registry text is not a passage.

UX on a mismatch: the chat reply keeps the engine values and adds
**"Hesap motoru doğrulaması: LLM taslağında uyuşmazlık tespit edildi."**, the Turkish root-cause label, the class, the
fields and the correction state with its regression case count. *Ayrıntılı çözüm* keeps inputs, assumptions, formulas,
units, sources, engine version and validation trace, and adds a self-maintenance panel. The System page shows registry,
mismatch and correction counts.

## 11. Base-circle / base-pitch investigation

Checked independently against:

- the DAYANERA ISO requirements `iso21771.eq13`, `eq19` and `eq28`;
- the engine;
- four implementations: freecad.gears `dg = d·cos(pressure_angle_t)`, bd_warehouse `base_radius = pitch_radius·cos(α_t)`,
  cq_gears `rb = cos(a0)·d0/2` and FreeCAD `Rb = Rref·cos(α)`.

All of them agree on the **general rule**:

> d_b = d·cos α_t = z·m_t·cos α_t, p_bt = p_t·cos α_t = π·m_t·cos α_t = π·d_b / z, with tan α_t = tan α_n / cos β
> (β = 0 ⇒ α_t = α_n, m_t = m_n). Both are independent of x and k.

Findings about the Qwen draft (d_b = 53.452, p_bt = 5.345):

- The implied angles are arccos(53.452/60) = **27.02°** and arccos(5.345/6.2832) = **31.71°**. They differ from each
  other and from 20°, and no standard angle matches (so the class is not WRONG_PRESSURE_ANGLE).
- The draft **contradicts itself**: p_bt·z = 160.35 but π·d_b = 167.92 (4.5 % off).
- These hypotheses do **not** reproduce it: degree-as-radian (24.49), d·sin α (20.52), d/cos α (63.85),
  d·cos² α (52.98), wrong diameter, and unit conversions.
- Normal/transverse confusion, helix omission and module conversion cannot apply at β = 0. Profile shift cannot matter,
  because x = 0 and d_b does not depend on x.
- **Root cause: LLM_ARITHMETIC_ERROR.** Qwen used the right formula family but evaluated the cosine wrongly and
  inconsistently. The error is not in the formula or in DAYANERA.

The engine is correct and was not changed. The correction learned is the family rule plus the invariant check
(`p_bt·z = π·d_b`) and the ISO 53 reference value cos 20° = 0.93969. It is not the example: a test asserts that no
number from the case appears in the statement.

## 12. Conflicts with the ISO-backed engine (engine unchanged, conflict recorded)

| Implementation | Location | Finding | Class |
|---|---|---|---|
| cq_gears `CrossedHelicalGear` | `crossed_helical_gear.py#L40` | `at0 = arctan(a0 / cos β)` uses the angle in radians instead of tan α_n. It is wrong even for β = 0: 19.24° instead of 20° | DEGREE_RADIAN_ERROR |
| cq_gears `CrossedHelicalGear` | `#L53` | base radius inherits the at0 defect (+0.47 % for z = 17, m = 1) | DEGREE_RADIAN_ERROR |
| cq_gears `CrossedHelicalGear` | `#L44–47` | addendum and dedendum on m_t, although the input is the normal module (ISO: h_aP = 1·m_n). This also affects d_a and d_f | NORMAL_TRANSVERSE_CONFUSION |
| freecad.gears `InvoluteTooth` | `involute_tooth.py#L52` | library default clearance 0.12 vs ISO 53 c_P = 0.25 (the workbench overrides it with 0.25) | constant deviation |
| FreeCAD `CreateExternalGear` | `fcgear/involute.py#L33` | root fillet 0.375 vs ISO 53 ρ_fP = 0.38 (a documented legacy default) | constant deviation |
| cq_gears `PlanetaryGearset` | `ring_gear.py#L282` | a stricter assembly test rejects assemblable sets (z_s = 13, z_p = 12, n = 2) that freecad.gears accepts. There is no ISO authority, so the rule stays CANDIDATE | external disagreement |
| freecad.gears `compute_shifted_gears` | `computation.py#L48` | Newton "derivative" `1/cos x − 1` of inv x (true: tan² x). It still converges there, but the claim is REJECTED | failed math check |

Convention differences are *not* conflicts. They are validated inside their domain (`domain: {"beta": 0}`):
freecad.gears' default transverse system (`properties_from_tool = False`), cq_gears `SpurGear` twisting a spur
profile, and FreeCAD's spur-only profile.

## 13. Security decisions

- Reference repositories are treated as untrusted. They were cloned shallowly into `/tmp`, with `core.hooksPath=/dev/null`,
  and nothing was installed, imported or executed.
- The scanner uses `ast.parse` only. A test proves that a module which writes a file and calls `SystemExit` at import
  is scanned without running. Only `*.py` files under 1 MB are scanned, and vendored/`.git` folders are skipped.
- `knowledge-scan` refuses a path inside the DAYANERA tree. Nothing from the clones is committed.
- GPL/LGPL code is never copied. Formulas are clean-room normalized expressions with citations. Manifests store
  locations and hashes, never third-party source text.
- Registry expressions are evaluated by a hand-written AST walker. It has a function whitelist and no
  `eval`/attribute/subscript/lambda. The claims of API proposals go through the same evaluator.
- No `.env`, credentials or tokens were read or written. The repository's preflight rules (forbidden paths and secret
  patterns) were re-implemented in Python because PowerShell is not available here: 222 tracked files, PASS.
- An earlier layout put the registry under `knowledge/data/`. That path is git-ignored (`data/`) and forbidden by the
  preflight, so it was moved to `registry_data/`. A regression test keeps it tracked.

## 14. Observability

The loggers are `dayanera.knowledge` and `dayanera.selfmaint`. Each writes one structured line per event:

- `knowledge.validation rules= verified= … engine_cases=`, and `knowledge.validation.engine_conflict` at ERROR level;
- `selfmaint.mismatch event= calc_type= fields= class=`;
- `selfmaint.correction.candidate` / `.verified cases= checks= ms=` / `.rejected first_failure=` / `.blocked`;
- `selfmaint.retrieval formulas= corrections= chars=`.

The audit rows are `knowledge.correction.created`, `knowledge.correction.status` and `knowledge.mismatch.recorded`.
`/system/status` and the System page show the counts. There is no per-line noise.

## 15. Tests and results

| Suite | Result |
|---|---|
| Backend baseline before changes | 275 passed, 2 skipped |
| Backend after (`-m "not live and not corpus"`, slow included) | **326 passed, 2 skipped** (PowerShell preflight: no pwsh in the container; faster-whisper model not downloaded). The frontend-dist test, skipped in the baseline, now runs and passes |
| Backend `-m slow` | 4 passed, 1 skipped (faster-whisper model not downloaded) |
| Backend `-m corpus` / `-m live` | not run: they need the licensed ISO booklets and the running Qwen stack, which are not in this cloud container |
| New: `test_knowledge_registry.py` | 17 tests (registry, evaluator safety, statuses, conflicts, rejection, provenance, scanner, snapshots) |
| New: `test_self_maintenance.py` | 26 tests (12 of them parametrized diagnosis classes), including the mandatory case, the T8 transverse-module case, the regression matrix, proposal rejection, blocked evidence, API/chat end to end, staleness and failure isolation |
| Frontend `npm run typecheck` | pass |
| Frontend `vitest` | 18 passed (new `selfmaint.test.tsx`) |
| Frontend `npm run build` | success. The chunk-size warning is pre-existing (KaTeX bundle) |
| Ruff (new files) | clean except B008 (FastAPI `Depends`, the codebase-wide pattern). The repository baseline has 322 pre-existing findings. The 5 non-B008 findings left in modified files are on lines from earlier commits (`git blame`: 88166492 / 322f8b8) |

## 16. Files changed

New:

- `backend/app/knowledge/`: `expr.py`, `registry.py`, `validation.py`, `scanner.py`, `diagnosis.py`, `corrections.py`, `regression.py`
- `backend/app/knowledge/registry_data/`: `gear_formulas.json`, `reference_repositories.json`
- `backend/app/services/self_maintenance.py` and `backend/app/api/routes/knowledge.py`
- `database/migrations/sql/0003_self_maintenance{,.down}.sql` and `versions/0003_self_maintenance.py`
- `docs/knowledge/formula_registry_validation.json`, `docs/knowledge/reference_scan_manifest.json` and this report
- `tests/backend/test_knowledge_registry.py`, `tests/backend/test_self_maintenance.py` and `frontend/src/test/selfmaint.test.tsx`

Modified:

- `backend/app/services/calculations.py`: retrieval, mismatch processing and the notice
- `chat.py`, `serialize.py`, `system_status.py`, `database_sync.py` (new tables are local-only)
- `db/models.py`, `api/schemas.py`, `cli.py`, `core/config.py`, `inference/prompts.py`, `main.py`
- `frontend/src/api/types.ts`, `components/CalcDetail.tsx`, `pages/SystemPage.tsx`
- `docs/openapi.json`, `README.md`, `.env.example`, `tests/backend/test_api_contract.py`, `tests/backend/test_git_safety.py`

## 17. Unresolved limitations

- **Push blocked:** the GitHub App has no push access to `bedizoymak/DAYANERA.ai` from this session (HTTP 403). The
  commits exist locally on `claude/funny-fermi-qooox8`.
- Corpus and live suites were not run (no ISO booklets, no Ollama/Qwen here). The synthetic corpus uses the same
  evidence phrases. Run `scripts/test-all.ps1 -Live` on the home server.
- No deployment was performed. The deployment workflow is Windows/home-server only.
- The diagnosis error models cover cylindrical geometry, the gear pair and ISO 1328. ISO 286 lookups get only the
  generic unit and rounding models, and they have no registry rules, so their corrections stay CANDIDATE (`no_authority`).
- CANDIDATE rules (involute function, profile-shift pairs, tooth thickness, lead, planetary, worm) are not retrieved
  and not computed by the engine. They need a DAYANERA ISO evidence requirement (a passage in the licensed corpus) and
  an engine rule before they can become VERIFIED.
- The LLM root-cause *suggestion* field exists but is not populated. A Qwen diagnosis call would cost about 1 minute
  on CPU, and it could never be authoritative anyway.
- `ensure_correction` is not concurrency-hardened. Two simultaneous first mismatches of the same family would make
  one savepoint fail (the calculation is still saved). Acceptable for the single-user, serial-Ollama beta.
- The 237 unreviewed scanner discoveries have not been normalized yet. They are the work queue for the next round.

## 18. Recommended next engineering areas

1. Add ISO 21771 evidence requirements and engine rules for the **involute function, tooth thickness (s_n, s_yt) and
   profile-shifted centre distance**. These CANDIDATEs already agree with 1–4 implementations and with the engine.
2. **Contact ratio ε_α / overlap ratio ε_β / total ε_γ** (ISO 21771 Clause 5) and **span measurement W_k / measurement
   over balls M_d** (ISO 21771 Annex / DIN 3960). They are absent from all references, so they need ISO-first implementation.
3. **Minimum tooth count and undercut** (z_min = 2·h_aP*/sin² α_t) and **internal gears** (z < 0 convention).
4. Backlash and tooth-thickness tolerances (ISO 21771 / ISO 1328-2 context) and diametral pitch conversions.
5. Extend the diagnosis error models to ISO 286 fundamental deviations once more letters than H/h are supported.
6. Run `knowledge-scan` periodically against upstream HEADs. A `changed` or `missing` anchor forces re-review before
   an observation may support a rule again.

## 19. Operating commands (from `backend/`)

```powershell
python -m app.cli migrate                    # applies 0003_self_maintenance
python -m app.cli knowledge-validate --out ../docs/knowledge/formula_registry_validation.json
python -m app.cli knowledge-scan --repos C:\path\outside\repo --out ../docs/knowledge/reference_scan_manifest.json [--update-registry]
python -m app.cli knowledge-reverify         # after an engine or registry change (also runs at API startup)
```
