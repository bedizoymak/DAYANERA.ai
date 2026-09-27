"""Parser layer: turns parser output (PyMuPDF text layer, Docling documents)
into DAYANERA's canonical page/block model (``extractors.base``).

Parsers are replaceable implementation layers; DAYANERA stays the system of
record for documents, versions, pages, chunks, provenance and verification.
"""
