"""api — FastAPI surface over the attribution database.

Read-only except for POST /scan. Nothing here recomputes the pipeline on the
request path: every figure served comes from a table some pipeline step already
wrote and audited, so the API cannot quietly disagree with `scripts/evaluate.py`.

The one rule this package exists to enforce: **an unmeasured component is never
rendered as a number.** score/attribution.py refuses to treat None as zero, the
links table stores NULL rather than 0.0 (see tests/test_persistence.py), and
api/schemas.py carries that all the way to the wire as
`{"measured": false, "reason": "..."}`. A client that renders a bare 0.00 for
the I term is asserting something this system spent Phase 3 proving false.
"""

from __future__ import annotations

__all__ = ["main"]
