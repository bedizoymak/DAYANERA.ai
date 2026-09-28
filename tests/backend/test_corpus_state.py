"""Regression tests for the distinction between indexed and owner-approved corpus data."""
from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    ("indexed_documents", "indexed_chunks", "approved_versions", "eligible_chunks", "expected"),
    [
        (0, 0, 0, 0, "EMPTY"),
        (12, 4045, 0, 0, "INDEXED_UNAPPROVED"),
        (12, 4045, 1, 3411, "READY"),
        (12, 4045, 0, 3411, "INDEXED_UNAPPROVED"),
        (12, 4045, 1, 0, "INDEXED_UNAPPROVED"),
    ],
)
def test_corpus_state_keeps_indexing_review_and_approval_distinct(
    indexed_documents, indexed_chunks, approved_versions, eligible_chunks, expected,
):
    from app.services.system_status import _corpus_state

    assert _corpus_state(indexed_documents, indexed_chunks, approved_versions, eligible_chunks).value == expected


def test_inactive_or_obsolete_versions_cannot_make_state_ready():
    """Only current, active, indexed versions may contribute the READY metrics."""
    from app.services.system_status import _corpus_state

    # An old verified version is deliberately absent from the approved/eligible
    # aggregates that the production SQL computes.
    assert _corpus_state(indexed_documents=0, indexed_chunks=0, approved_versions=0, eligible_chunks=0).value == "EMPTY"
    assert _corpus_state(indexed_documents=1, indexed_chunks=10, approved_versions=0, eligible_chunks=0).value == "INDEXED_UNAPPROVED"
