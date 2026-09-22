"""leads.py — the clearnet pointers an actor leaves behind.

The problem statement's headline is "deanonymize threat actors and link them to
suspect real-world entities". Everything else in this project links personas to
each other; this module is the one that points *outward*, at a mailbox, a
hosting company, a wallet, an XMPP server — the things an investigator can
actually serve process on or subpoena.

WHY THERE IS NO NUMBER HERE
---------------------------
Every other score in this codebase is measured against ground truth. This one
cannot be: `fixtures/ground_truth.json` records which personas share an actor,
not which real-world person is behind them, so there is nothing to calibrate a
0..1 confidence against. Inventing one would produce a decimal with the *shape*
of a measurement and none of the substance, next to the genuinely measured
attribution scores — which is exactly how a reader learns to distrust both.

So a lead carries a band, a sentence saying how it was found, and a sentence
saying what it does not prove. The bands are the same vocabulary the rest of the
system uses, deliberately.

WHAT MAKES A LEAD WEAK, WHICH IS MOST OF THEM
---------------------------------------------
Three traps this module exists to refuse:

  * **Shared infrastructure looks like identity.** Thousands of people have a
    protonmail.com address. The mailbox is a lead; the domain is hosting trivia,
    and a tool that reports "domain: protonmail.com" as a real-world link is
    reporting noise with a confident face.
  * **Some pointers point the wrong way.** mail2tor.com is itself a hidden
    service. An address there is not a step out of the dark web, it is a step
    further in, and it must not be presented as a clearnet lead.
  * **Not every chain is traceable.** BTC, ETH and LTC have public explorers and
    a subpoena-able trail. Monero does not. Linking an XMR address to an
    "explorer" would imply a lookup that does not exist.

Nothing in this module feeds the attribution score, and tests/test_leads.py
asserts that by reading this file's source.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional, Sequence

__all__ = [
    "BANDS",
    "DARKNET_MAIL_HOSTS",
    "Lead",
    "SHARED_MAIL_HOSTS",
    "derive",
    "leads_for_personas",
]

#: Strongest first, so `BANDS.index()` orders them.
BANDS: tuple[str, ...] = ("STRONG", "MODERATE", "WEAK")

#: Mail providers that are themselves hidden services. An address here points
#: back into the dark web — it is not a real-world lead and must not be sold as
#: one. There is no operator to subpoena at a .onion mail host.
DARKNET_MAIL_HOSTS: frozenset[str] = frozenset({
    "mail2tor.com", "onionmail.org", "dnmx.org", "sonar.ws", "elude.in",
    "cock.li", "danwin1210.de", "torbox3uiot6wchz.onion",
})

#: Large privacy-mail providers. A real mailbox, but the domain identifies
#: nobody and the provider is specifically built to resist identifying its
#: users. Caps the band at MODERATE however many personas carry it.
SHARED_MAIL_HOSTS: frozenset[str] = frozenset({
    "protonmail.com", "proton.me", "pm.me", "tutanota.com", "tutanota.de",
    "tuta.io", "gmail.com", "outlook.com", "hotmail.com", "yahoo.com",
    "riseup.net", "disroot.org", "mailfence.com", "posteo.de", "yandex.com",
})

#: Public block explorers, for the chains that have one.
EXPLORERS: dict[str, str] = {
    "btc": "https://mempool.space/address/{value}",
    "eth": "https://etherscan.io/address/{value}",
    "ltc": "https://blockchair.com/litecoin/address/{value}",
}

#: How far each correlation signal actually goes, and why. The bands follow
#: recon/correlate.py's own framing: a banner-only match means two hosts run the
#: same software, and that is a coincidence "until a certificate, favicon or
#: ETag agrees with it". So banners are weak and the rest corroborate.
MATCH_STRENGTH: dict[str, tuple[str, str]] = {
    "onion_location": ("STRONG",
        "the clearnet site declares this onion itself, in a header or a meta "
        "tag — this is the operator's own statement, not an inference"),
    "tls_serial": ("STRONG",
        "one certificate, with one serial, served on both — certificates are "
        "issued to a named subscriber, so this is the strongest technical tie "
        "between the onion and this host"),
    "tls_san": ("STRONG",
        "the onion's own certificate names this host in its subject "
        "alternative names, which the operator had to configure deliberately"),
    "favicon": ("MODERATE",
        "the same custom favicon hash on both — distinctive, but a favicon is "
        "a public file and copying one is trivial"),
    "etag": ("MODERATE",
        "the same ETag on both, which usually encodes a file's inode and "
        "modification time — suggestive of one filesystem, but ETags collide "
        "and some proxies rewrite them"),
    "banner": ("WEAK",
        "only the server banner agrees, which means two hosts run the same "
        "software — the normal case on the internet, and not evidence until a "
        "certificate, favicon or ETag agrees with it"),
}

#: How each signal is written in prose. "An ETag match", not "An etag match".
MATCH_LABELS: dict[str, str] = {
    "onion_location": "Onion-Location",
    "tls_serial": "TLS certificate serial",
    "tls_san": "TLS certificate SAN",
    "favicon": "favicon hash",
    "etag": "ETag",
    "banner": "server banner",
}

#: A lead the actor published, versus one that belongs to a site they trade on.
#: Kept apart because conflating them is how a marketplace's hosting provider
#: ends up reading as a vendor's own infrastructure.
SCOPES: tuple[str, ...] = ("actor", "source")


def _article(word: str) -> str:
    """'a banner' / 'an ETag'. Small, but the panel is read by humans."""
    return "an" if word[:1].lower() in "aeiou" else "a"

_UID_ADDRESS = re.compile(r"<([^>]+@[^>]+)>")


@dataclass(frozen=True)
class Lead:
    """One pointer at the world outside Tor.

    `why` is how it was found. `caveat` is what it does not prove. Both are
    sentences, both are mandatory, and the UI prints them verbatim.
    """

    kind: str                       #: email | jabber | jabber_server | telegram
                                    #: | wallet | pgp_uid | clearnet_host
    value: str                      #: the thing to go and look at
    band: str                       #: STRONG | MODERATE | WEAK
    why: str
    caveat: str
    #: "actor" — the actor published this themselves.
    #: "source" — infrastructure behind a site they post on, which every other
    #: vendor on that site shares. Conflating the two would let a marketplace's
    #: hosting provider read as one vendor's own server.
    scope: str = "actor"
    personas: tuple[int, ...] = ()  #: which personas of this actor carry it
    url: Optional[str] = None       #: where to look it up, when such a place exists
    detail: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.band not in BANDS:
            raise ValueError(f"{self.band!r} is not one of {BANDS}")
        if self.scope not in SCOPES:
            raise ValueError(f"{self.scope!r} is not one of {SCOPES}")


def _corroboration(personas: Sequence[int]) -> tuple[str, bool]:
    """How many personas carry this, and whether that counts as corroborated.

    One persona claiming an address is a claim. The same address on two handles
    on two different sites is the thing worth acting on, and it is the only
    lever in this module that raises a band.
    """
    count = len(set(personas))
    if count >= 2:
        return f"carried by {count} personas of this actor", True
    return "carried by 1 persona", False


def _email_lead(row: Mapping) -> Optional[Lead]:
    value = str(row["value"])
    meta = row.get("meta") or {}
    domain = str(meta.get("domain") or value.rpartition("@")[2]).lower()
    personas = tuple(sorted(set(row.get("personas") or ())))
    corro, corroborated = _corroboration(personas)

    if domain in DARKNET_MAIL_HOSTS:
        return Lead(
            kind="email", value=value, band="WEAK",
            why=f"email address extracted from prose, {corro}",
            caveat=f"{domain} is itself a dark web mail service, so this points "
                   f"further into the dark web rather than out of it — there is "
                   f"no clearnet operator to approach for subscriber records",
            personas=personas, detail={"domain": domain, "provider": "darknet"},
        )

    if domain in SHARED_MAIL_HOSTS:
        return Lead(
            kind="email", value=value, band="MODERATE",
            why=f"email address extracted from prose, {corro}",
            caveat=f"the mailbox is the lead, not the domain — {domain} is a "
                   f"large privacy-focused provider shared by many thousands of "
                   f"users and built to resist identifying them",
            personas=personas, detail={"domain": domain, "provider": "shared"},
        )

    return Lead(
        kind="email", value=value,
        band="STRONG" if corroborated else "MODERATE",
        why=f"email address extracted from prose, {corro}",
        caveat=f"{domain} is not a known bulk provider, so the domain itself may "
               f"be worth a registration lookup — but an address appearing in a "
               f"post is a claim, not proof the actor controls the mailbox",
        personas=personas, detail={"domain": domain, "provider": "other"},
    )


def _jabber_leads(row: Mapping) -> list[Lead]:
    """Two leads: the account, and the clearnet host that runs it."""
    value = str(row["value"])
    meta = row.get("meta") or {}
    server = str(meta.get("domain") or value.rpartition("@")[2]).lower()
    personas = tuple(sorted(set(row.get("personas") or ())))
    corro, corroborated = _corroboration(personas)

    darknet = server in DARKNET_MAIL_HOSTS or server.endswith(".onion")

    account = Lead(
        kind="jabber", value=value,
        band="WEAK" if darknet else ("STRONG" if corroborated else "MODERATE"),
        why=f"XMPP address extracted from prose, {corro}",
        caveat=(f"{server} is a dark web service, so this does not leave Tor"
                if darknet else
                f"XMPP accounts are free and pseudonymous; {server} may keep no "
                f"records worth requesting, and many such servers advertise that"),
        personas=personas, detail={"server": server},
    )
    if darknet:
        return [account]

    host = Lead(
        kind="jabber_server", value=server, band="MODERATE",
        why=f"the clearnet host behind the XMPP address {value}",
        caveat="a shared server with many unrelated users — it identifies the "
               "operator's choice of provider, not the operator",
        personas=personas,
        url=f"https://{server}", detail={"account": value},
    )
    return [account, host]


def _wallet_lead(row: Mapping) -> Lead:
    chain = str(row["type"]).lower()
    value = str(row["value"])
    personas = tuple(sorted(set(row.get("personas") or ())))
    corro, corroborated = _corroboration(personas)

    template = EXPLORERS.get(chain)
    if template is None:
        # Monero, and anything else without a public address index.
        return Lead(
            kind="wallet", value=value, band="WEAK",
            why=f"{chain.upper()} address extracted from prose, {corro}",
            caveat="Monero has no public address lookup — balances and "
                   "counterparties are not visible to anyone, so this is only "
                   "useful if an exchange or seizure ties it to an account",
            personas=personas, url=None, detail={"chain": chain, "traceable": False},
        )

    return Lead(
        kind="wallet", value=value,
        band="STRONG" if corroborated else "MODERATE",
        why=f"{chain.upper()} address extracted from prose and checksum-validated, "
            f"{corro}",
        caveat="a public ledger shows the flow but not the holder; an exchange "
               "deposit or a KYC'd counterparty is what turns this into a name",
        personas=personas, url=template.format(value=value),
        detail={"chain": chain, "traceable": True},
    )


def _telegram_lead(row: Mapping) -> Lead:
    value = str(row["value"])
    handle = value.lstrip("@")
    personas = tuple(sorted(set(row.get("personas") or ())))
    corro, corroborated = _corroboration(personas)
    return Lead(
        kind="telegram", value=value,
        band="STRONG" if corroborated else "MODERATE",
        why=f"Telegram handle claimed in prose, {corro}",
        caveat="handles are claimed, not proven, and are freely reassigned after "
               "an account is deleted — confirm the account exists and predates "
               "the posts before relying on it",
        personas=personas, url=f"https://t.me/{handle}",
        detail={"handle": handle},
    )


def _pgp_lead(row: Mapping) -> Optional[Lead]:
    """The addresses inside a key's User ID packets."""
    meta = row.get("meta") or {}
    uids = [str(u) for u in (meta.get("uids") or []) if u]
    if not uids:
        return None

    personas = tuple(sorted(set(row.get("personas") or ())))
    corro, corroborated = _corroboration(personas)
    key_id = str(meta.get("key_id") or row["value"][-16:])

    addresses = [m.group(1) for uid in uids if (m := _UID_ADDRESS.search(uid))]
    shown = addresses[0] if addresses else uids[0]
    domain = shown.rpartition("@")[2].lower() if "@" in shown else ""

    shared = domain in SHARED_MAIL_HOSTS or domain in DARKNET_MAIL_HOSTS
    return Lead(
        kind="pgp_uid", value=shown,
        band="MODERATE" if (shared or not corroborated) else "STRONG",
        why=f"User ID packet on PGP key {key_id}, {corro}",
        caveat="a UID is self-asserted — the key owner typed it, nobody verified "
               "it, and it may name a persona rather than a person",
        personas=personas,
        detail={"key_id": key_id, "uids": uids, "domain": domain},
    )


