"""Tests for link/leads.py — the clearnet pointers derived from an actor.

The problem statement asks the system to "link them to suspect real-world
entities". This module is that link, and the whole risk of it is overclaiming:
a shared Protonmail domain, a public Jabber server and a Monero address all
*look* like real-world pointers and none of them locates anybody.

So the tests below are mostly about refusing. A lead carries a qualitative band
and two sentences — how it was found and what it does not prove — and there is
no number anywhere, because a decimal on "this email might belong to a person"
implies a precision nothing here earns.

Nothing in this module feeds the attribution score. `test_leads_do_not_touch_
attribution` at the bottom is the guard on that.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from link.leads import BANDS, derive  # noqa: E402


def ident(type_, value, personas=(1,), meta=None):
    """One identifiers row as the derive() input wants it."""
    return {
        "type": type_,
        "value": value,
        "meta": meta or {},
        "personas": tuple(personas),
    }


def corr(host, **kw):
    row = {
        "onion_url": "http://abc.onion",
        "clearnet_host": host,
        "clearnet_ip": "185.212.44.23",
        "match_type": "tls_serial",
        "score": 1.0,
        "asn": "AS201814",
        "org": "HostVault B.V.",
        "country": "NL",
    }
    row.update(kw)
    return row


def only(leads, kind):
    found = [lead for lead in leads if lead.kind == kind]
    assert found, f"no {kind} lead in {[lead.kind for lead in leads]}"
    return found[0]


# ─────────────────────────────────────────────────────────────────────────────
# Shape
# ─────────────────────────────────────────────────────────────────────────────

def test_every_lead_says_how_it_was_found_and_what_it_does_not_prove():
    """A lead with no caveat is a conclusion wearing a lead's clothes."""
    leads = derive(
        [
            ident("email", "someone@protonmail.com", meta={"domain": "protonmail.com"}),
            ident("btc", "1Mqzt9d3DXoopBkctjwxLwMLLuwwxjxjWm"),
            ident("telegram", "@dread_fam"),
        ],
        [corr("gamma-mirror.hostvault.net")],
    )
    assert leads
    for lead in leads:
        assert lead.band in BANDS, f"{lead.kind} invented band {lead.band!r}"
        assert lead.why and " " in lead.why, f"{lead.kind} has no explanation"
        assert lead.caveat and " " in lead.caveat, f"{lead.kind} has no caveat"
        assert lead.value


def test_there_is_no_numeric_confidence_anywhere():
    """Bands only. A decimal here would imply a precision nothing earns."""
    leads = derive([ident("email", "a@b.com", meta={"domain": "b.com"})], [])
    lead = leads[0]
    assert not hasattr(lead, "score")
    assert not hasattr(lead, "confidence")


# ─────────────────────────────────────────────────────────────────────────────
# Corroboration is what moves a band
# ─────────────────────────────────────────────────────────────────────────────

def test_an_identifier_on_two_personas_outranks_the_same_one_on_one():
    """Carried across two sites is the only thing that makes a pointer strong."""
    single = only(derive([ident("jabber", "x@jabber.example.net",
                                personas=(1,), meta={"domain": "jabber.example.net"})],
                         []), "jabber")
    both = only(derive([ident("jabber", "x@jabber.example.net",
                              personas=(1, 9), meta={"domain": "jabber.example.net"})],
                       []), "jabber")

    assert BANDS.index(both.band) < BANDS.index(single.band), (
        f"corroboration did not raise the band: {single.band} -> {both.band}"
    )
    assert "2 personas" in both.why or "two personas" in both.why


def test_the_personas_carrying_a_lead_are_named():
    lead = only(derive([ident("btc", "1Mqzt9d3DXoopBkctjwxLwMLLuwwxjxjWm",
                              personas=(3, 18))], []), "wallet")
    assert lead.personas == (3, 18)


# ─────────────────────────────────────────────────────────────────────────────
# The refusals — where this module earns its keep
# ─────────────────────────────────────────────────────────────────────────────

def test_a_free_mail_domain_is_not_itself_a_lead():
    """Thousands of people share protonmail.com. The mailbox is the lead."""
    lead = only(derive([ident("email", "nordicpharm.supply@protonmail.com",
                              personas=(2, 10, 17),
                              meta={"domain": "protonmail.com"})], []), "email")
    assert "protonmail.com" in lead.caveat
    assert lead.band != "STRONG", (
        "a shared privacy-mail domain must not reach STRONG however many "
        "personas carry it — the provider will not identify its users"
    )


def test_a_darknet_mail_host_points_back_into_the_dark_web():
    """mail2tor is not a real-world lead. Saying so is the whole point."""
    lead = only(derive([ident("email", "mtl.broker@mail2tor.com",
                              personas=(1, 2),
                              meta={"domain": "mail2tor.com"})], []), "email")
    assert lead.band == "WEAK"
    assert "dark web" in lead.caveat.lower() or "darknet" in lead.caveat.lower()


