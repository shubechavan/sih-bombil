"""test_onion_location.py — the link a site declares about itself.

Offline and deterministic: `detect()` parses a response somebody else fetched,
so there is nothing to mock and no network to avoid.

VALID is a genuine v3 address (generated once with an Ed25519 key and checked
against stem), so the checksum assertions below exercise real arithmetic rather
than a string that happens to be 56 characters long.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from recon.onion_location import (  # noqa: E402
    OnionLocation,
    detect,
    is_valid_v3_address,
    parse_meta,
)

VALID = "6n7tpqq66cdyr3z7o4m6w6phoheyjvncsjff6ydmnbfsvizxjs3uxmyd.onion"
#: Same length, same alphabet, wrong checksum — the case a regex would accept.
CORRUPT = "zijq52cw5d6l4rf7xxdglow7pa2cpqllz77btuons2l5hmqtci7puoad.onion"


# ── address validation ────────────────────────────────────────────────────────

def test_a_real_v3_address_validates():
    assert is_valid_v3_address(VALID)


def test_the_bare_address_validates_without_the_suffix():
    assert is_valid_v3_address(VALID.replace(".onion", ""))


def test_a_corrupted_address_fails_the_checksum():
    """The whole point of checksumming rather than regexing."""
    assert not is_valid_v3_address(CORRUPT)


@pytest.mark.parametrize("bad", [
    "",
    "example.com",
    "short.onion",
    "UPPERCASE" + "a" * 47 + ".onion",
    "3g2upl4pq6kufc4m.onion",          # a well-formed v2 address; v2 is dead
    "!" * 56 + ".onion",
])
def test_malformed_addresses_are_rejected(bad):
    assert not is_valid_v3_address(bad)


# ── header path ───────────────────────────────────────────────────────────────

def test_a_header_declaration_is_detected():
    found = detect(f"https://example.com/", {"Onion-Location": f"http://{VALID}/"})
    assert isinstance(found, OnionLocation)
    assert found.onion_address == VALID
    assert found.via == "header"
    assert found.clearnet_host == "example.com"


def test_the_header_name_is_matched_case_insensitively():
    """HTTP header names are case-insensitive and servers disagree in practice."""
    for name in ("onion-location", "Onion-Location", "ONION-LOCATION"):
        assert detect("https://example.com/", {name: f"http://{VALID}/"})


def test_headers_may_arrive_as_pairs_rather_than_a_mapping():
    found = detect("https://example.com/", [("Onion-Location", f"http://{VALID}/")])
    assert found and found.onion_address == VALID


def test_no_declaration_returns_none():
    assert detect("https://example.com/", {"Server": "nginx"}) is None


def test_a_declaration_over_https_is_marked_honoured():
    found = detect("https://example.com/", {"Onion-Location": f"http://{VALID}/"})
    assert found.honoured_by_browser is True


def test_a_declaration_over_plain_http_is_kept_but_marked_inert():
    """Tor Browser ignores it; an analyst should still see the operator said it."""
    found = detect("http://example.com/", {"Onion-Location": f"http://{VALID}/"})
    assert found is not None
    assert found.honoured_by_browser is False
    assert "plain HTTP" in found.caveat


# ── meta tag path ─────────────────────────────────────────────────────────────

def test_a_meta_tag_declaration_is_detected():
    body = f'<html><head><meta http-equiv="onion-location" content="http://{VALID}/">'
    found = detect("https://example.com/", {}, body)
    assert found and found.onion_address == VALID
    assert found.via == "meta"


def test_the_meta_tag_survives_attribute_reordering():
    body = f"<meta content='http://{VALID}/' http-equiv='onion-location'/>"
    assert parse_meta(body) == f"http://{VALID}/"


def test_a_meta_tag_in_bytes_is_decoded():
    body = f'<meta http-equiv="onion-location" content="http://{VALID}/">'.encode()
    found = detect("https://example.com/", {}, body)
    assert found and found.onion_address == VALID


def test_the_header_wins_over_a_meta_tag():
    """The spec's primary mechanism; also, a header is harder to inject."""
    body = f'<meta http-equiv="onion-location" content="http://{CORRUPT}/">'
    found = detect("https://example.com/", {"Onion-Location": f"http://{VALID}/"}, body)
    assert found.via == "header"
    assert found.onion_address == VALID


def test_an_unrelated_meta_tag_is_ignored():
    body = '<meta http-equiv="refresh" content="0; url=http://example.com/">'
    assert parse_meta(body) is None
    assert detect("https://example.com/", {}, body) is None


# ── refusals ──────────────────────────────────────────────────────────────────

def test_a_declared_address_that_fails_its_checksum_is_dropped():
    """Declared is not the same as valid. A bad value must not become a finding."""
    assert detect("https://example.com/", {"Onion-Location": f"http://{CORRUPT}/"}) is None


def test_a_declaration_pointing_at_a_clearnet_host_is_dropped():
    assert detect("https://example.com/", {"Onion-Location": "https://evil.com/"}) is None


def test_an_empty_declaration_is_dropped():
    assert detect("https://example.com/", {"Onion-Location": "   "}) is None


def test_a_bare_address_with_no_scheme_is_still_read():
    """Seen in the wild; the spec wants a URL but operators paste the address."""
    found = detect("https://example.com/", {"Onion-Location": VALID})
    assert found and found.onion_address == VALID


# ── the sentences the UI prints ───────────────────────────────────────────────

def test_a_finding_explains_itself_and_qualifies_itself():
    found = detect("https://example.com/", {"Onion-Location": f"http://{VALID}/"})
    assert "example.com" in found.why and VALID in found.why
    assert "unauthenticated" in found.caveat
    assert found.why.endswith(".") and found.caveat.endswith(".")


def test_the_declared_value_is_preserved_verbatim():
    raw = f"http://{VALID}/vendor/7?ref=clearnet"
    found = detect("https://example.com/", {"Onion-Location": raw})
    assert found.declared_value == raw
    assert found.onion_address == VALID


def test_a_finding_is_immutable():
    found = detect("https://example.com/", {"Onion-Location": f"http://{VALID}/"})
    with pytest.raises((AttributeError, TypeError)):
        found.onion_address = "x"  # type: ignore[misc]


# ── the restraint ─────────────────────────────────────────────────────────────

def test_onion_location_does_not_write_correlations_or_touch_the_score():
    """No `infra_correlations` write until the signal has been measured.

    That table feeds the I term. `scripts/measure_reliability.py` is the
    precedent: a plausible signal stays out of the score until ground truth
    says it belongs there, and this corpus has no Onion-Location ground truth.
    """
    import ast

    tree = ast.parse(
        (ROOT / "recon" / "onion_location.py").read_text(encoding="utf-8")
    )
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
        ), f"onion_location.py must not import {forbidden}"


def test_the_module_makes_no_network_calls():
    """Passive means it parses what was already fetched."""
    source = (ROOT / "recon" / "onion_location.py").read_text(encoding="utf-8")
    for banned in ("requests", "urlopen", "socket", "httpx"):
        assert banned not in source, f"{banned} has no business in this module"
