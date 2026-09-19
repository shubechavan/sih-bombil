"""export — case reports and result-set downloads.

`report.py` builds the PDF. CSV and JSON are served directly by
api/routers/export.py, which has no formatting to do beyond flattening.

Everything in here inherits one rule from the rest of the system: an unmeasured
component is never printed as a number. A PDF is the artefact most likely to be
read months later by someone who was not in the room, so a `0.00` standing in
for "we did not look" is more dangerous here than anywhere else on screen.
"""

from __future__ import annotations

__all__ = ["report"]
