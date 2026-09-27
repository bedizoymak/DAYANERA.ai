"""Calibrate ``app/ingestion/tokens.py`` against the real Qwen tokenizer of the local Ollama.

    python -m app.evaluation.token_calibration [--samples 240] [--model qwen3:14b-q4_K_M]

Takes chunk texts from the last benchmark dump (DATA_ROOT/eval/chunking/new.json; stratified by
content type: formulas, tables, prose, ...), asks the LOCAL Ollama for the exact token count
(``prompt_eval_count`` of a raw prompt with ``num_predict: 1``; nothing leaves the machine),
fits the linear estimator by least squares and writes the coefficients and error statistics
(numbers only) to docs/chunking/token_calibration.json. Update ``tokens.COEF`` with the printed
values only when the error statistics are better than the current ones.
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import urllib.request

import numpy as np

from app.core.config import REPO_ROOT, get_settings
from app.ingestion import tokens

FEATURES = ["word", "long_word_chars", "digit", "non_ascii", "punct", "newline"]


def qwen_count(base_url: str, model: str, text: str) -> int:
    body = json.dumps({"model": model, "prompt": text, "raw": True, "stream": False,
                       "options": {"num_predict": 1, "temperature": 0}}).encode("utf-8")
    req = urllib.request.Request(f"{base_url}/api/generate", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return int(json.loads(r.read())["prompt_eval_count"])


def main(argv: list[str] | None = None) -> int:
    s = get_settings()
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=int, default=240)
    ap.add_argument("--model", default=s.ollama_model)
    a = ap.parse_args(argv)
    dump = json.loads((s.data_root / "eval" / "chunking" / "new.json").read_text(encoding="utf-8"))
    by_type: dict[str, list[str]] = {}
    for c in dump["chunks"]:
        if 20 <= len(c["text"]) <= 4000:
            by_type.setdefault(c["content_type"], []).append(c["text"])
    rnd = random.Random(7)
    per = max(8, a.samples // max(1, len(by_type)))
    sample = [t for texts in by_type.values() for t in rnd.sample(texts, min(per, len(texts)))][:a.samples]
    X, y = [], []
    for t in sample:
        y.append(qwen_count(s.ollama_base_url, a.model, t))
        f = tokens.features(t)
        X.append([1.0] + [f[k] for k in FEATURES])
    X_, y_ = np.array(X), np.array(y, dtype=float)
    coef, *_ = np.linalg.lstsq(X_, y_, rcond=None)

    def errors(pred) -> dict:
        rel = [(p - t) / t for p, t in zip(pred, y) if t]
        return {"mean_abs_pct": round(100 * statistics.mean(abs(r) for r in rel), 2),
                "p95_abs_pct": round(100 * sorted(abs(r) for r in rel)[int(0.95 * (len(rel) - 1))], 2),
                "underestimate_share": round(sum(r < 0 for r in rel) / len(rel), 3)}

    current = [tokens.estimate_tokens(t) for t in sample]
    fitted = [max(1.0, float(np.dot(coef, x))) for x in X]
    chars4 = [len(t) / 4 for t in sample]
    out = {"model": a.model, "samples": len(sample), "content_types": {k: min(per, len(v)) for k, v in by_type.items()},
           "fitted": {"intercept": round(float(coef[0]), 3), **{k: round(float(c), 3) for k, c in zip(FEATURES, coef[1:])}},
           "error_current_estimator": errors(current), "error_fitted": errors(fitted),
           "error_chars_div_4": errors(chars4),
           "tokens_per_char_by_type": {k: round(statistics.mean(y[i] / len(sample[i]) for i in range(len(sample))
                                                                if sample[i] in v), 3)
                                       for k, v in by_type.items() if any(t in v for t in sample)}}
    path = REPO_ROOT / "docs" / "chunking" / "token_calibration.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