def _host_lead(row: Mapping) -> Lead:
    """A clearnet host recon correlated with an onion this actor posts on.

    Scoped to the *source*, not the actor. This is the infrastructure behind a
    marketplace or forum, and every vendor on that site correlates with the same
    hosts — presenting it as one actor's own hosting would be the single most
    misleading thing this panel could do.
    """
    host = str(row["clearnet_host"])
    match = str(row.get("match_type") or "banner")
    band, reason = MATCH_STRENGTH.get(
        match, ("WEAK", f"{match} is not a signal this build knows how to weigh"))
    label = MATCH_LABELS.get(match, match)

    org = row.get("org")
    owner = f"{org} ({row['asn']})" if org and row.get("asn") else (org or "the host")

    return Lead(
        kind="clearnet_host", value=host, band=band, scope="source",
        why=f"{_article(label).capitalize()} {label} match ties this host to "
            f"{row.get('onion_url', 'an onion')} "
            f"(score {float(row.get('score') or 0):.2f})",
        caveat=f"{reason}. The real-world entity reachable here is {owner}, the "
               f"hosting provider — not the operator, and not this actor "
               f"specifically: this is infrastructure behind a site they post "
               f"on, which every other vendor on it shares",
        url=f"https://bgp.he.net/{row['asn']}" if row.get("asn") else None,
        detail={
            "ip": row.get("clearnet_ip"),
            "asn": row.get("asn"),
            "org": org,
            "country": row.get("country"),
            "match_type": match,
            "onion": row.get("onion_url"),
        },
    )


