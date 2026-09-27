"""Self-maintenance: Qwen draft -> engine -> comparator -> mismatch event ->
diagnosis -> correction candidate -> deterministic regression -> correction
memory -> targeted retrieval. Unit tests use in-memory evidence; the API
tests run against the temporary database with the synthetic ISO corpus."""
from __future__ import annotations

import itertools
import json
import math
import time
from dataclasses import asdict

import pytest
from app.calc.compare import compare
from app.calc.engine import CalculationEngine
from app.calc.evidence import REQ, InMemoryEvidenceResolver, PageRecord
from app.calc.types import CalcRequest, InputValue, Provenance
from app.knowledge.corrections import build_from_diagnosis, build_proposal
from app.knowledge.diagnosis import diagnose
from app.knowledge.regression import run_regression
from conftest import audit_rows
from synthetic_corpus import write_all

CODES = {"iso21771": "ISO 21771:2007", "iso53": "ISO 53:1998", "iso1328": "ISO 1328-1:2013"}
# the production mismatch this work started from (Qwen draft vs engine)
QWEN_DRAFT = {"d_b": 53.452, "p_bt": 5.345}


def _pages() -> list[PageRecord]:
    out = []
    for rid, req in REQ.items():
        code = CODES.get(rid.split(".")[0])
        if code:
            out.append(PageRecord(rid, f"{rid}-v1", 1, code, code, 1, "s. 1", "\n".join(req.must_contain)))
    return out


RESOLVER = InMemoryEvidenceResolver(_pages())


def _run(calc_type="cylindrical_gear_geometry", **inputs):
    units = {"m_n": "mm", "alpha_n": "°", "beta": "°", "d": "mm", "b": "mm"}
    p = Provenance("user_input", "msg", "user_input")
    return CalculationEngine(RESOLVER).run(CalcRequest(calc_type, {
        k: InputValue(k, v, units.get(k, ""), p) for k, v in inputs.items()}))


def _spur(**over):
    return _run(**{"z": 30, "m_n": 2, "alpha_n": 20, "beta": 0, "x": 0, **over})


def _diagnose(result, draft):
    cmp = compare(result, draft)
    return cmp, diagnose(result.calc_type, {i["key"]: i["canonical_value"] for i in result.inputs},
                         [asdict(o) for o in result.outputs], cmp)


# ---------------------------------------------------------------- mandatory case
def test_mandatory_spur_gear_values():
    r = _spur()
    assert r.status == "ok", r.diagnostics
    out = r.output_map()
    assert out["m_t"] == pytest.approx(2.0) and out["alpha_t"] == pytest.approx(20.0)
    assert out["d"] == pytest.approx(60.0)
    assert out["d_b"] == pytest.approx(56.3816, abs=1e-4)
    assert out["p_n"] == pytest.approx(6.2832, abs=1e-4) and out["p_t"] == pytest.approx(6.2832, abs=1e-4)
    assert out["p_bt"] == pytest.approx(5.9043, abs=1e-4)
    assert out["h_a"] == pytest.approx(2.0) and out["h_f"] == pytest.approx(2.5) and out["h"] == pytest.approx(4.5)
    assert out["d_a"] == pytest.approx(64.0) and out["d_f"] == pytest.approx(55.0)
    # the general rule, not the example: d_b = d·cos α_t and p_bt = p_t·cos α_t = π·d_b/z
    assert out["d_b"] == pytest.approx(60 * math.cos(math.radians(20)))
    assert out["p_bt"] * 30 == pytest.approx(math.pi * out["d_b"])


