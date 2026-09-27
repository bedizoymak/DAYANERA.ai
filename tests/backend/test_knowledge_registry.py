"""Unit tests: engineering knowledge registry, authority validation, provenance
and the static reference-repository scanner (no database, no LLM)."""
from __future__ import annotations

import math
import re
from dataclasses import replace
from pathlib import Path

import pytest
from app.calc.evidence import REQ
from app.knowledge import expr
from app.knowledge.registry import Registry, check, get_registry, load
from app.knowledge.scanner import discover, locate, scan
from app.knowledge.validation import engine_outputs, get_report, provenance, validate

REPO = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------- registry data
def test_registry_loads_and_is_well_formed():
    reg = load()
    assert check(reg) == []
    assert len(reg.rules) >= 40
    inspected = {r["name"]: r for r in reg.repositories if r.get("inspected")}
    for rule in reg.rules:
        for o in rule.observations:
            meta = inspected[o.repo]
            assert meta["commit"] and meta["license"], o.repo
            # every citation is anchored at a line and fingerprinted at the recorded commit
            assert o.line and o.fingerprint and len(o.fingerprint) == 16, (rule.id, o.key)
    # licences decide reuse: GPL/LGPL code is cited, never copied
    assert inspected["freecad.gears"]["license"] == "GPL-3.0"
    assert "never copied" in inspected["freecad.gears"]["reuse_policy"]


def test_registry_statuses_are_not_declared_in_the_data():
    raw = (REPO / "backend/app/knowledge/registry_data/gear_formulas.json").read_text(encoding="utf-8")
    assert '"status"' not in raw, "statuses are computed by app.knowledge.validation, never declared"


def test_expression_evaluator_is_safe():
    assert expr.evaluate("d*cos(alpha_t)", {"d": 60.0, "alpha_t": math.radians(20)}) == pytest.approx(56.38155, abs=1e-5)
    for bad in ("__import__('os')", "().__class__", "d.real", "x[0]", "(lambda: 1)()", "'a'", "open('f')",
                "cos", "[1, 2]", "a if b else c", "d == 1"):
        with pytest.raises(expr.ExpressionError):
            expr.evaluate(bad, {"d": 1.0, "x": 1.0, "a": 1.0, "b": 1.0, "c": 1.0})
    with pytest.raises(expr.ExpressionError):
        expr.evaluate("acos(2)", {})
    for deg in (5, 14.5, 20, 25, 40):
        a = math.radians(deg)
        assert expr.involute_inverse(expr.involute(a)) == pytest.approx(a, abs=1e-12)


# ---------------------------------------------------------------- authority validation
def test_verified_rules_agree_with_engine():
    report = get_report()
    verified = [v for v in report.results.values() if v.status == "VERIFIED"]
    assert len(verified) >= 23
    for v in verified:
        assert v.iso_anchor and not v.missing_evidence_ids, v.rule.id
        assert all(e in REQ for e in v.rule.evidence_ids)
        assert v.engine_agrees is True and v.engine_cases > 0, v.rule.id
        assert v.engine_max_rel_error < 1e-9, v.rule.id
        assert not v.identity_failures, (v.rule.id, v.identity_failures)
    assert not [v.rule.id for v in report.results.values() if v.status == "CONFLICT"]
    assert report.summary()["engine_checked_cases"] > 5000


def test_status_counts_and_candidates():
    report = get_report()
    by = report.summary()["by_status"]
    assert by["VERIFIED"] == 23 and by["CANDIDATE"] >= 15 and by["REJECTED"] == 1 and by["UNVERIFIED"] >= 1
    st = report.status
    assert st("gear.cyl.base_diameter") == "VERIFIED"
    assert st("gear.cyl.transverse_base_pitch") == "VERIFIED"
    assert st("gear.rack.iso53_basic_rack") == "VERIFIED"  # engine defaults == ISO 53 Table 2
    # multiple independent implementations, but no DAYANERA ISO passage: at most CANDIDATE
    assert st("gear.involute.function") == "CANDIDATE"
    assert report.results["gear.involute.function"].confidence == "multiple_independent_implementations"
    assert len(report.results["gear.involute.function"].supporting_repositories) == 4
    assert st("gear.planetary.ring_phase_cad_convention") == "UNVERIFIED"
    assert st("gear.involute.derivative_claim_freecad_gears") == "REJECTED"
    assert st("gear.cyl.normal_tooth_thickness") == "CANDIDATE"
    assert report.results["gear.cyl.normal_tooth_thickness"].confidence == "derived_from_verified_rules"
    base = report.results["gear.cyl.base_diameter"]
    assert set(base.supporting_repositories) == {"freecad.gears", "bd_warehouse", "cq_gears", "FreeCAD"}


def test_candidate_pair_rules_are_consistent_with_engine():
    report = get_report()
    for rid in ("gear.pair.working_pressure_angle_profile_shift", "gear.pair.working_centre_distance",
                "gear.pair.reference_centre_distance"):
        v = report.results[rid]
        assert v.status == "CANDIDATE" and v.consistency and v.consistency["agrees"], rid
        assert v.consistency["cases"] > 50
        assert v.confidence.endswith("+engine_consistent")


