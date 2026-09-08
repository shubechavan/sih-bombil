"""recon — passive fingerprinting of hidden services and clearnet correlation.

Everything in this package reads only what a server already publishes to any
visitor: response headers, banners, favicons, robots.txt, sitemaps, certificates
and conventionally-public status pages. There is no exploitation, no
authentication, no credential use and no brute force anywhere in it, and
`recon.fingerprint.PROBE_PATHS` is asserted against that rule in
tests/test_recon.py.

Findings are *site-level*. `infra_findings` is keyed by onion URL and joins to
`sources`, not to `personas` — a vendor renting a stall on a market does not run
that market's web server. See link/infra.py for why that distinction decides
whether the attribution formula's I term can be measured at all.
"""

from __future__ import annotations

__all__ = ["correlate", "fingerprint", "tor"]
