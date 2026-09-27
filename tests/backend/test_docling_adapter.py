"""Docling adapter: DoclingDocument JSON -> DAYANERA blocks (no Docling install needed).

The payload below is a minimal, invented DoclingDocument with the item kinds seen in
the ISO pilot runs: page furniture, a number-only header followed by its title header,
a mislabelled "Figure 1." header, a table with a split subscript ("h aP"), a caption,
and a model-recognised formula.
"""
from __future__ import annotations

from app.ingestion.parsing.docling_adapter import parser_version, to_pages


def _prov(page: int) -> list[dict]:
    return [{"page_no": page, "bbox": {"l": 0, "t": 0, "r": 1, "b": 1, "coord_origin": "BOTTOMLEFT"}}]


def _payload(formulas: bool) -> dict:
    texts = [
        {"self_ref": "#/texts/0", "label": "page_header", "text": "ISO 90053:2026(E)", "prov": _prov(1)},
        {"self_ref": "#/texts/1", "label": "section_header", "text": "Scope", "prov": _prov(1)},
        {"self_ref": "#/texts/2", "label": "text", "text": "This synthetic standard specifies a rack.", "prov": _prov(1)},
        {"self_ref": "#/texts/3", "label": "section_header", "text": "3.1", "prov": _prov(1)},
        {"self_ref": "#/texts/4", "label": "section_header", "text": "synthetic rack term", "prov": _prov(1)},
        {"self_ref": "#/texts/5", "label": "section_header", "text": "Figure 1.", "prov": _prov(1)},
        {"self_ref": "#/texts/6", "label": "caption", "text": "Table 2 — Proportions", "prov": _prov(2)},
        {"self_ref": "#/texts/7", "label": "formula", "text": r"s _ { p } = \frac { \pi m } { 2 }",
         "orig": "sP = πm/2 (1)", "prov": _prov(2)},
        {"self_ref": "#/texts/8", "label": "page_footer", "text": "Not for Resale", "prov": _prov(2)},
    ]
    table = {"self_ref": "#/tables/0", "label": "table", "prov": _prov(2),
             "captions": [{"$ref": "#/texts/6"}],
             "data": {"num_rows": 2, "num_cols": 2, "table_cells": [
                 {"text": "h aP", "start_row_offset_idx": 0, "start_col_offset_idx": 0},
                 {"text": "1 m", "start_row_offset_idx": 0, "start_col_offset_idx": 1},
                 {"text": "αP", "start_row_offset_idx": 1, "start_col_offset_idx": 0},
                 {"text": "20°", "start_row_offset_idx": 1, "start_col_offset_idx": 1}]}}
    body = [{"$ref": f"#/texts/{i}"} for i in range(6)] + [{"$ref": "#/tables/0"}] + \
           [{"$ref": "#/texts/7"}, {"$ref": "#/texts/8"}]
    return {"runner": {"versions": {"docling": "2.130.0", "docling-parse": "7.22.0", "docling-ibm-models": "4.0.3"},
                       "options": {"formula_enrichment": formulas}},
            "document": {"body": {"children": body}, "groups": [], "texts": texts, "tables": [table],
                         "pages": {"1": {}, "2": {}}}}


def test_docling_items_map_to_validated_blocks():
    pages = to_pages(_payload(formulas=False), lexicon={"haP", "αP"})
    p1 = [(b.kind, b.clause, b.text) for b in pages[1]]
    assert ("heading", "1", "1 Scope") in p1
    assert ("heading", "3.1", "3.1 synthetic rack term") in p1  # number-only header + title header
    assert ("text", None, "Figure 1.") in p1  # implausible header proposal demoted to text
    assert all("ISO 90053" not in t for _k, _c, t in p1)  # page furniture skipped
    table = next(b for b in pages[2] if b.kind == "table")
    assert table.label == "Table 2" and "| haP | 1 m |" in table.text and "| αP | 20° |" in table.text
    formula = next(b for b in pages[2] if b.kind == "formula")
    assert formula.label == "(1)" and formula.source == "docling:formula"
    assert not any("Resale" in b.text for b in pages[2])


def test_model_recognised_formulas_are_marked_as_model_output():
    pages = to_pages(_payload(formulas=True))
    formula = next(b for b in pages[2] if b.kind == "formula")
    assert formula.source == "docling:formula_model"  # -> draft_extraction chunk, needs review
    assert "formula-enrichment" in parser_version(_payload(formulas=True))
