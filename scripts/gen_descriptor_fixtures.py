"""gen_descriptor_fixtures.py — synthesise v3 descriptors with known properties.

    python scripts/gen_descriptor_fixtures.py

Writes `fixtures/descriptors/`: one descriptor per configuration we want
`recon/descriptor.py` to recognise, plus `index.json` mapping each file to the
onion address whose key signed it (a descriptor can only be decrypted by its
own address, so the pairing has to be recorded).

WHY SYNTHESISE RATHER THAN CAPTURE
----------------------------------
A captured descriptor from a real hidden service would put a real service's
signed material in this repo, would name real introduction point relays, and
would expire. Worse, it would be one configuration — whichever that service
happened to run — so the interesting cases (single-onion, proof-of-work,
client auth) would be untestable.

These are generated from freshly minted Ed25519 keys that exist for the length
of this script. The addresses are real addresses in the sense that they carry a
valid v3 checksum; no service has ever answered on any of them.

Re-running overwrites the files and produces *different* addresses, because new
keys are generated each time. That is intentional — the tests read `index.json`
rather than hard-coding addresses, so they survive a regeneration. Nothing
about the corpus depends on these being stable, unlike `fixtures/` proper.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / "fixtures" / "descriptors"

#: A plausible pow-params line (proposal 327). stem 1.8.2 predates it, so it
#: rides in as an unrecognized line and recon/descriptor.py parses it by hand.
POW_LINE = "pow-params v1 GMTGV3tnL9WJYFwCJ1zrhA 5000 2026-10-01T00:00:00"


def build(name: str, *, single: bool, intros: list[tuple[str, int]],
          extra: str = "") -> str:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from stem.descriptor.hidden_service import (
        HiddenServiceDescriptorV3,
        InnerLayer,
        IntroductionPointV3,
    )

    key = Ed25519PrivateKey.generate()
    address = HiddenServiceDescriptorV3.address_from_identity_key(key.public_key())

    points = [IntroductionPointV3.create_for_address(a, p) for a, p in intros]
    attr = {"single-onion-service": ""} if single else None
    inner = InnerLayer.create(attr, introduction_points=points)

    if extra:
        # Splice the unrecognized line in ahead of a line we know is present,
        # rather than appending — a descriptor's trailing section is signed
        # material and order matters to the parser.
        spliced = str(inner).replace("create2-formats", f"{extra}\ncreate2-formats", 1)
        inner = InnerLayer(spliced.encode(), validate=False)

    content = HiddenServiceDescriptorV3.content(inner_layer=inner, identity_key=key)
    if isinstance(content, bytes):
        content = content.decode("utf-8")

    (OUT / f"{name}.txt").write_text(content, encoding="utf-8")
    return address


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    cases = {
        # The ordinary case: three-hop service, two intro points.
        "plain_service": dict(single=False, intros=[("1.2.3.4", 9001),
                                                    ("5.6.7.8", 443)]),
        # The finding that actually matters: the operator turned off their own
        # location anonymity.
        "single_service": dict(single=True, intros=[("1.2.3.4", 9001)]),
        # Under denial of service, and running Tor >= 0.4.8 to say so.
        "pow_service": dict(single=False, intros=[("9.9.9.9", 9001)],
                            extra=POW_LINE),
    }

    index = {name: build(name, **kwargs) for name, kwargs in cases.items()}
    (OUT / "index.json").write_text(
        json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    print(f"wrote {len(index)} descriptors to {OUT.relative_to(ROOT)}")
    for name, address in sorted(index.items()):
        print(f"  {name:16} {address}")
    print("\nAddresses change on every run; tests read index.json.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
