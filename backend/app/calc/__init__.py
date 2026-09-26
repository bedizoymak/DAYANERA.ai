"""Deterministic gear calculation engine.

This package has NO dependency on the LLM or on the web/API layer. It takes
validated, unit-tagged inputs with provenance, resolves the standard
evidence each rule requires from the active verified ISO corpus (through an
injected ``EvidenceResolver``) and returns authoritative results.
"""