def test_qwen_draft_mismatch_detected_and_diagnosed():
    r = _spur()
    cmp, dg = _diagnose(r, {"d": 60, **QWEN_DRAFT})
    assert cmp["mismatch"] is True
    assert sorted(i["key"] for i in cmp["items"] if i["status"] == "mismatch") == ["d_b", "p_bt"]
    # engine values stay authoritative
    assert r.output_map()["d_b"] == pytest.approx(56.3816, abs=1e-4)
    assert dg["primary_class"] == "LLM_ARITHMETIC_ERROR"
    assert dg["formula_ids"] == ["gear.cyl.base_diameter", "gear.cyl.transverse_base_pitch"]
    assert dg["families"] == ["base_circle"]
    assert set(dg["authoritative_sources"]) == {"iso21771.eq13", "iso21771.eq19", "iso21771.eq28"}
    # hypotheses that were tested and failed / cannot apply for a spur gear (β = 0 ⇒ α_t = α_n, m_t = m_n)
    assert {"DEGREE_RADIAN_ERROR", "WRONG_BASE_CIRCLE_FORMULA", "WRONG_BASE_PITCH_FORMULA", "UNIT_ERROR"} <= set(dg["ruled_out_classes"])
    assert {"NORMAL_TRANSVERSE_CONFUSION", "HELIX_ANGLE_OMITTED", "MODULE_CONVERSION_ERROR"} <= set(dg["not_applicable_classes"])
    fields = {f["key"]: f for f in dg["fields"]}
    assert fields["d_b"]["implied_angle_deg"] == pytest.approx(27.02, abs=0.05)
    assert fields["p_bt"]["implied_angle_deg"] == pytest.approx(31.71, abs=0.05)
    # the draft contradicts itself: p_bt·z ≠ π·d_b
    inv = {i["name"]: i for i in dg["invariants"]}
    assert inv["p_bt·z = π·d_b"]["holds"] is False and dg["draft_internally_consistent"] is False


@pytest.mark.parametrize("over, draft, expected", [
    ({}, {"d_b": 60 * math.cos(20.0)}, "DEGREE_RADIAN_ERROR"),                      # cos(20 rad)
    ({}, {"d_b": 60 * math.cos(math.radians(25))}, "WRONG_PRESSURE_ANGLE"),         # α = 25° instead of 20°
    ({}, {"d_b": 60 * math.sin(math.radians(20))}, "WRONG_BASE_CIRCLE_FORMULA"),
    ({}, {"d_b": 56.0}, "ROUNDING_ERROR"),
    ({}, {"d_b": 56.3816 / 25.4}, "UNIT_ERROR"),
    ({}, {"alpha_t": math.radians(20)}, "ANGLE_UNIT_ERROR"),
    ({}, {"d_a": 60 - 4.0}, "INTERNAL_EXTERNAL_SIGN_ERROR"),
    ({"beta": 20}, {"d_b": 30 * 2 / math.cos(math.radians(20)) * math.cos(math.radians(20))}, "NORMAL_TRANSVERSE_CONFUSION"),
    ({"beta": 20}, {"d": 60.0}, "HELIX_ANGLE_OMITTED"),
    ({"beta": 20}, {"m_t": 2 * math.cos(math.radians(20))}, "MODULE_CONVERSION_ERROR"),
    ({"x": 0.5}, {"d_a": 64.0}, "PROFILE_SHIFT_OMITTED"),
    ({"beta": 20}, {"p_bt": math.pi * 2 * math.cos(math.radians(20))}, "NORMAL_TRANSVERSE_CONFUSION"),
])
def test_diagnosis_classifies_structural_errors(over, draft, expected):
    r = _spur(**over)
    cmp, dg = _diagnose(r, draft)
    assert cmp["mismatch"], cmp
    assert dg["primary_class"] == expected, dg["fields"]


def test_diagnosis_never_uses_an_llm(monkeypatch):
    import app.inference.registry as reg

    monkeypatch.setattr(reg, "get_provider", lambda *a, **k: (_ for _ in ()).throw(AssertionError("LLM called")))
    _cmp, dg = _diagnose(_spur(), QWEN_DRAFT)
    assert dg["method"].startswith("deterministic")