def test_external_code_never_verifies_alone():
    """Four agreeing repositories without a DAYANERA ISO anchor stay CANDIDATE; an engine
    disagreement is a CONFLICT even with an ISO anchor."""
    reg = get_registry()
    base = reg.get("gear.cyl.base_diameter")
    no_iso = replace(base, id="test.no_iso", evidence_ids=())
    wrong = replace(base, id="test.wrong", expression="d*cos(alpha_n)", identities=(), observations=())
    report = validate(Registry(rules=(no_iso, wrong), variables=reg.variables, repositories=reg.repositories,
                               conventions={}))
    assert report.status("test.no_iso") == "CANDIDATE"
    assert len(report.results["test.no_iso"].supporting_repositories) == 4
    assert report.status("test.wrong") == "CONFLICT"
    assert report.results["test.wrong"].engine_counterexample


def test_cq_gears_transverse_angle_conflict_is_recorded():
    report = get_report()
    conflicts = {c["id"]: c for c in report.conflicts()}
    c = conflicts["conflict:gear.cyl.transverse_pressure_angle:cq_gears:CrossedHelicalGear.__init__:at0"]
    assert c["rule_status"] == "VERIFIED" and c["line"] == 40 and c["commit"].startswith("e73874cf")
    assert c["permalink"] == ("https://github.com/meadiode/cq_gears/blob/e73874cf17a25447a99b1e7c22a4d5af38560e9c/"
                              "cq_gears/crossed_helical_gear.py#L40")
    # wrong even for a spur gear: arctan(0.349 rad) instead of 20°
    assert c["counterexample"]["inputs"] == {"alpha_n": 20.0, "beta": 0.0}
    assert math.degrees(c["counterexample"]["observed"]) == pytest.approx(19.2424, abs=1e-3)
    assert c["suspected_error_class"] == "DEGREE_RADIAN_ERROR"
    assert "değiştirilmedi" in c["resolution"]
    # the engine was not changed to match GitHub
    out = engine_outputs("cylindrical_gear_geometry", {"z": 30.0, "m_n": 2.0, "alpha_n": math.radians(20),
                                                       "beta": math.radians(15)})
    assert out["alpha_t"] == pytest.approx(math.degrees(math.atan(math.tan(math.radians(20)) / math.cos(math.radians(15)))))


def test_external_conflicts_are_recorded():
    report = get_report()
    ids = {c["id"] for c in report.conflicts()}
    assert len(ids) == report.summary()["conflict_records"] == 10
    assert any("CrossedHelicalGear.__init__:adn" in i for i in ids)  # addendum on m_t (normal/transverse confusion)
    assert any("arg:filletCoeff" in i for i in ids)  # FreeCAD 0.375 vs ISO 53 ρ_fP = 0.38
    planetary = next(c for c in report.conflicts() if c["rule_id"] == "gear.planetary.assembly_condition")
    assert planetary["rule_status"] == "CANDIDATE" and planetary["authority"].startswith("yok")


def test_rejected_derivative_claim():
    v = get_report().results["gear.involute.derivative_claim_freecad_gears"]
    assert v.status == "REJECTED" and v.derivative_check["agrees"] is False
    assert v.derivative_check["max_rel_error"] > 0.1
    assert [o.agreement for o in v.observations] == ["CONFLICT"]


# ---------------------------------------------------------------- provenance
def test_provenance_answers_where_a_formula_came_from():
    p = provenance("gear.cyl.base_diameter")
    assert p["status"] == "VERIFIED" and p["expression"] == "d*cos(alpha_t)"
    assert p["iso"]["citation"].startswith("ISO 21771:2007") and p["iso"]["anchored"]
    assert {e["requirement_id"] for e in p["iso"]["evidence"]} == {"iso21771.eq13", "iso21771.eq19"}
    eng = p["engine"][0]
    assert eng["file"] == "backend/app/calc/rules.py" and eng["function"].endswith("CylindricalGearGeometry.compute")
    line = (REPO / eng["file"]).read_text(encoding="utf-8").splitlines()[eng["line"] - 1]
    assert '_out("d_b"' in line
    repos = {o["repo"]: o for o in p["external"] if o["agreement"] == "SUPPORTS"}
    assert repos["freecad.gears"]["permalink"].endswith("pygears/involute_tooth.py#L119")
    assert repos["freecad.gears"]["license"] == "GPL-3.0"
    assert p["validation"]["engine_cases"] >= 270 and p["dayanera_tests"]
    assert provenance("no.such.rule") is None


def test_dayanera_test_references_exist():
    for rule in get_registry().rules:
        for ref in rule.dayanera_tests:
            path, _, name = ref.partition("::")
            text = (REPO / path).read_text(encoding="utf-8")
            assert re.search(rf"^def {re.escape(name)}\(", text, re.MULTILINE), f"{rule.id}: {ref}"