def test_a_monero_address_gets_no_explorer_link():
    """XMR has no public address lookup. A link would imply one exists."""
    lead = only(derive([ident("xmr", "45kz1FpMcpgM4ogMKetxJNc3WwXnPMhm9CSRWEUB")], []),
                "wallet")
    assert lead.url is None
    assert "monero" in lead.caveat.lower()
    assert lead.band == "WEAK"


def test_an_onion_mirror_is_not_a_clearnet_lead():
    """It points at another hidden service, which is not the real world."""
    leads = derive([ident("onion_mirror", "x6bdjztamavehh2lehtkyyf2wvht2omaynjh2xa")], [])
    assert not [lead for lead in leads if lead.kind == "clearnet_host"]


# ─────────────────────────────────────────────────────────────────────────────
# What a lead points at
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("type_,value,fragment", [
    ("btc", "1Mqzt9d3DXoopBkctjwxLwMLLuwwxjxjWm", "mempool.space"),
    ("eth", "0xeDfff09e7A42D6139E323f865ae8fC75a063be98", "etherscan.io"),
    ("ltc", "LaKAxeY9fx7oTJ6B4xuVee47GEwZVrrvVr", "blockchair.com"),
])
def test_traceable_chains_link_to_a_public_explorer(type_, value, fragment):
    lead = only(derive([ident(type_, value)], []), "wallet")
    assert lead.url and fragment in lead.url
    assert value in lead.url


def test_a_telegram_handle_links_to_the_account():
    lead = only(derive([ident("telegram", "@dread_fam")], []), "telegram")
    assert lead.url == "https://t.me/dread_fam"


def test_a_jabber_address_yields_its_server_as_a_separate_target():
    """The account is one lead; the host that runs it is another."""
    leads = derive([ident("jabber", "dreadpirate@jabber.calyxinstitute.net",
                          meta={"domain": "jabber.calyxinstitute.net"})], [])
    server = only(leads, "jabber_server")
    assert server.value == "jabber.calyxinstitute.net"


def test_a_pgp_uid_surfaces_the_address_inside_it():
    lead = only(derive([ident("pgp_fpr", "A4F57ACBFD98FEB663038D0CA51FDFBD1EDC302F",
                              meta={"uids": ["Dread Pirate <dreadpirate@jabber.calyxinstitute.net>"],
                                    "key_id": "A51FDFBD1EDC302F"})], []),
                "pgp_uid")
    assert "dreadpirate@jabber.calyxinstitute.net" in lead.value
    assert "A51FDFBD1EDC302F" in lead.why or "A51FDFBD1EDC302F" in str(lead.detail)


def test_a_correlated_host_carries_its_hosting_provider():
    """ASN and org are the actual real-world entity — a company you can serve."""
    lead = only(derive([], [corr("gamma-mirror.hostvault.net")]), "clearnet_host")
    assert lead.value == "gamma-mirror.hostvault.net"
    assert lead.detail.get("asn") == "AS201814"
    assert lead.detail.get("org") == "HostVault B.V."
    assert lead.detail.get("country") == "NL"
    assert "185.212.44.23" in str(lead.detail.values()) or lead.detail.get("ip")


def test_a_banner_only_correlation_is_weak():
    """Two hosts running the same nginx is a coincidence until something agrees."""
    lead = only(derive([], [corr("web07.cheaphost.example",
                                 match_type="banner", score=0.40)]), "clearnet_host")
    assert lead.band == "WEAK"


def test_a_certificate_match_outranks_a_banner_match():
    strong = only(derive([], [corr("a.example", match_type="tls_serial", score=1.0)]),
                  "clearnet_host")
    weak = only(derive([], [corr("b.example", match_type="banner", score=0.40)]),
                "clearnet_host")
    assert BANDS.index(strong.band) < BANDS.index(weak.band)


@pytest.mark.parametrize("match,band", [
    ("onion_location", "STRONG"),
    ("tls_serial", "STRONG"),
    ("tls_san", "STRONG"),
    ("favicon", "MODERATE"),
    ("etag", "MODERATE"),
    ("banner", "WEAK"),
])
def test_each_match_type_lands_in_the_band_recon_argues_for(match, band):
    """recon/correlate.py says a banner is a coincidence "until a certificate,
    favicon or ETag agrees with it". These bands say the same thing."""
    lead = only(derive([], [corr("h.example", match_type=match)]), "clearnet_host")
    assert lead.band == band


def test_the_caveat_reads_as_english():
    """'a etag match' shipped once. The panel is read by people."""
    for match in ("etag", "onion_location", "banner"):
        lead = only(derive([], [corr("h.example", match_type=match)]),
                    "clearnet_host")
        assert " a etag" not in lead.why.lower()
        assert " a onion" not in lead.why.lower()
        assert not lead.why.lower().startswith("a etag")


# ─────────────────────────────────────────────────────────────────────────────
# Scope — the actor's own pointers vs a site's infrastructure
# ─────────────────────────────────────────────────────────────────────────────