# ---------------------------------------------------------------- correction + regression
def test_base_circle_regression_matrix():
    _cmp, dg = _diagnose(_spur(), QWEN_DRAFT)
    [spec] = build_from_diagnosis("cylindrical_gear_geometry", dg)
    assert spec.key == "base_circle:LLM_ARITHMETIC_ERROR" and spec.output_keys == ["d_b", "p_bt"]
    assert {c["expression"] for c in spec.claims} == {"d*cos(alpha_t)", "p_t*cos(alpha_t)"}
    # general rule: none of the example's numbers are learned
    for number in ("53,452", "53.452", "5,345", "5.345", "56,38", "56.38", "5,904", "5.904"):
        assert number not in spec.statement
    assert "d_b = d·cos α_t" in spec.statement and "p_bt·z = π·d_b" in spec.statement
    result = run_regression(spec, RESOLVER)
    assert result.status == "passed", result.failures
    assert result.groups == {"spur": 30, "helical": 60, "profile_shift": 60} and result.cases == 150
    assert result.checks > 1500 and not result.failures


def test_regression_matrix_values_come_from_authoritative_formulas():
    """α ∈ {20°, 25°} × m ∈ {1, 2, 3} × z ∈ {17, 20, 30, 40, 80}: engine = d·cos α and p_t·cos α."""
    for an, m, z in itertools.product((20, 25), (1, 2, 3), (17, 20, 30, 40, 80)):
        out = _spur(alpha_n=an, m_n=m, z=z).output_map()
        a = math.radians(an)
        assert out["d_b"] == pytest.approx(z * m * math.cos(a), rel=1e-12)
        assert out["p_bt"] == pytest.approx(math.pi * m * math.cos(a), rel=1e-12)
        shifted = _spur(alpha_n=an, m_n=m, z=z, x=0.5).output_map()  # base circle independent of x
        assert shifted["d_b"] == pytest.approx(out["d_b"], rel=1e-12)


def test_llm_proposed_wrong_correction_is_rejected():
    wrong = build_proposal("cylindrical_gear_geometry", "base_circle", "LLM_ARITHMETIC_ERROR",
                           [{"output": "d_b", "rule_id": "gear.cyl.base_diameter", "expression": "d*cos(alpha_t)**2"}],
                           "Qwen: d_b = d·cos² α", "llm")
    res = run_regression(wrong, RESOLVER)
    assert res.status == "failed" and res.failures[0]["check"] == "claim_vs_engine"
    right = build_proposal("cylindrical_gear_geometry", "base_circle", "LLM_ARITHMETIC_ERROR",
                           [{"output": "d_b", "expression": "z*m_n*cos(alpha_t)/cos(beta)"}], "doğru", "llm")
    assert run_regression(right, RESOLVER).status == "passed"
    no_authority = build_proposal("iso286_it_tolerance", "iso286", "ROUNDING_ERROR",
                                  [{"output": "IT", "expression": "1"}], "x", "llm")
    assert run_regression(no_authority, RESOLVER).status == "not_applicable"


def test_regression_is_blocked_without_iso_evidence():
    _cmp, dg = _diagnose(_spur(), QWEN_DRAFT)
    [spec] = build_from_diagnosis("cylindrical_gear_geometry", dg)
    res = run_regression(spec, InMemoryEvidenceResolver([]))
    assert res.status == "blocked" and res.cases == 0
    assert any("ISO 21771" in r for r in res.blocked_reason)


def test_transverse_module_mismatch_gets_a_verified_correction():
    """The strict-ISO T8 production case: Qwen drafted m_t = 2.142 for m_n = 2 mm, β = 25°."""
    r = _run("transverse_module", m_n=2, beta=25)
    cmp, dg = _diagnose(r, {"m_t": 2.142})
    assert cmp["mismatch"] and r.output_map()["m_t"] == pytest.approx(2.2068, abs=1e-4)
    assert dg["primary_class"] == "LLM_ARITHMETIC_ERROR" and dg["families"] == ["transverse_conversion"]
    [spec] = build_from_diagnosis("transverse_module", dg)
    res = run_regression(spec, RESOLVER)
    assert res.status == "passed" and res.cases == 35 and res.groups == {"spur": 5, "helical": 30}
    _cmp, dg2 = _diagnose(_run("transverse_module", m_n=2, beta=25), {"m_t": 2 * math.cos(math.radians(25))})
    assert dg2["primary_class"] == "MODULE_CONVERSION_ERROR"