# ---------------------------------------------------------------- static scanner
def _toy_registry(tmp_repo: str) -> Registry:
    reg = get_registry()
    rule = replace(reg.get("gear.cyl.base_diameter"), observations=(
        replace(reg.get("gear.cyl.base_diameter").observations[0], repo=tmp_repo, file="gears/tooth.py",
                symbol="Tooth.factors", target="self.db", line=None, fingerprint=None),
    ))
    return Registry(rules=(rule,), variables=reg.variables,
                    repositories=({"name": tmp_repo, "inspected": True, "origin": "local", "commit": None,
                                   "license": "test"},), conventions={})


def test_scanner_locates_anchors_detects_drift_and_never_executes(tmp_path):
    root = tmp_path / "toyrepo"
    (root / "gears").mkdir(parents=True)
    marker = tmp_path / "executed.txt"
    src = (f"import pathlib\npathlib.Path({str(marker)!r}).write_text('ran')\nraise SystemExit(3)\n"
           "class Tooth:\n    def factors(self):\n        self.d = self.z * self.m\n"
           "        self.db = self.d * cos(self.alpha_t)\n        self.pitch_x = 1\n")
    (root / "gears" / "tooth.py").write_text(src, encoding="utf-8")
    reg = _toy_registry("toyrepo")
    result = scan(tmp_path, reg)
    anchor = result["anchors"][0]
    assert anchor["state"] == "ok" and anchor["line"] == 7 and anchor["fingerprint"]
    assert not marker.exists(), "the scanner must parse, never execute, untrusted code"
    repo = result["repositories"][0]
    assert repo["present"] and repo["anchors"]["ok"] == 1
    found = {d["target"] for d in result["unreviewed_discoveries"]}
    assert "self.pitch_x" in found and "self.db" not in found  # cited anchors are not "unreviewed"
    assert all("code" not in d for d in result["unreviewed_discoveries"])  # locations only, no source text

    recorded = replace(reg.rules[0].observations[0], line=7, fingerprint=anchor["fingerprint"])
    reg2 = replace(reg, rules=(replace(reg.rules[0], observations=(recorded,)),))
    (root / "gears" / "tooth.py").write_text(src.replace("cos(self.alpha_t)", "sin(self.alpha_t)"), encoding="utf-8")
    assert scan(tmp_path, reg2)["anchors"][0]["state"] == "changed"
    (root / "gears" / "tooth.py").write_text("x = 1\n\n" + src.replace("raise SystemExit(3)\n", ""), encoding="utf-8")
    assert scan(tmp_path, reg2)["anchors"][0]["state"] == "moved"
    (root / "gears" / "tooth.py").write_text("class Tooth:\n    pass\n", encoding="utf-8")
    assert scan(tmp_path, reg2)["anchors"][0]["state"] == "missing"


def test_scanner_target_forms():
    import ast

    tree = ast.parse("def f(a, clearance=0.25):\n    if a <= 1:\n        pass\n    return a * 2\n"
                     "class G:\n    ka = 1.0\n")
    assert locate(tree, "f", "arg:clearance").line == 1
    assert locate(tree, "f", "if:a <= 1").line == 2
    assert locate(tree, "f", "return").line == 4
    assert locate(tree, "G", "ka").line == 6
    assert locate(tree, "f", "missing") is None and locate(tree, "H", "ka") is None


def test_discovery_skips_vendored_folders(tmp_path):
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.py").write_text("rb = 1\n", encoding="utf-8")
    (tmp_path / "gear.py").write_text("rb = 2\n", encoding="utf-8")
    assert [d["file"] for d in discover(tmp_path)] == ["gear.py"]


# ---------------------------------------------------------------- committed artefacts
def test_validation_snapshot_is_current():
    import json

    from app.knowledge.validation import snapshot

    path = REPO / "docs" / "knowledge" / "formula_registry_validation.json"
    exported = json.loads(path.read_text(encoding="utf-8"))
    live = snapshot(get_report())
    hint = "run: python -m app.cli knowledge-validate --out ../docs/knowledge/formula_registry_validation.json"
    assert exported["registry_fingerprint"] == live["registry_fingerprint"], hint
    assert [(r["id"], r["status"]) for r in exported["rules"]] == [(r["id"], r["status"]) for r in live["rules"]], hint
    assert exported["summary"]["by_status"] == live["summary"]["by_status"]
    assert [c["id"] for c in exported["conflicts"]] == [c["id"] for c in live["conflicts"]]


def test_reference_scan_manifest_covers_every_anchor():
    import json

    manifest = json.loads((REPO / "docs" / "knowledge" / "reference_scan_manifest.json").read_text(encoding="utf-8"))
    observations = sum(len(r.observations) for r in get_registry().rules)
    assert len(manifest["anchors"]) == observations == manifest["anchor_states"]["ok"]
    assert "no import/exec" in manifest["method"]
    scanned = {r["name"]: r for r in manifest["repositories"] if r.get("present")}
    assert {"freecad.gears", "cq_gears", "FreeCAD", "bd_warehouse"} <= set(scanned)
    assert all(r["commit_matches_registry"] for r in scanned.values())