def test_a_correlated_host_is_scoped_to_the_source_not_the_actor():
    """Every vendor on a marketplace correlates with the same hosting. Saying
    that host is *this* actor's would be the worst thing this panel could do."""
    lead = only(derive([], [corr("gamma-mirror.hostvault.net")]), "clearnet_host")
    assert lead.scope == "source"
    assert "every other vendor" in lead.caveat or "share" in lead.caveat


def test_what_the_actor_published_is_scoped_to_the_actor():
    for lead in derive([ident("email", "a@self-hosted.example",
                              meta={"domain": "self-hosted.example"}),
                        ident("btc", "1Mqzt9d3DXoopBkctjwxLwMLLuwwxjxjWm")], []):
        assert lead.scope == "actor"


def test_the_actors_own_leads_sort_above_site_infrastructure():
    leads = derive(
        [ident("btc", "1Mqzt9d3DXoopBkctjwxLwMLLuwwxjxjWm")],
        [corr("gamma-mirror.hostvault.net", match_type="tls_serial", score=1.0)],
    )
    scopes = [lead.scope for lead in leads]
    assert scopes == sorted(scopes, key=lambda s: s != "actor"), (
        "site infrastructure sorted above what the actor published"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Determinism and isolation
# ─────────────────────────────────────────────────────────────────────────────

def test_derive_is_deterministic():
    rows = [
        ident("email", "a@proton.me", meta={"domain": "proton.me"}),
        ident("btc", "1Mqzt9d3DXoopBkctjwxLwMLLuwwxjxjWm"),
    ]
    first = [(lead.kind, lead.value, lead.band) for lead in derive(rows, [])]
    second = [(lead.kind, lead.value, lead.band) for lead in derive(rows, [])]
    assert first == second


def test_no_input_no_leads():
    assert derive([], []) == []


def test_leads_do_not_touch_attribution():
    """The guard: this module may not reach the scorer or the links table.

    Checked over the import graph rather than the file text — the docstring
    talks about linking at length, and a substring match would fail on prose
    while missing an actual `from score.attribution import ...`.
    """
    import ast  # noqa: PLC0415

    tree = ast.parse((ROOT / "link" / "leads.py").read_text(encoding="utf-8"))

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imported.add(module)
            imported.update(f"{module}.{alias.name}" for alias in node.names)

    for forbidden in ("score", "score.attribution", "link.resolve", "link.cluster"):
        assert not any(name == forbidden or name.startswith(forbidden + ".")
                       for name in imported), (
            f"link/leads.py imports {forbidden!r}; leads are context and must "
            f"never reach the attribution score"
        )

    # It may read Link rows for display, but it must not write them.
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert "attribution_score" not in names
    assert "band_for" not in names


# ─────────────────────────────────────────────────────────────────────────────
# Over the wire, against the real corpus
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    """A signed-in analyst, or skip."""
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    from api.main import app  # noqa: PLC0415

    with fastapi_testclient.TestClient(app) as test_client:
        health = test_client.get("/health")
        if health.status_code != 200 or health.json().get("database") != "ok":
            pytest.skip("database unavailable")
        if not health.json().get("ready"):
            pytest.skip("pipeline has not been run")
        login = test_client.post(
            "/auth/login", json={"username": "analyst", "password": "analyst-demo"}
        )
        if login.status_code != 200:
            pytest.skip("demo accounts are not seeded")
        test_client.headers.update(
            {"Authorization": f"Bearer {login.json()['access_token']}"}
        )
        yield test_client


def test_the_actor_profile_carries_leads(client):
    payload = client.get("/actors/1").json()
    assert payload["leads"], "actor 1 has identifiers but produced no leads"
    assert payload["leads_note"]


def test_every_lead_on_the_wire_keeps_its_caveat(client):
    """The caveat is the whole safety story; it must survive serialisation."""
    for actor_id in (1, 2, 3):
        response = client.get(f"/actors/{actor_id}")
        if response.status_code != 200:
            continue
        for lead in response.json()["leads"]:
            assert lead["band"] in BANDS
            assert lead["scope"] in ("actor", "source")
            assert lead["why"].strip()
            assert len(lead["caveat"]) > 40, (
                f"{lead['kind']} caveat is too short to say anything: "
                f"{lead['caveat']!r}"
            )


def test_the_note_refuses_a_confidence_number(client):
    payload = client.get("/actors/1").json()
    note = payload["leads_note"].lower()
    assert "never conclusions" in note or "corroborate" in note
    assert "no number" in note


def test_marketplace_hosting_is_not_claimed_as_the_actors_own(client):
    """Every vendor on a site shares its hosting. The scope field says so."""
    payload = client.get("/actors/1").json()
    hosts = [lead for lead in payload["leads"] if lead["kind"] == "clearnet_host"]
    assert hosts, "no correlated host reached the profile"
    assert all(lead["scope"] == "source" for lead in hosts)