def test_tolerance_family_regression_passes():
    r = _run("iso1328_flank_tolerance", m_n=2, d=60, b=20, A=6)
    cmp = compare(r, {"f_pT": r.output_map()["f_pT"] + 3})
    dg = diagnose(r.calc_type, {i["key"]: i["canonical_value"] for i in r.inputs}, [asdict(o) for o in r.outputs], cmp)
    [spec] = build_from_diagnosis(r.calc_type, dg)
    assert spec.family == "iso1328_tolerance"
    assert run_regression(spec, RESOLVER).status == "passed"


# ---------------------------------------------------------------- API / chat integration
@pytest.fixture(scope="module")
def sm_corpus(settings, admin):
    from app.ingestion.watcher import Watcher
    from conftest import run_jobs

    write_all(settings.iso_booklets_path / "calc")
    time.sleep(2.1)
    Watcher(settings).scan()
    run_jobs(settings)
    types = {t["calc_type"]: t for t in admin.get("/calculations/types").json()}
    assert types["cylindrical_gear_geometry"]["available"]
    yield types


MANDATORY_INPUTS = {"z": {"value": 30}, "m_n": {"value": 2, "unit": "mm"}, "alpha_n": {"value": 20, "unit": "°"},
                    "beta": {"value": 0, "unit": "°"}, "x": {"value": 0}}


def _draft_responder(outputs: dict, seen: list):
    def respond(messages):
        system = messages[0].content
        if "TASLAK" in system and "outputs" in system:
            seen.append(messages[-1].content)
            return json.dumps({"outputs": outputs})
        if "SADECE JSON" in system:
            return '{"calc_type": "unsupported"}'
        return "x"
    return respond


def _status_transitions(key: str) -> list[tuple[str, str]]:
    return [(r["details"]["from"], r["details"]["to"]) for r in audit_rows("knowledge.correction.status")
            if r["details"].get("key") == key]