def derive(identifiers: Iterable[Mapping],
           correlations: Iterable[Mapping]) -> list[Lead]:
    """Build the lead list for one actor. Pure: no database, no network.

    Args:
        identifiers: rows with `type`, `value`, `meta` and `personas` (the ids
            of this actor's personas that carry the value).
        correlations: onion->clearnet rows with `clearnet_host`, `match_type`,
            `score` and, where recon recorded them, `asn`, `org`, `country`.

    Returns:
        Leads, strongest band first and stable within a band, so the panel does
        not reshuffle between reads.
    """
    leads: list[Lead] = []

    for row in identifiers:
        kind = str(row.get("type") or "").lower()

        if kind == "email":
            lead = _email_lead(row)
            if lead:
                leads.append(lead)
        elif kind == "jabber":
            leads.extend(_jabber_leads(row))
        elif kind in {"btc", "eth", "ltc", "xmr"}:
            leads.append(_wallet_lead(row))
        elif kind == "telegram":
            leads.append(_telegram_lead(row))
        elif kind == "pgp_fpr":
            lead = _pgp_lead(row)
            if lead:
                leads.append(lead)
        # session ids and onion mirrors are deliberately absent: both point at
        # services inside the dark web, and this panel is about what points out.

    for row in correlations:
        if row.get("clearnet_host"):
            leads.append(_host_lead(row))

    leads.sort(key=lambda lead: (SCOPES.index(lead.scope),
                                 BANDS.index(lead.band), lead.kind, lead.value))
    return leads


