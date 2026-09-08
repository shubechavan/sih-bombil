"""Extraction layer — normalisation, identifiers, PGP, GLiNER.

Nothing here touches the network or the database. These modules take text and
return structured values; `scripts/ingest.py` is what writes them down.

`identifiers` deliberately does not import `gliner_extract`: pulling in
legacy/llm.py costs ~16 seconds of torch import, and the regex path is the one
that must always work.
"""