def test_mismatch_creates_verified_correction_and_is_retrieved(admin, member_factory, fake_llm, sm_corpus):
    # 1. the observed production mismatch, via the calculation API
    r = admin.post("/calculations", {"calc_type": "cylindrical_gear_geometry", "inputs": MANDATORY_INPUTS,
                                     "llm_draft_outputs": {"d": 60, **QWEN_DRAFT}})
    assert r.status_code == 201, r.text
    c = r.json()
    outs = {o["key"]: o["value"] for o in c["result"]["outputs"]}
    assert outs["d_b"] == pytest.approx(56.3816, abs=1e-4) and outs["p_bt"] == pytest.approx(5.9043, abs=1e-4)
    assert c["mismatch"] is True
    sm = c["comparison"]["self_maintenance"]
    assert sm["suspected_class"] == "LLM_ARITHMETIC_ERROR" and sorted(sm["fields"]) == ["d_b", "p_bt"]
    [corr] = sm["corrections"]
    assert corr["key"] == "base_circle:LLM_ARITHMETIC_ERROR"
    assert corr["status"] == "VERIFIED" and corr["regression_status"] == "passed" and corr["cases"] == 150
    assert _status_transitions(corr["key"]) == [("CANDIDATE", "TESTING"), ("TESTING", "VERIFIED")]
    assert audit_rows("knowledge.correction.created") and audit_rows("knowledge.mismatch.recorded")

    # 2. the structured mismatch event (numeric inputs only, authority + external support attached)
    [event] = [e for e in admin.get("/knowledge/mismatches").json() if e["calculation_id"] == c["id"]]
    assert event["suspected_class"] == "LLM_ARITHMETIC_ERROR" and event["disposition"] == "verified_correction"
    assert event["normalized_inputs"] == {"alpha_n": 20.0, "beta": 0.0, "h_aP_star": 1.0, "h_fP_star": 1.25,
                                          "k": 0.0, "m_n": 2.0, "x": 0.0, "z": 30.0}
    assert event["llm_values"]["d_b"] == 53.452 and event["engine_values"]["d_b"] == pytest.approx(56.3816, abs=1e-4)
    bad = {f["key"]: f for f in event["fields"] if f["status"] == "mismatch"}
    assert bad["d_b"]["rel_error"] == pytest.approx(0.0519, abs=1e-3) and bad["d_b"]["tolerance"] > 0
    assert {s["requirement_id"] for s in event["authority_sources"]} == {"iso21771.eq13", "iso21771.eq19", "iso21771.eq28"}
    assert all(s["document_id"] and s["page_number"] for s in event["authority_sources"])
    support = {s["rule_id"]: s for s in event["supporting_evidence"]}
    assert "freecad.gears" in support["gear.cyl.base_diameter"]["supporting_repositories"]
    assert event["engine_version"] == c["engine_version"] and event["correction_id"] == corr["id"]

    # 3. future Qwen drafts receive the VERIFIED correction (targeted retrieval), the engine still decides
    seen: list[str] = []
    fake_llm.responder = _draft_responder(QWEN_DRAFT, seen)
    conv = admin.post("/conversations", {}).json()["id"]
    a = admin.post(f"/conversations/{conv}/messages",
                   {"content": "z=30, m=2 mm dişli geometrisini hesapla"}).json()["assistant_message"]
    assert a["answer_mode"] == "calculation" and a["metadata"]["mismatch"] is True
    assert "Hesap motoru doğrulaması: LLM taslağında uyuşmazlık tespit edildi" in a["content"]
    assert "UYUŞMADI" in a["content"] and "LLM_ARITHMETIC_ERROR" in a["content"]
    assert "53.452" not in a["content"] and "53,452" not in a["content"]  # Qwen arithmetic never shown as a result
    assert a["metadata"]["self_maintenance"]["corrections"][0]["status"] == "VERIFIED"
    prompt = seen[-1]
    assert "DOĞRULANMIŞ DÜZELTME NOTLARI" in prompt and "d_b = d·cos α_t" in prompt
    assert "DOĞRULANMIŞ FORMÜLLER" in prompt and "p_bt = p_t·cos α_t" in prompt
    assert "freecad" not in prompt.lower() and "def " not in prompt  # no repository code in the context
    detail = admin.get(f"/messages/{a['id']}/calculation").json()
    assert "base_circle:LLM_ARITHMETIC_ERROR" in detail["llm_draft"]["knowledge_context"]["correction_keys"]
    # 4. the same general rule is reused, not re-learned
    events = [e for e in admin.get("/knowledge/mismatches").json() if e["calculation_id"] == detail["id"]]
    assert events[0]["disposition"] == "known_verified_correction"
    assert _status_transitions(corr["key"]) == [("CANDIDATE", "TESTING"), ("TESTING", "VERIFIED")]
    listed = {x["key"]: x for x in admin.get("/knowledge/corrections").json()}
    assert listed[corr["key"]]["occurrences"] >= 2

    # 5. members see only VERIFIED corrections and no mismatch events
    member = member_factory()
    assert member.get("/knowledge/mismatches").status_code == 403
    assert {x["status"] for x in member.get("/knowledge/corrections").json()} <= {"VERIFIED"}