def leads_for_personas(session, persona_ids: Sequence[int]) -> list[Lead]:
    """`derive()` over what the database holds for one actor's personas.

    Correlations are matched by the onion URLs of the sources those personas
    post on: a clearnet host correlated with a marketplace is a lead about the
    marketplace's infrastructure, which is the honest framing — it is rarely a
    lead about one vendor on it, and the caveat on each host lead says so.
    """
    if not persona_ids:
        return []

    from sqlalchemy import select  # noqa: PLC0415

    from db import (  # noqa: PLC0415
        Identifier as IdentifierRow,
        InfraCorrelation,
        Persona,
        PersonaIdentifier,
        Source,
    )

    ids = list(persona_ids)

    carriers: dict[int, set[int]] = {}
    for persona_id, identifier_id in session.execute(
        select(PersonaIdentifier.persona_id, PersonaIdentifier.identifier_id)
        .where(PersonaIdentifier.persona_id.in_(ids))
    ).all():
        carriers.setdefault(identifier_id, set()).add(persona_id)

    identifiers: list[dict] = []
    if carriers:
        for row in session.execute(
            select(IdentifierRow).where(IdentifierRow.id.in_(list(carriers)))
        ).scalars():
            identifiers.append({
                "type": row.type,
                "value": row.value,
                "meta": row.meta or {},
                "personas": tuple(sorted(carriers.get(row.id, ()))),
            })

    onions = [
        url for (url,) in session.execute(
            select(Source.url).join(Persona, Persona.source_id == Source.id)
            .where(Persona.id.in_(ids), Source.is_onion.is_(True)).distinct()
        ).all() if url
    ]

    correlations: list[dict] = []
    if onions:
        for row in session.execute(
            select(InfraCorrelation).where(InfraCorrelation.onion_url.in_(onions))
        ).scalars():
            correlations.append({
                "onion_url": row.onion_url,
                "clearnet_host": row.clearnet_host,
                "clearnet_ip": row.clearnet_ip,
                "match_type": row.match_type,
                "score": row.score,
                "asn": getattr(row, "clearnet_asn", None),
                "org": getattr(row, "clearnet_org", None),
                "country": getattr(row, "clearnet_country", None),
            })

    # One host can correlate on several match types; keep the most decisive.
    best: dict[str, dict] = {}
    for row in correlations:
        host = row["clearnet_host"]
        current = best.get(host)
        if current is None or float(row["score"] or 0) > float(current["score"] or 0):
            best[host] = row

    return derive(identifiers, list(best.values()))
