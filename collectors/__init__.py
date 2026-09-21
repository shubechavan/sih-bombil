"""collectors — live acquisition over Tor.

Phase 6. Until now the pipeline read `fixtures/` off disk; these crawl a real
Tor v3 hidden service and emit the same documents `scripts/ingest.py` already
accepts, so a collected run and a fixtures run reach the database by one path
and can be compared directly.

They go only where an operator sends them. There is no default target list —
see collectors/base.py for the three rules and where each is enforced.
"""

from __future__ import annotations

__all__ = ["base", "forum_collector", "market_collector"]