def test_rejected_or_candidate_corrections_are_never_retrieved(admin, fake_llm, sm_corpus):
    r = admin.post("/knowledge/corrections", {
        "calc_type": "cylindrical_gear_geometry", "family": "tip_root_diameter", "root_cause": "LLM_ARITHMETIC_ERROR",
        "claims": [{"output": "d_a", "rule_id": "gear.cyl.tip_diameter", "expression": "d + 2*h_f"}],
        "statement": "YANLIŞ-ÖNERİ: d_a = d + 2·h_f", "proposed_by": "llm"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "REJECTED" and body["regression_status"] == "failed"
    assert body["regression"]["failures"][0]["check"] == "claim_vs_engine"
    seen: list[str] = []
    fake_llm.responder = _draft_responder({"d_a": 44}, seen)
    conv = admin.post("/conversations", {}).json()["id"]
    admin.post(f"/conversations/{conv}/messages", {"content": "z=20, m=2 mm dişli geometrisini hesapla"})
    assert seen and "YANLIŞ-ÖNERİ" not in seen[-1]
    # an invalid root cause is refused before anything is stored
    bad = admin.post("/knowledge/corrections", {"calc_type": "cylindrical_gear_geometry", "family": "x",
                                                "root_cause": "SELF_CERTIFIED", "claims": [{"output": "d", "expression": "d"}],
                                                "statement": "x"})
    assert bad.status_code == 422


def test_stale_verification_is_rechecked_after_engine_change(admin, sm_corpus, monkeypatch):
    from app.db.session import session_scope
    from app.services import self_maintenance
    from app.services.audit import Actor

    admin.post("/calculations", {"calc_type": "cylindrical_gear_geometry", "inputs": MANDATORY_INPUTS,
                                 "llm_draft_outputs": QWEN_DRAFT})
    monkeypatch.setattr(self_maintenance, "ENGINE_VERSION", "9.9.9-test")
    with session_scope() as db:
        assert self_maintenance.retrieve_context(db, "cylindrical_gear_geometry", ["d_b"], "targeted")["text"] == ""
        done = self_maintenance.reverify_stale(db, Actor.system())
        assert "base_circle:LLM_ARITHMETIC_ERROR" in done
        ctx = self_maintenance.retrieve_context(db, "cylindrical_gear_geometry", ["d_b", "p_bt"], "targeted")
        assert "base_circle:LLM_ARITHMETIC_ERROR" in ctx["correction_keys"]
        db.rollback()  # leave the stored verdict for the real engine version untouched


def test_self_maintenance_failure_never_breaks_calculation(admin, sm_corpus, monkeypatch):
    from app.services import self_maintenance

    def boom(*a, **k):
        raise RuntimeError("simulated")

    monkeypatch.setattr(self_maintenance, "process_mismatch", boom)
    r = admin.post("/calculations", {"calc_type": "cylindrical_gear_geometry", "inputs": MANDATORY_INPUTS,
                                     "llm_draft_outputs": QWEN_DRAFT})
    assert r.status_code == 201 and r.json()["status"] == "ok" and r.json()["mismatch"] is True
    assert r.json()["comparison"]["self_maintenance"] == {"error": "RuntimeError"}


def test_knowledge_api_provenance_and_status(admin, member_factory, sm_corpus):
    p = admin.get("/knowledge/formulas/gear.cyl.base_diameter").json()
    assert p["status"] == "VERIFIED"
    assert all(e["active_in_corpus"] and e["source"]["page_number"] for e in p["iso"]["evidence"])
    assert p["engine"][0]["file"] == "backend/app/calc/rules.py"
    assert any(o["repo"] == "cq_gears" and o["agreement"] == "SUPPORTS" for o in p["external"])
    assert admin.get("/knowledge/formulas/nope").status_code == 404
    assert len(admin.get("/knowledge/formulas", params={"status": "VERIFIED"}).json()) == 23
    assert any(c["repo"] == "cq_gears" and c["target"] == "at0" for c in admin.get("/knowledge/conflicts").json())
    refs = {r["name"]: r for r in admin.get("/knowledge/references").json()}
    assert refs["cq_gears"]["commit"].startswith("e73874cf") and refs["peft"]["inspected"] is False
    summary = admin.get("/knowledge/summary").json()
    assert summary["registry"]["by_status"]["VERIFIED"] == 23 and "corrections" in summary
    status = admin.get("/system/status").json()
    assert status["self_maintenance"]["registry"]["rules"] >= 40
    member = member_factory()
    assert member.get("/knowledge/formulas/gear.cyl.base_diameter").status_code == 200
    assert member.post("/knowledge/corrections", {}).status_code in (403, 422)
