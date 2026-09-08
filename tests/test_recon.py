"""test_recon.py — passive fingerprinting.

The first test in this file is the important one: it encodes CLAUDE.md's
passive-only rule as an assertion rather than a paragraph, so a later change
that adds a login probe or a POST fails the suite instead of the review.
"""

from __future__ import annotations

import base64
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from recon import fingerprint as fp  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# The passive-only contract
# ─────────────────────────────────────────────────────────────────────────────

def test_recon_only_ever_issues_a_get():
    assert fp.PROBE_METHOD == "GET"


def test_the_probe_surface_is_conventional_public_paths_only():
    """No auth, no admin, no guessing. If this list grows, justify it here."""
    assert set(fp.PROBE_PATHS) == {
        "/", "/favicon.ico", "/robots.txt", "/sitemap.xml",
        "/server-status", "/server-info",
    }


def test_no_probe_path_reaches_for_credentials_or_private_state():
    forbidden = ("login", "admin", "auth", "password", "passwd", "token",
                 "api_key", "apikey", "secret", "wp-admin", "phpmyadmin",
                 "..", "%2e", "shell", "cmd", "backup", ".git", ".env")
    for path in fp.PROBE_PATHS:
        lowered = path.lower()
        for needle in forbidden:
            assert needle not in lowered, (
                f"probe path {path!r} contains {needle!r} — recon is passive "
                f"collection of what the server already publishes"
            )


def test_the_rate_limit_matches_what_claude_md_mandates():
    assert fp.PER_HOST_INTERVAL == 2.0
    assert fp.GLOBAL_CONCURRENCY >= 1


# ─────────────────────────────────────────────────────────────────────────────
# Rate limiting
# ─────────────────────────────────────────────────────────────────────────────

