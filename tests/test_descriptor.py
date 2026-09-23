"""test_descriptor.py — what a v3 descriptor says, and what it cannot say.

Offline. `analyse()` is pure, and `fixtures/descriptors/` holds descriptors
synthesised by scripts/gen_descriptor_fixtures.py with known properties, so
none of this needs Tor, a network or a control port.

The most important test in this file is the last one: the descriptor does not
contain the service's IP address, and the module must never present an
introduction point relay as though it did.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from recon.descriptor import (  # noqa: E402
    DescriptorFacts,
    IntroPoint,
    analyse,
    intro_point_overlap,
)

FIXTURES = ROOT / "fixtures" / "descriptors"

pytestmark = pytest.mark.skipif(
    not (FIXTURES / "index.json").exists(),
    reason="run scripts/gen_descriptor_fixtures.py first",
)


@pytest.fixture(scope="module")
def index() -> dict:
    return json.loads((FIXTURES / "index.json").read_text(encoding="utf-8"))


def load(name: str, index: dict) -> DescriptorFacts:
    text = (FIXTURES / f"{name}.txt").read_text(encoding="utf-8")
    return analyse(text, index[name])


# ── the outer layer, which always parses ──────────────────────────────────────

def test_a_descriptor_reports_its_version_and_lifetime(index):
    facts = load("plain_service", index)
    assert facts.version == 3
    assert facts.lifetime_minutes == 180


def test_a_descriptor_carries_a_revision_counter(index):
    facts = load("plain_service", index)
    assert isinstance(facts.revision_counter, int)
    assert facts.revision_counter > 0


def test_the_address_is_normalised_with_its_suffix(index):
    bare = index["plain_service"].replace(".onion", "")
    text = (FIXTURES / "plain_service.txt").read_text(encoding="utf-8")
    assert analyse(text, bare).address.endswith(".onion")


# ── the inner layer, which is the point ───────────────────────────────────────

def test_an_ordinary_service_is_not_flagged_single(index):
    assert load("plain_service", index).is_single_service is False


def test_a_single_onion_service_is_detected(index):
    """The one descriptor finding that is genuinely about operator posture."""
    facts = load("single_service", index)
    assert facts.is_single_service is True
    assert any("single onion service" in o for o in facts.observations())
    assert any("not trying to hide where the service runs" in o
               for o in facts.observations())


def test_introduction_points_are_counted(index):
    assert load("plain_service", index).intro_point_count == 2
    assert load("single_service", index).intro_point_count == 1


def test_handshake_formats_are_reported(index):
    assert load("plain_service", index).formats


# ── pow-params, which stem 1.8.2 does not model ───────────────────────────────

def test_pow_params_are_parsed_although_stem_predates_them(index):
    facts = load("pow_service", index)
    assert facts.pow_params is not None
    assert facts.pow_params["scheme"] == "v1"
    assert facts.pow_params["effort"] == 5000


def test_pow_params_are_absent_when_not_advertised(index):
    assert load("plain_service", index).pow_params is None


def test_the_pow_observation_names_the_tor_version_floor(index):
    facts = load("pow_service", index)
    assert any("0.4.8" in o for o in facts.observations())


# ── overlap, and its disclaimer ───────────────────────────────────────────────

def test_intro_point_overlap_returns_a_count_and_a_caveat(index):
    a = load("plain_service", index)
    b = load("single_service", index)
    count, note = intro_point_overlap(a, b)
    assert isinstance(count, int)
    assert "not" in note.lower() and "evidence" in note.lower()


def test_overlap_with_itself_is_total(index):
    a = load("plain_service", index)
    a = DescriptorFacts(
        address=a.address,
        intro_points=(IntroPoint(fingerprint="AAAA"), IntroPoint(fingerprint="BBBB")),
    )
    count, _ = intro_point_overlap(a, a)
    assert count == 2


def test_overlap_ignores_intro_points_with_no_fingerprint():
    a = DescriptorFacts(address="a.onion", intro_points=(IntroPoint(relay_address="1.2.3.4"),))
    b = DescriptorFacts(address="b.onion", intro_points=(IntroPoint(relay_address="1.2.3.4"),))
    count, _ = intro_point_overlap(a, b)
    assert count == 0, "a shared relay IP is not a shared fingerprint"


# ── the refusals ──────────────────────────────────────────────────────────────

def test_the_descriptor_does_not_reveal_the_service_address(index):
    """The central property. A descriptor never contains the service's IP.

    The intro point addresses are public relays the service selected. If this
    module ever labels one as the service's own address, that is the most
    damaging bug it could have, so the field is named `relay_address` and this
    test pins the naming.
    """
    facts = load("plain_service", index)
    assert facts.intro_points
    for point in facts.intro_points:
        assert hasattr(point, "relay_address")
        assert not hasattr(point, "service_address")
        assert not hasattr(point, "ip")

    # And nothing on the facts object offers one either.
    for field in DescriptorFacts.__dataclass_fields__:
        assert "service_ip" not in field
        assert field != "ip_address"


def test_analyse_touches_neither_the_database_nor_the_score():
    import ast

    tree = ast.parse((ROOT / "recon" / "descriptor.py").read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(f"{node.module}.{a.name}" for a in node.names)

    for forbidden in ("db", "score", "score.attribution", "recon.correlate"):
        assert not any(
            n == forbidden or n.startswith(forbidden + ".") for n in imported
        ), f"descriptor.py must not import {forbidden}"


def test_analyse_never_contacts_the_service(index):
    """`analyse` is pure: it is handed text and returns facts."""
    source = (ROOT / "recon" / "descriptor.py").read_text(encoding="utf-8")
    body = source.split("def analyse(")[1].split("\ndef ")[0]
    for banned in ("requests", "socket", "urlopen", "HSFETCH"):
        assert banned not in body


def test_a_corrupted_descriptor_raises_rather_than_inventing_facts(index):
    text = (FIXTURES / "plain_service.txt").read_text(encoding="utf-8")
    with pytest.raises(Exception):
        analyse(text.replace("hs-descriptor 3", "hs-descriptor 9"),
                index["plain_service"])


def test_a_descriptor_read_with_the_wrong_address_does_not_decrypt(index):
    """Decryption is keyed on the address; the wrong one must not half-work."""
    text = (FIXTURES / "plain_service.txt").read_text(encoding="utf-8")
    facts = analyse(text, index["single_service"])
    assert facts.inner_locked is True
    assert facts.intro_points == ()
    assert facts.is_single_service is False
    assert any("did not open" in o for o in facts.observations())


# ── dual-stack intro points ───────────────────────────────────────────────────

def test_a_dual_stack_intro_point_keeps_both_addresses():
    """Regression: the IPv6 specifier used to overwrite the IPv4 one.

    Measured against the live lab service, where two of three introduction
    points advertise both families and both lost their IPv4 address — the
    report showed an IPv6 address with a port glued to the end of it.
    """
    point = IntroPoint(
        relay_address="65.109.108.233", relay_port=9001,
        relay_address_v6="2a01:cb10:822e:1803::303", relay_port_v6=9001,
        link_types=("ipv4", "ipv6"),
    )
    assert point.relay_address == "65.109.108.233"
    assert point.relay_address_v6 == "2a01:cb10:822e:1803::303"


def test_an_ipv6_endpoint_is_bracketed():
    """`2a01:...:9001` is unreadable; RFC 3986 brackets exist for this."""
    point = IntroPoint(relay_address_v6="2a01:cb10::303", relay_port_v6=9001)
    assert point.endpoint == "[2a01:cb10::303]:9001"


def test_an_ipv4_endpoint_is_plain():
    point = IntroPoint(relay_address="65.109.108.233", relay_port=9001)
    assert point.endpoint == "65.109.108.233:9001"


def test_an_intro_point_with_no_address_says_so_rather_than_guessing():
    assert IntroPoint(fingerprint="AAAA").endpoint == "(no address specifier)"
