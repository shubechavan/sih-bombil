"""test_correlate.py — onion↔clearnet candidate scoring.

The fixture corpus was built with the answers planted in it: market_alpha and
market_gamma share a favicon with a CDN host, market_alpha's ETag appears on a
staging box, market_gamma's certificate serial and SANs name a mirror, and six
further records are noise that must not be linked. These tests check the module
finds exactly that, and — just as important — that the weak signal stays weak.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from recon import correlate as co  # noqa: E402
from recon.fingerprint import Fingerprint, read_fixture_findings  # noqa: E402

ALPHA = "http://7i5rgrutibeuicqoyved7evdkadd5ybtrdjrxcybpvprms2lio2pofyd.onion"
BETA = "http://awiplo4np7ezfbhffufxdnjdpcfuue2mqfiplcneld5jfsamgi3fmoad.onion"
GAMMA = "http://odqwdplj5e3gdip5hu7mr4u4hetnej6uuz476tq7cm3ir4r3s2r72fyd.onion"


@pytest.fixture(scope="module")
def findings():
    return read_fixture_findings()


@pytest.fixture(scope="module")
def provider():
    return co.FixturesProvider()


@pytest.fixture(scope="module")
def observations(provider, findings):
    return provider.observations(co.Pivots.from_findings(findings))


@pytest.fixture(scope="module")
def results(findings, provider, observations):
    return co.correlate(findings, provider, observations)


def signals(results, onion, host) -> dict[str, float]:
    return {r.match_type: r.score for r in results
            if r.onion_url == onion and r.clearnet_host == host}


# ─────────────────────────────────────────────────────────────────────────────
# The rubric
# ─────────────────────────────────────────────────────────────────────────────

def test_the_rubric_is_the_one_the_build_plan_specifies():
    assert co.MATCH_WEIGHTS == {
        "tls_serial": 1.00,
        "tls_san": 0.95,
        "favicon": 0.80,
        "etag": 0.70,
        "banner": 0.40,
    }


def test_a_certificate_outranks_a_favicon_outranks_a_banner():
    order = list(co.MATCH_WEIGHTS.values())
    assert order == sorted(order, reverse=True)


# ─────────────────────────────────────────────────────────────────────────────
# The planted pivots
# ─────────────────────────────────────────────────────────────────────────────

def test_the_gamma_mirror_is_found_by_certificate(results):
    found = signals(results, GAMMA, "gamma-mirror.hostvault.net")
    assert found["tls_serial"] == 1.00
    assert found["tls_san"] == 0.95


def test_the_shared_cdn_is_found_by_favicon_and_etag(results):
    found = signals(results, ALPHA, "cdn-static-eu.hostvault.net")
    assert found["favicon"] == 0.80
    assert found["etag"] == 0.70


def test_gamma_reaches_the_cdn_by_favicon_but_not_by_etag(results):
    """The two markets share an icon; their ETags differ, and must."""
    found = signals(results, GAMMA, "cdn-static-eu.hostvault.net")
    assert found["favicon"] == 0.80
    assert "etag" not in found


def test_the_staging_box_is_found_by_etag_alone(results):
    found = signals(results, ALPHA, "staging.hostvault.net")
    assert found["etag"] == 0.70


def test_the_certificate_match_beats_every_other_candidate(results):
    combined = co.roll_up(results)
    assert combined[(GAMMA, "gamma-mirror.hostvault.net")] == 1.0
    assert combined[(GAMMA, "gamma-mirror.hostvault.net")] > \
        combined[(GAMMA, "cdn-static-eu.hostvault.net")]


# ─────────────────────────────────────────────────────────────────────────────
# The noise must stay noise
# ─────────────────────────────────────────────────────────────────────────────

def test_the_unrelated_hosts_never_rise_above_a_banner_match(results):
    """web07 and blog.personal run the same software and nothing more."""
    for onion, host in ((ALPHA, "web07.cheaphost.example"),
                        (BETA, "blog.personal.example")):
        found = signals(results, onion, host)
        assert set(found) == {"banner"}
        assert co.roll_up(results)[(onion, host)] == 0.40


def test_hosts_running_other_software_match_nothing(results):
    matched = {r.clearnet_host for r in results}
    for host in ("mail.example-corp.net", "shop.retailer.example",
                 "api.fintech.example", "files.university.example",
                 "cache.mediacdn.example"):
        assert host not in matched


def test_an_empty_favicon_hash_is_not_a_match():
    """Two hosts with no icon have not got the same icon."""
    finding = Fingerprint(onion_url="http://a.onion", favicon_hash="0")
    observation = {"ip_str": "1.2.3.4", "hostnames": ["h.example"],
                   "http": {"favicon": {"hash": "0"}}}
    assert co.correlate([finding], co.FixturesProvider(), [observation]) == []


# ─────────────────────────────────────────────────────────────────────────────
# Comparison rules
# ─────────────────────────────────────────────────────────────────────────────

def test_the_distribution_suffix_is_not_part_of_the_software_identity():
    assert co.normalise_banner("nginx/1.18.0 (Ubuntu)") == "nginx/1.18.0"
    assert co.normalise_banner("nginx/1.18.0") == "nginx/1.18.0"
    assert co.normalise_banner(None) == ""


def test_the_version_still_is_part_of_it():
    assert co.normalise_banner("nginx/1.18.0") != co.normalise_banner("nginx/1.24.0")


def test_header_order_is_compared_over_the_shared_keys_only():
    onion = ["Server", "X-Powered-By", "ETag", "Content-Type"]
    assert co.header_order_agrees(onion, {"Server": "x", "ETag": "z"})
    assert not co.header_order_agrees(onion, {"ETag": "z", "Server": "x"})


def test_one_header_in_common_is_not_an_order_match():
    """Every host on earth sends `Server`. One key cannot disagree with itself."""
    assert not co.header_order_agrees(["Server"], {"Server": "x"})
    assert co.MIN_SHARED_HEADERS == 2


def test_an_unknown_header_order_declines_rather_than_guessing():
    """A finding read back from Postgres with no stored order must not fall
    back to `headers`, whose keys JSONB has re-sorted."""
    assert not co.header_order_agrees([], {"Server": "x", "ETag": "z"})

    finding = Fingerprint(
        onion_url="http://a.onion", server_banner="nginx/1.18.0",
        headers={"ETag": "z", "Server": "x"},  # JSONB's ordering, not the wire's
        header_order=[],
    )
    observation = {"ip_str": "1.2.3.4", "hostnames": ["h.example"],
                   "http": {"server": "nginx/1.18.0"},
                   "headers": {"Server": "x", "ETag": "z"}}
    assert co.correlate([finding], co.FixturesProvider(), [observation]) == []


def test_a_banner_match_needs_the_header_order_too(results):
    """gamma-mirror sends only `Server`, so its banner match cannot fire."""
    assert "banner" not in signals(results, GAMMA, "gamma-mirror.hostvault.net")


def test_a_wildcard_san_covers_one_label_and_no_more():
    finding = Fingerprint(onion_url="http://a.onion", tls_sans=["*.hostvault.net"])
    assert co._san_matches(finding.tls_sans, "cdn.hostvault.net")
    assert not co._san_matches(finding.tls_sans, "deep.cdn.hostvault.net")
    assert not co._san_matches(finding.tls_sans, "hostvault.net")


# ─────────────────────────────────────────────────────────────────────────────
# Rows, roll-up and storage shape
# ─────────────────────────────────────────────────────────────────────────────

def test_every_row_carries_its_own_reason(results):
    for correlation in results:
        assert correlation.evidence
        assert all(e.get("type") and e.get("detail") for e in correlation.evidence)
        assert correlation.provider == "fixtures"


def test_scores_stay_inside_the_check_constraint(results):
    for correlation in results:
        assert 0.0 <= correlation.as_row()["score"] <= 1.0


def test_one_row_per_onion_host_and_signal(results):
    keys = [r.key for r in results]
    assert len(keys) == len(set(keys))


def test_the_roll_up_combines_signals_the_way_identifiers_combine():
    """1 - Π(1 - w), shared with score/attribution.py rather than reimplemented."""
    rolled = co.roll_up([
        co.Correlation(onion_url="o", clearnet_host="h", clearnet_ip=None,
                       clearnet_port=None, match_type="favicon", score=0.80),
        co.Correlation(onion_url="o", clearnet_host="h", clearnet_ip=None,
                       clearnet_port=None, match_type="etag", score=0.70),
    ])
    assert rolled[("o", "h")] == pytest.approx(1 - 0.2 * 0.3)


# ─────────────────────────────────────────────────────────────────────────────
# The provider interface
# ─────────────────────────────────────────────────────────────────────────────

def test_swapping_the_provider_changes_only_where_records_come_from(findings,
                                                                    observations):
    class Stub:
        name = "stub"

        def observations(self, pivots):
            return list(observations)

        def caveats(self):
            return []

    stub = co.correlate(findings, Stub(), None)
    fixtures = co.correlate(findings, co.FixturesProvider(), observations)
    assert [(r.onion_url, r.clearnet_host, r.match_type, r.score) for r in stub] == \
           [(r.onion_url, r.clearnet_host, r.match_type, r.score) for r in fixtures]
    assert {r.provider for r in stub} == {"stub"}


def test_pivots_are_derived_from_the_findings(findings):
    pivots = co.Pivots.from_findings(findings)
    assert "-1274392844" in pivots.favicon_hashes
    assert "0F3A9C1D77B54E2A" in pivots.cert_serials
    assert "nginx/1.18.0" in pivots.banners


def test_an_unknown_provider_is_rejected_by_name():
    with pytest.raises(ValueError, match="censys"):
        co.provider_for("censys")


def test_shodan_refuses_to_construct_without_a_key(monkeypatch):
    monkeypatch.delenv("SHODAN_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="SHODAN_API_KEY"):
        co.ShodanProvider()


def test_the_shodan_path_declares_itself_unverified_at_runtime(monkeypatch):
    """Not only in the docstring: the caveat has to reach the operator."""
    monkeypatch.setenv("SHODAN_API_KEY", "not-a-real-key")
    provider = co.ShodanProvider()
    caveats = provider.caveats()
    assert caveats and "never been exercised" in caveats[0]


def test_a_row_from_the_unverified_provider_carries_the_caveat(monkeypatch,
                                                              findings,
                                                              observations):
    monkeypatch.setenv("SHODAN_API_KEY", "not-a-real-key")
    provider = co.ShodanProvider()
    rows = co.correlate(findings, provider, observations)
    assert rows
    for row in rows:
        details = [e["detail"] for e in row.evidence if e["type"] == "provenance"]
        assert any("never been exercised" in d for d in details)


def test_shodan_queries_are_built_from_the_pivots(monkeypatch, findings):
    monkeypatch.setenv("SHODAN_API_KEY", "not-a-real-key")
    queries = co.ShodanProvider().queries(co.Pivots.from_findings(findings))
    assert "ssl.cert.serial:0F3A9C1D77B54E2A" in queries
    assert "http.favicon.hash:-1274392844" in queries
