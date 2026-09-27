"""Standalone Docling runner (executed by the Docling interpreter, NOT the backend).

Docling pulls in PyTorch and layout/table models, so it lives in its own virtual
environment (``DOCLING_PYTHON``) and runs in a subprocess. This file must only
import the standard library and docling. It writes one JSON file:

    {"runner": {versions, options, timings}, "document": <DoclingDocument dict>}

Usage: python docling_runner.py <input.pdf> <output.json> [--ocr] [--formulas] [--fast-tables]
Remote services stay disabled: nothing leaves the machine except the one-time
model download performed by Docling itself when its artifacts are not cached.
"""
from __future__ import annotations

import argparse
import importlib.metadata as md
import json
import sys
import time


def _versions() -> dict:
    out = {}
    for pkg in ("docling", "docling-core", "docling-parse", "docling-ibm-models", "torch"):
        try:
            out[pkg] = md.version(pkg)
        except md.PackageNotFoundError:
            out[pkg] = None
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--ocr", action="store_true", help="OCR bitmap regions (scanned pages)")
    ap.add_argument("--formulas", action="store_true", help="formula enrichment (LaTeX) with the CodeFormula model")
    ap.add_argument("--fast-tables", action="store_true", help="TableFormer FAST mode instead of ACCURATE")
    ap.add_argument("--artifacts", default=None, help="local model artifacts folder (offline operation)")
    args = ap.parse_args(argv)

    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
    from docling.document_converter import DocumentConverter, PdfFormatOption

    opts = PdfPipelineOptions()
    opts.enable_remote_services = False
    opts.do_ocr = bool(args.ocr)
    opts.do_table_structure = True
    opts.table_structure_options.do_cell_matching = True
    opts.table_structure_options.mode = TableFormerMode.FAST if args.fast_tables else TableFormerMode.ACCURATE
    opts.do_formula_enrichment = bool(args.formulas)
    if args.artifacts:
        opts.artifacts_path = args.artifacts

    t0 = time.perf_counter()
    conv = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})
    res = conv.convert(args.input)
    seconds = round(time.perf_counter() - t0, 2)
    payload = {
        "runner": {
            "versions": _versions(),
            "options": {"ocr": opts.do_ocr, "formula_enrichment": opts.do_formula_enrichment,
                        "table_mode": opts.table_structure_options.mode.value, "remote_services": False},
            "status": str(getattr(res, "status", "")),
            "errors": [str(e) for e in (getattr(res, "errors", None) or [])],
            "seconds": seconds,
        },
        "document": res.document.export_to_dict(),
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
