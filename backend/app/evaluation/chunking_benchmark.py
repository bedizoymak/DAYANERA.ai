"""Old chunker vs engineering chunker: measured on the real corpus, end to end.

    python -m app.evaluation.chunking_benchmark run [--baseline b69692f] [--docs 2.pdf,6.pdf,...]
    python -m app.evaluation.chunking_benchmark report        # recompute metrics from the last dumps

``run`` checks out the baseline commit (the pipeline before this change) as a ``git worktree``
under DATA_ROOT/eval/chunking/legacy-tree and runs ``chunking_bench_driver.py`` twice - once with
the baseline tree, once with the current tree - each in its own throwaway database. Both runs
use the same documents, the same OCR cache and the same query set; all metrics below are
computed by the same code from the two dumps:

  chunks          retrievable chunks (parents are context windows, not counted), tokens
                  (same Qwen estimator for both), tiny chunks, page-spanning chunks
  formulas        equation + its number in one chunk; + heading/clause + lead-in in the SAME chunk
                  ("complete unit"); normalised form equal to the canonical form; LaTeX available
  tables          ground-truth rows found on one line; + caption and header in the same chunk
  headings        chunks with clause + heading; chunks with the full heading path
  provenance      page range present; verbatim lines traced to the cited pages
  retrieval       Hit@1, Hit@4, Recall@4, MRR, answer grounding, negatives abstained
  citation        top-1 passage cites the right standard and page
  formula context the model's context holds the equation number AND its lead-in/definitions

Outputs: DATA_ROOT/eval/chunking/{old,new}.json (dumps, local only - they contain document text)
and docs/chunking/benchmark.{json,md} (numbers only, safe to commit).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
from pathlib import Path

from app.core.config import REPO_ROOT, get_settings
from app.evaluation.chunking_ground_truth import CANONICAL, FORMULAS, QUERIES, TABLE_ROWS
from app.ingestion.quality import is_traced
from app.ingestion.tokens import estimate_tokens

BASELINE = "b69692f"  # last commit before the engineering chunker (self-maintenance work)
DOCS = "1.pdf,2.pdf,3.pdf,4.pdf,5.pdf,6.pdf,7.pdf,8.pdf,9.pdf,10.pdf,11.pdf,12.pdf"
DOCS_OUT = REPO_ROOT / "docs" / "chunking"


# --------------------------------------------------------------------------- orchestration
def _env() -> dict:
    env = dict(os.environ)
    p = REPO_ROOT / ".env"
    if p.exists():  # secrets are passed through the environment, never written anywhere
        for line in p.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip())
    env["PYTHONIOENCODING"] = "utf-8"
    return env


SWEEP = {  # chunk-size experiment, calibrated Qwen tokens; "current" is ChunkingConfig's default
    "tiny": {"soft_target": 110, "hard_max": 240, "leaf_section_max": 190, "parent_max": 480, "table_leaf_max": 190,
             "table_group_target": 130},
    "current": {},
    "medium": {"soft_target": 250, "hard_max": 520, "leaf_section_max": 440, "parent_max": 1030, "table_leaf_max": 440,
               "table_group_target": 300},
    "large": {"soft_target": 390, "hard_max": 750, "leaf_section_max": 640, "parent_max": 1500, "table_leaf_max": 640,
              "table_group_target": 440},
    "no_section_leaf": {"leaf_section_max": 0},
}


def sweep(docs: str) -> dict:
    """Ingest the corpus once per size configuration (new tree only) and compare chunk + retrieval metrics."""
    s = get_settings()
    root = s.data_root / "eval" / "chunking"
    root.mkdir(parents=True, exist_ok=True)
    qfile = root / "queries.json"
    qfile.write_text(json.dumps(QUERIES, ensure_ascii=False), encoding="utf-8")
    driver = Path(__file__).with_name("chunking_bench_driver.py")
    results = {}
    for name, cfg in SWEEP.items():
        env = _env()
        env.update(PYTHONPATH=str(REPO_ROOT / "backend"), PROJECT_ROOT=str(REPO_ROOT),
                   DAYANERA_CHUNKING_CONFIG=json.dumps(cfg))
        out = root / f"sweep-{name}.json"
        subprocess.run([sys.executable, str(driver), "--variant", f"sweep_{name}", "--run-root",
                        str(root / f"run-sweep-{name}"), "--docs", docs, "--source", str(s.iso_booklets_path),
                        "--ocr-cache", str(s.data_root / "indexes" / "ocr-cache"), "--queries", str(qfile),
                        "--out", str(out)], cwd=str(REPO_ROOT / "backend"), env=env, check=True)
        dump = json.loads(out.read_text(encoding="utf-8"))
        cm, rm = chunk_metrics(dump), retrieval_metrics(dump)
        ctx = [len(r.get("context") or []) for r in dump["queries"] if r.get("context")]
        results[name] = {"config": cfg, **{k: cm[k] for k in ("chunks_retrievable", "chunks_parent", "tokens_median",
                                                            "tokens_p95", "tokens_max", "tiny_chunks_lt25",
                                                            "formula_complete_unit")},
                         "passages_in_context_avg": round(statistics.mean(ctx), 2) if ctx else 0,
                         **{f"all_{k}": rm["aggregate"][k] for k in ("hit@1", "mrr", "answer_grounding",
                                                                    "formula_context_complete")},
                         **{f"heldout_{k}": rm["heldout"][k] for k in ("hit@1", "mrr", "answer_grounding")}}
    DOCS_OUT.mkdir(parents=True, exist_ok=True)
    (DOCS_OUT / "size_sweep.json").write_text(json.dumps(results, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    keys = list(next(iter(results.values())).keys())[1:]
    md = ["# Chunk size experiment", "", "| metric | " + " | ".join(results) + " |", "|---|" + "---|" * len(results)]
    for k in keys:
        md.append(f"| {k} | " + " | ".join(str(results[n][k]) for n in results) + " |")
    md.append("")
    md.append("Configurations: " + "; ".join(f"{n} = {c or 'ChunkingConfig defaults'}" for n, c in SWEEP.items()))
    (DOCS_OUT / "size_sweep.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))
    return results


def run(baseline: str, docs: str, skip_old: bool = False, skip_new: bool = False) -> None:
    s = get_settings()
    root = s.data_root / "eval" / "chunking"
    root.mkdir(parents=True, exist_ok=True)
    qfile = root / "queries.json"
    qfile.write_text(json.dumps(QUERIES, ensure_ascii=False), encoding="utf-8")
    driver = Path(__file__).with_name("chunking_bench_driver.py")
    trees = []
    if not skip_old:
        tree = root / "legacy-tree"
        if not tree.exists():
            subprocess.run(["git", "worktree", "add", "--detach", str(tree), baseline], cwd=REPO_ROOT, check=True)
        trees.append(("old", tree / "backend"))
    if not skip_new:
        trees.append(("new", REPO_ROOT / "backend"))
    for variant, backend in trees:
        env = _env()
        env["PYTHONPATH"] = str(backend)
        env["PROJECT_ROOT"] = str(REPO_ROOT)
        cmd = [sys.executable, str(driver), "--variant", variant, "--run-root", str(root / f"run-{variant}"),
               "--docs", docs, "--source", str(s.iso_booklets_path),
               "--ocr-cache", str(s.data_root / "indexes" / "ocr-cache"), "--queries", str(qfile),
               "--out", str(root / f"{variant}.json")]
        subprocess.run(cmd, cwd=str(backend), env=env, check=True)


# --------------------------------------------------------------------------- metrics
def _canon(s: str) -> str:
    s = s.replace("·", "").replace("⋅", "").replace("²", "^2").replace("[", "(").replace("]", ")")
    return re.sub(r"[\s()]", "", s)


def _retrievable(chunks: list[dict]) -> list[dict]:
    return [c for c in chunks if (c.get("chunk_role") or "leaf") != "parent"]


def _covers(c: dict, std: str, page: int) -> bool:
    p0 = c.get("page_start") or c.get("page_number")
    p1 = c.get("page_end") or p0
    return (c.get("doc_code") or c.get("standard_code") or "").startswith(std) and p0 is not None and p0 <= page <= p1


def _formula_line(c: dict, num: str) -> str | None:
    for f in c.get("formula") or []:
        if f.get("number") == num:
            return f.get("plain")
    for ln in (c.get("text") or "").splitlines():
        if re.search(r"\(" + re.escape(num) + r"\)\s*$", ln):
            return re.sub(r"\s*\(" + re.escape(num) + r"\)\s*$", "", ln)
    return None


def chunk_metrics(dump: dict) -> dict:
    chunks = _retrievable(dump["chunks"])
    toks = sorted(estimate_tokens(c["text"]) for c in chunks)
    pages = {(p["doc_code"], p["page_number"]): p["text"] for p in dump["pages"]}
    squash = {k: re.sub(r"\s+", "", v) for k, v in pages.items()}
    structured = [c for c in chunks if c.get("extraction_method") in ("native_text", "native_text_rebuilt")]

    def depth(c: dict) -> int:
        cl = c.get("clause") or ""
        return cl.count(".") + 1 if re.match(r"^\d", cl) else (cl.count(".") + 1 if cl else 0)

    full_path = [c for c in structured if c.get("clause") and len(c.get("heading_path") or []) >= depth(c)]
    traced = 0
    for c in chunks:
        p0 = c.get("page_start") or c.get("page_number")
        p1 = c.get("page_end") or p0
        span = "".join(squash.get((c["doc_code"], n), "") for n in range(p0, p1 + 1))
        verbatim = "\n".join(ln for ln in c["text"].splitlines() if not ln.startswith("|")
                             and not re.search(r"\((?:[A-Z]\.)?\d{1,3}[a-z]?\)\s*$", ln))
        traced += is_traced(verbatim, span)
    # formulas
    f_found = f_unit = f_exact = f_latex = 0
    per_formula = []
    for std, num, page, lead, clause in FORMULAS:
        cands = [c for c in chunks if (_covers(c, std, page) or _covers(c, std, page - 1) or _covers(c, std, page + 1))
                 and f"({num})" in c["text"]]
        found = bool(cands)
        unit = any((clause is None or c.get("clause") == clause or clause in (c.get("text") or "")
                    or any(clause in h for h in c.get("heading_path") or []))
                   and (lead is None or re.search(lead, c["text"])) for c in cands)
        forms = [_formula_line(c, num) for c in cands]
        canon = CANONICAL.get((std, num))
        exact = bool(canon and any(f and _canon(f) == _canon(canon) for f in forms))
        latex = any(f.get("latex") for c in cands for f in c.get("formula") or [] if f.get("number") == num)
        f_found += found
        f_unit += unit
        f_exact += exact
        f_latex += latex
        per_formula.append({"formula": f"{std} ({num})", "found": found, "complete_unit": unit, "exact": exact,
                            "latex": latex, "canonical_known": bool(canon)})
    n_canon = sum(1 for std, num, *_ in FORMULAS if (std, num) in CANONICAL)
    # tables
    t_row = t_head = 0
    for std, label, page, cells, header in TABLE_ROWS:
        rx = re.compile(r".*?".join(cells))
        cands = [c for c in chunks if any(_covers(c, std, pg) for pg in (page, page + 1, page + 2, page + 3))
                 and any(rx.search(ln) for ln in c["text"].splitlines())]
        t_row += bool(cands)
        label_ok = (lambda c: label.split()[0] != "Table" or label in c["text"])
        t_head += any(label_ok(c) and all(h.lower() in c["text"].lower() for h in header) for c in cands)
    n = max(1, len(chunks))
    return {
        "chunks_retrievable": len(chunks), "chunks_parent": len(dump["chunks"]) - len(chunks),
        "tokens_avg": round(statistics.mean(toks), 1) if toks else 0, "tokens_median": statistics.median(toks) if toks else 0,
        "tokens_p95": toks[int(0.95 * (len(toks) - 1))] if toks else 0, "tokens_max": toks[-1] if toks else 0,
        "tiny_chunks_lt25": round(sum(t < 25 for t in toks) / n, 4),
        "page_spanning_chunks": round(sum(1 for c in chunks if (c.get("page_end") or 0) > (c.get("page_start") or 0)) / n, 4),
        "formula_found": f"{f_found}/{len(FORMULAS)}", "formula_complete_unit": f"{f_unit}/{len(FORMULAS)}",
        "formula_exact_form": f"{f_exact}/{n_canon}", "formula_latex": f"{f_latex}/{len(FORMULAS)}",
        "table_rows_found": f"{t_row}/{len(TABLE_ROWS)}", "table_rows_with_header": f"{t_head}/{len(TABLE_ROWS)}",
        "heading_present": round(sum(1 for c in structured if c.get("clause") and c.get("heading")) / max(1, len(structured)), 4),
        "heading_full_path": round(len(full_path) / max(1, len(structured)), 4),
        "page_provenance": round(sum(1 for c in chunks if c.get("page_start") and c.get("page_end")) / n, 4),
        "verbatim_traced": round(traced / n, 4),
        "per_formula": per_formula,
    }


def retrieval_metrics(dump: dict, k: int = 4) -> dict:
    by_id = {q["id"]: q for q in QUERIES}
    rows = []
    for r in dump["queries"]:
        q = by_id[r["id"]]
        units = q["relevant"]
        top = r.get(f"top{k}") or []

        def rel(p: dict, u: tuple) -> bool:
            # a passage is judged with the heading lineage it carries (old: heading column; new: heading path)
            std, page, rx = u
            body = "\n".join([p.get("heading") or ""] + list(p.get("heading_path") or []) + [p["text"]])
            return (p["standard_code"] or "").startswith(std) and p["page"] <= page <= (p.get("page_end") or p["page"]) \
                and re.search(rx, body) is not None

        hits = [any(rel(p, u) for u in units) for p in top]
        ctx = "\n".join(r.get("context") or [])
        row = {"id": q["id"], "kind": q.get("kind"), "set": q.get("set", "tuning")}
        if units:
            first = next((i for i, h in enumerate(hits, start=1) if h), None)
            row.update(hit1=bool(hits[:1] and hits[0]), hit4=any(hits), mrr=round(1 / first, 4) if first else 0.0,
                       recall4=round(sum(1 for u in units if any(rel(p, u) for p in top)) / len(units), 4),
                       grounded=bool(r.get("passes_gate")) and all(re.search(rx, ctx) for rx in q["answer"]))
            t1 = top[0] if top else None
            row["citation_ok"] = bool(t1) and any((t1["standard_code"] or "").startswith(std)
                                                  and t1["page"] <= page <= (t1.get("page_end") or t1["page"])
                                                  for std, page, _ in units)
            if q.get("formula"):
                std, num = q["formula"]
                lead = next((f[3] for f in FORMULAS if f[0] == std and f[1] == num), None)
                row["formula_context_complete"] = f"({num})" in ctx and (lead is None or re.search(lead, ctx) is not None)
        else:
            row["abstained"] = not r.get("passes_gate")
        rows.append(row)
    def mean(key: str, items: list[dict]) -> float:
        return round(statistics.mean(float(x[key]) for x in items), 4) if items else 0.0

    out = {"queries": rows}
    for name, subset in (("aggregate", rows), ("tuning", [x for x in rows if x["set"] == "tuning"]),
                         ("heldout", [x for x in rows if x["set"] == "heldout"])):
        out[name] = _aggregate(subset, mean)
    return out


def _aggregate(rows: list[dict], mean) -> dict:
    pos = [x for x in rows if "hit1" in x]
    neg = [x for x in rows if "abstained" in x]
    form = [x for x in pos if "formula_context_complete" in x]
    agg = {"queries": len(rows), "positives": len(pos), "hit@1": mean("hit1", pos), "hit@4": mean("hit4", pos),
           "recall@4": mean("recall4", pos), "mrr": mean("mrr", pos), "answer_grounding": mean("grounded", pos),
           "citation_correct@1": mean("citation_ok", pos),
           "formula_context_complete": mean("formula_context_complete", form),
           "negatives_abstained": f"{sum(x['abstained'] for x in neg)}/{len(neg)}"}
    for kind in ("formula", "table", "definition", "text"):
        sub = [x for x in pos if x["kind"] == kind]
        if sub:
            agg[f"hit@1[{kind}]"] = mean("hit1", sub)
    return agg


def verify_ground_truth(dump: dict) -> list[str]:
    """Every (standard, page, regex) evidence unit must exist in the page text layer (tree-independent)."""
    pages = {}
    for p in dump["pages"]:
        pages[(p["doc_code"] or "", p["page_number"])] = p["text"]
    miss = []
    for q in QUERIES:
        for std, page, rx in q["relevant"]:
            text = next((t for (code, n), t in pages.items() if code.startswith(std) and n == page), None)
            if text is None or not re.search(rx, text):
                miss.append(f"{q['id']}: {std} s.{page} /{rx}/")
    return miss


def report() -> dict:
    s = get_settings()
    root = s.data_root / "eval" / "chunking"
    out: dict = {"baseline_commit": BASELINE, "variants": {}}
    for variant in ("old", "new"):
        f = root / f"{variant}.json"
        if not f.exists():
            continue
        dump = json.loads(f.read_text(encoding="utf-8"))
        per_doc = {}
        for code in sorted({c["doc_code"] for c in dump["chunks"] if c.get("doc_code")}):
            sub = {"chunks": [c for c in dump["chunks"] if c["doc_code"] == code],
                   "pages": [p for p in dump["pages"] if p["doc_code"] == code]}
            m = chunk_metrics(sub)
            per_doc[code] = {k: m[k] for k in ("chunks_retrievable", "tokens_median", "tokens_max", "heading_full_path",
                                               "page_spanning_chunks")}
        out["variants"][variant] = {"ingest_seconds": dump.get("ingest_seconds"), "chunks": chunk_metrics(dump),
                                    "retrieval": retrieval_metrics(dump), "per_document": per_doc,
                                    "ground_truth_misses": verify_ground_truth(dump)}
    DOCS_OUT.mkdir(parents=True, exist_ok=True)
    (DOCS_OUT / "benchmark.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (DOCS_OUT / "benchmark.md").write_text(render_md(out), encoding="utf-8")
    print(render_md(out))
    return out


_CHUNK_ROWS = [("chunks_retrievable", "retrievable chunks"), ("chunks_parent", "parent (context) chunks"),
               ("tokens_avg", "tokens avg"), ("tokens_median", "tokens median"), ("tokens_p95", "tokens p95"),
               ("tokens_max", "tokens max"), ("tiny_chunks_lt25", "tiny chunks (<25 tokens)"),
               ("page_spanning_chunks", "page-spanning chunks"), ("formula_found", "formula + number in one chunk"),
               ("formula_complete_unit", "formula complete unit (number+clause+lead-in)"),
               ("formula_exact_form", "formula exact normalised form"), ("formula_latex", "formula LaTeX available"),
               ("table_rows_found", "table rows preserved"), ("table_rows_with_header", "table rows with caption+header"),
               ("heading_present", "clause + heading present"), ("heading_full_path", "full heading path"),
               ("page_provenance", "page range present"), ("verbatim_traced", "verbatim text traced to cited pages")]
_RET_ROWS = ["hit@1", "hit@4", "recall@4", "mrr", "answer_grounding", "citation_correct@1", "formula_context_complete",
             "negatives_abstained", "hit@1[formula]", "hit@1[table]", "hit@1[definition]", "hit@1[text]"]


def render_md(out: dict) -> str:
    v = out["variants"]
    old, new = v.get("old", {}), v.get("new", {})
    lines = [f"# Chunking benchmark (baseline {out['baseline_commit']} vs engineering chunker)", "",
             "Generated by `python -m app.evaluation.chunking_benchmark report`. Numbers only; no document text.", "",
             "| metric | old chunker | new engineering chunker |", "|---|---|---|"]
    for key, label in _CHUNK_ROWS:
        lines.append(f"| {label} | {old.get('chunks', {}).get(key, '–')} | {new.get('chunks', {}).get(key, '–')} |")
    for key in _RET_ROWS:
        lines.append(f"| retrieval {key} | {old.get('retrieval', {}).get('aggregate', {}).get(key, '–')} | "
                     f"{new.get('retrieval', {}).get('aggregate', {}).get(key, '–')} |")
    for subset in ("tuning", "heldout"):
        for key in ("queries", "hit@1", "hit@4", "mrr", "answer_grounding", "citation_correct@1",
                    "formula_context_complete", "negatives_abstained"):
            lines.append(f"| {subset}: {key} | {old.get('retrieval', {}).get(subset, {}).get(key, '–')} | "
                         f"{new.get('retrieval', {}).get(subset, {}).get(key, '–')} |")
    lines.append(f"| ingest seconds (12 documents) | {old.get('ingest_seconds', '–')} | {new.get('ingest_seconds', '–')} |")
    lines += ["", "## Per formula (new)", "", "| formula | found | complete unit | exact | LaTeX |", "|---|---|---|---|---|"]
    for f in new.get("chunks", {}).get("per_formula", []):
        lines.append(f"| {f['formula']} | {f['found']} | {f['complete_unit']} | {f['exact'] if f['canonical_known'] else '–'} "
                     f"| {f['latex']} |")
    lines += ["", "## Per query", "", "| query | kind | old hit@1 | new hit@1 | old grounded | new grounded |",
              "|---|---|---|---|---|---|"]
    oq = {r["id"]: r for r in old.get("retrieval", {}).get("queries", [])}
    for r in new.get("retrieval", {}).get("queries", []):
        o = oq.get(r["id"], {})
        if "hit1" in r:
            lines.append(f"| {r['id']}{' (held-out)' if r['set'] == 'heldout' else ''} | {r['kind']} | "
                         f"{o.get('hit1', '–')} | {r['hit1']} | {o.get('grounded', '–')} | {r['grounded']} |")
        else:
            lines.append(f"| {r['id']} | negative | abstained={o.get('abstained', '–')} | abstained={r['abstained']} | | |")
    miss = new.get("ground_truth_misses") or []
    lines += ["", f"Ground-truth evidence units not found in the page text layer: {len(miss)}"] + [f"- {m}" for m in miss]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="app.evaluation.chunking_benchmark")
    ap.add_argument("command", choices=["run", "report", "sweep"])
    ap.add_argument("--baseline", default=BASELINE)
    ap.add_argument("--docs", default=DOCS)
    ap.add_argument("--skip-old", action="store_true")
    ap.add_argument("--skip-new", action="store_true")
    a = ap.parse_args(argv)
    if a.command == "sweep":
        sweep(a.docs)
        return 0
    if a.command == "run":
        run(a.baseline, a.docs, a.skip_old, a.skip_new)
    report()
    return 0


if __name__ == "__main__":
    sys.exit(main())
