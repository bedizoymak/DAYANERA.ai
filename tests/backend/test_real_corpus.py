"""Integration with the REAL local ISO corpus (skipped when absent).

The licensed PDFs are copied from ``iso booklets`` into the temporary test
project root only; nothing is written to the real corpus or database.
"""
from __future__ import annotations

import shutil
import time

import pytest

from app.core.paths import fs
from conftest import REPO

REAL = REPO / "iso booklets"
WANTED = {
    "ISO 53, 2, 1998": "ISO 53:1998",
    "ISO 21771, 1, 2007": "ISO 21771:2007",
    "ISO system of flank tolerance": "ISO 1328-1:2013",
    "Geometrical product specifications (GPS)_ ISO code system": "ISO 286-1:2010",
    "Surface temper etch": "ISO 14104:2017",
}

pytestmark = pytest.mark.corpus


@pytest.fixture(scope="module")
def real_corpus(settings, admin):
    if not REAL.exists():
        pytest.skip("Gerçek ISO korpusu yok")
    dest = settings.iso_booklets_path / "real"
    dest.mkdir(exist_ok=True)
    copied = 0
    for p in REAL.iterdir():
        for key in WANTED:
            if key in p.name:
                shutil.copyfile(fs(p), fs(dest / p.name))
                copied += 1
    if copied < len(WANTED):
        pytest.skip("Gerekli ISO PDF'lerinden bazıları eksik")
    from app.ingestion.jobs import run_pending
    from app.ingestion.watcher import Watcher

    time.sleep(2.1)
    Watcher(settings).scan()
    run_pending(settings)
    yield {d["standard_code"]: d for d in admin.get("/documents?limit=500").json()["items"] if d["standard_code"]}


def test_real_pdfs_are_indexed_with_detected_standard_codes(real_corpus):
    for code in WANTED.values():
        assert code in real_corpus, f"{code} algılanamadı"
        v = real_corpus[code]["current_version"]
        assert v["ingestion_status"] == "indexed" and real_corpus[code]["status"] == "active"
        assert v["extraction_summary"]["counts"]["verified_pages"] > 0
    # glyph-positioned PDF text layer is rebuilt into words
    assert real_corpus["ISO 14104:2017"]["current_version"]["extraction_summary"]["methods"].get("native_text_rebuilt")


def test_every_evidence_requirement_resolves_on_real_corpus(admin, real_corpus):
    types = admin.get("/calculations/types").json()
    missing = {t["calc_type"]: [k for k, ok in t["evidence_available"].items() if not ok] for t in types}
    assert all(not v for v in missing.values()), missing


def test_real_iso286_table_lookup_matches_standard_example(admin, real_corpus):
    # ISO 286-1:2010 gives the example "IT6 in the range above 30 mm up to and including 50 mm IT6 = 16 µm"
    r = admin.post("/calculations", {"calc_type": "iso286_it_tolerance",
                                     "inputs": {"nominal_size": {"value": 40, "unit": "mm"}, "grade": {"value": 6}}}).json()
    assert r["status"] == "ok", r["result"]["diagnostics"]
    assert r["result"]["outputs"][0]["value"] == pytest.approx(16.0)
    assert r["result"]["evidence"][0]["page_number"] == 26


def test_real_gear_geometry_is_grounded_in_iso21771_and_iso53(admin, real_corpus):
    r = admin.post("/calculations", {"calc_type": "cylindrical_gear_geometry",
                                     "inputs": {"z": {"value": 20}, "m_n": {"value": 2, "unit": "mm"}}}).json()
    assert r["status"] == "ok"
    pages = {(e["standard_code"], e["page_number"]) for e in r["result"]["evidence"]}
    assert ("ISO 21771:2007", 18) in pages and ("ISO 53:1998", 5) in pages


def test_real_retrieval_finds_basic_rack_pressure_angle(admin, real_corpus):
    r = admin.post("/retrieval/search", {"query": "ISO 53 standart temel kremayer profilinde basınç açısı nedir?"}).json()
    assert r["passages"] and all(p["standard_code"] == "ISO 53:1998" for p in r["passages"])
    assert any("pressure angle" in p["excerpt"].lower() for p in r["passages"])