class FakeClock:
    """A monotonic clock that only advances when someone sleeps on it."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def test_two_requests_to_one_host_are_two_seconds_apart():
    clock = FakeClock()
    limiter = fp.HostRateLimiter(clock=clock.time, sleep=clock.sleep)

    assert limiter.acquire("a.onion") == 0.0
    limiter.release()
    assert limiter.acquire("a.onion") == pytest.approx(2.0)
    limiter.release()
    assert clock.slept == [2.0]


def test_different_hosts_do_not_wait_on_each_other():
    clock = FakeClock()
    limiter = fp.HostRateLimiter(clock=clock.time, sleep=clock.sleep)

    for host in ("a.onion", "b.onion", "c.onion"):
        assert limiter.acquire(host) == 0.0
        limiter.release()
    assert clock.slept == []


def test_a_host_that_has_waited_long_enough_is_not_delayed_again():
    clock = FakeClock()
    limiter = fp.HostRateLimiter(clock=clock.time, sleep=clock.sleep)

    limiter.acquire("a.onion")
    limiter.release()
    clock.now += 5.0
    assert limiter.acquire("a.onion") == 0.0
    limiter.release()


def test_racing_threads_on_one_host_queue_rather_than_fire_together():
    """Four threads released at once must still leave the host alone between hits.

    The reservation is written under the lock as an absolute slot, so a caller
    that arrives while another is still sleeping queues behind that slot instead
    of reading an already-expired timestamp and firing alongside it. Timed on the
    real clock at a short interval, because what matters is the spacing of the
    requests rather than what any one caller was told to wait.
    """
    interval = 0.05
    limiter = fp.HostRateLimiter(interval=interval, concurrency=8)
    stamps: list[float] = []
    guard = threading.Lock()
    barrier = threading.Barrier(4)

    def probe():
        barrier.wait()
        limiter.acquire("a.onion")
        with guard:
            stamps.append(time.monotonic())
        limiter.release()

    threads = [threading.Thread(target=probe) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    stamps.sort()
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert len(gaps) == 3
    # a little slack for scheduler jitter; the point is that none of them
    # collapsed to zero
    assert all(gap >= interval * 0.8 for gap in gaps), gaps


def test_the_global_cap_bounds_how_many_probes_run_at_once():
    limiter = fp.HostRateLimiter(interval=0.0, concurrency=2)
    live = 0
    peak = 0
    guard = threading.Lock()

    def probe(host):
        nonlocal live, peak
        limiter.acquire(host)
        with guard:
            live += 1
            peak = max(peak, live)
        time.sleep(0.02)
        with guard:
            live -= 1
        limiter.release()

    threads = [threading.Thread(target=probe, args=(f"h{i}.onion",))
               for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert peak <= 2


def test_a_nonsense_limiter_is_rejected():
    with pytest.raises(ValueError):
        fp.HostRateLimiter(interval=-1)
    with pytest.raises(ValueError):
        fp.HostRateLimiter(concurrency=0)


# ─────────────────────────────────────────────────────────────────────────────
# Favicon hashing — the pivot that has to match somebody else's number
# ─────────────────────────────────────────────────────────────────────────────

def test_favicon_hash_follows_the_shodan_convention():
    """mmh3 over the *wrapped base64*, not the raw bytes.

    Hashing the raw bytes yields a self-consistent number that correlates with
    nothing anyone else has published — the worst possible failure for a pivot,
    because it looks like it works.
    """
    mmh3 = pytest.importorskip("mmh3")
    data = b"\x00\x01\x02" * 500

    assert fp.favicon_hash(data) == str(mmh3.hash(base64.encodebytes(data)))
    assert fp.favicon_hash(data) != str(mmh3.hash(data))
    assert fp.favicon_hash(data) != str(mmh3.hash(base64.b64encode(data)))


def test_favicon_hash_is_a_string_like_the_column_holds():
    pytest.importorskip("mmh3")
    value = fp.favicon_hash(b"icon-bytes")
    assert isinstance(value, str)
    assert int(value) == int(value)  # round-trips as a signed int


def test_no_favicon_is_none_not_a_hash_of_nothing():
    assert fp.favicon_hash(b"") is None


# ─────────────────────────────────────────────────────────────────────────────
# Parsers
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("html", [
    "<html><body><h1>Welcome to nginx!</h1></body></html>",
    "<title>Apache2 Ubuntu Default Page: It works</title>",
    "<h1>It works!</h1>",
])
def test_default_pages_are_recognised(html):
    assert fp.detect_default_page(html)


def test_a_real_page_is_not_a_default_page():
    assert not fp.detect_default_page(
        "<html><title>Alpha Market</title><h1>Vendors</h1></html>"
    )


def test_directory_listings_are_recognised():
    assert fp.detect_dir_listing("<html><title>Index of /uploads</title></html>")
    assert fp.detect_dir_listing("<h1>Index of /</h1>")
    assert not fp.detect_dir_listing("<h1>Index of our products</h1>")


def test_html_comments_are_collected_in_order_and_deduplicated():
    html = ("<!-- build 2024.11 -->x<!-- build 2024.11 -->y"
            "<!-- TODO:\n  remove cdn -->")
    assert fp.html_comments(html) == [
        "<!-- build 2024.11 -->", "<!-- TODO: remove cdn -->"
    ]


def test_generator_meta_is_found_whichever_order_the_attributes_come_in():
    assert fp.generator_meta(
        '<meta name="generator" content="phpBB 3.2.11">') == "phpBB 3.2.11"
    assert fp.generator_meta(
        '<meta content="WordPress 6.4" name="generator">') == "WordPress 6.4"
    assert fp.generator_meta("<meta name='viewport' content='width'>") is None


def test_clearnet_refs_exclude_onions_and_are_sorted():
    html = (
        '<link href="https://cdn-static-eu.hostvault.net/assets/app.css">'
        '<a href="http://abc.onion/page">mirror</a>'
        '<!-- see https://gamma-mirror.hostvault.net/status -->'
        '<img src="/local/logo.png">'
    )
    assert fp.clearnet_refs(html) == [
        "https://cdn-static-eu.hostvault.net/assets/app.css",
        "https://gamma-mirror.hostvault.net/status",
    ]


def test_a_page_with_no_clearnet_reference_yields_nothing():
    assert fp.clearnet_refs('<a href="http://abc.onion/x">only onions</a>') == []


# ─────────────────────────────────────────────────────────────────────────────
# misconfig_score
# ─────────────────────────────────────────────────────────────────────────────

def test_a_clean_service_scores_zero_and_a_leaky_one_scores_one():
    clean = fp.Fingerprint(onion_url="http://a.onion")
    assert fp.score_misconfig(clean)[0] == 0.0

    leaky = fp.Fingerprint(
        onion_url="http://a.onion", status_exposed=True, dir_listing=True,
        default_page=True, etag='W/"x"', powered_by="PHP/7.4",
        generator_meta="WordPress 6.4", server_banner="nginx/1.18.0",
        html_comments=["<!-- x -->"], clearnet_refs=["https://e.example/a.css"],
        robots_txt="User-agent: *\nDisallow: /admin\n",
    )
    assert fp.score_misconfig(leaky)[0] == 1.0


def test_every_signal_is_named_in_the_breakdown_whether_or_not_it_fired():
    score, breakdown = fp.score_misconfig(fp.Fingerprint(onion_url="http://a.onion"))
    assert {b["signal"] for b in breakdown} == set(fp.MISCONFIG_SIGNALS)
    assert all(b["present"] is False for b in breakdown)


def test_adding_a_leak_can_only_raise_the_score():
    base = fp.Fingerprint(onion_url="http://a.onion", etag='W/"x"')
    worse = fp.Fingerprint(onion_url="http://a.onion", etag='W/"x"',
                           status_exposed=True)
    assert fp.score_misconfig(worse)[0] > fp.score_misconfig(base)[0]


def test_an_empty_robots_disallow_is_not_a_disclosure():
    """`Disallow:` with nothing after it permits everything and names nothing."""
    permissive = fp.Fingerprint(onion_url="http://a.onion",
                                robots_txt="User-agent: *\nDisallow:\n")
    naming = fp.Fingerprint(onion_url="http://a.onion",
                            robots_txt="User-agent: *\nDisallow: /backup\n")
    assert fp.score_misconfig(permissive)[0] == 0.0
    assert fp.score_misconfig(naming)[0] > 0.0


def test_a_banner_without_a_version_is_not_a_version_leak():
    bare = fp.Fingerprint(onion_url="http://a.onion", server_banner="nginx")
    versioned = fp.Fingerprint(onion_url="http://a.onion",
                               server_banner="nginx/1.18.0")
    assert fp.score_misconfig(bare)[0] == 0.0
    assert fp.score_misconfig(versioned)[0] > 0.0


def test_a_sitemap_is_not_treated_as_a_misconfiguration():
    """Publishing a sitemap is intentional. Scoring it would make 'leaky' mean
    'has a sitemap'."""
    assert "sitemap" not in " ".join(fp.MISCONFIG_SIGNALS)
    with_sitemap = fp.Fingerprint(onion_url="http://a.onion",
                                  sitemap_xml="<urlset/>")
    assert fp.score_misconfig(with_sitemap)[0] == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures mode
# ─────────────────────────────────────────────────────────────────────────────

def test_fixtures_mode_needs_no_network_and_yields_one_finding_per_source():
    findings = fp.read_fixture_findings()
    assert len(findings) == 3
    assert {f.source_id for f in findings} == {1, 2, 3}
    assert all(f.host.endswith(".onion") for f in findings)


def test_fixtures_mode_recomputes_rather_than_trusting_the_declared_score():
    """Both modes run the same rubric, so --source fixtures and --source live
    mean the same pipeline. The declared value is kept only to report the gap."""
    for finding in fp.read_fixture_findings():
        assert finding.declared_misconfig_score is not None
        assert finding.misconfig_score == fp.score_misconfig(finding)[0]


def test_the_rubric_lands_close_to_the_hand_authored_fixture_values():
    """Recorded, not patched: docs/BUILD_PLAN.md carries the deltas.

    This is a regression guard on the rubric, not a claim that the fixture
    numbers are ground truth — they were hand-written, and the divergence is
    documented rather than tuned away.
    """
    for finding in fp.read_fixture_findings():
        assert abs(finding.misconfig_score - finding.declared_misconfig_score) < 0.05


def test_the_row_written_to_the_database_carries_only_schema_columns():
    row = fp.read_fixture_findings()[0].as_row()
    assert "declared_misconfig_score" not in row
    assert 0.0 <= row["misconfig_score"] <= 1.0


def test_the_served_header_order_is_captured_separately_from_the_headers():
    """Postgres re-sorts JSONB object keys, so the wire order needs its own
    column or the banner rule silently stops firing on stored findings."""
    alpha = fp.read_fixture_findings()[0]
    assert alpha.header_order == ["Server", "X-Powered-By", "ETag", "Content-Type"]
    assert alpha.as_row()["header_order"] == alpha.header_order


def test_header_order_is_never_inferred_from_the_headers_mapping():
    """Empty means unknown. Defaulting it would invent an order from whatever
    key sequence the storage layer happened to hand back."""
    finding = fp.Fingerprint(onion_url="http://a.onion",
                             headers={"ETag": "z", "Server": "x"})
    assert finding.header_order == []
