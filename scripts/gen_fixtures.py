"""
gen_fixtures.py — build the synthetic demo corpus under fixtures/.

    python scripts/gen_fixtures.py

Deterministic: seeded from FIXTURE_SEED, so re-running produces a byte-identical
tree and the corpus can be regenerated after tuning a style profile.

What it produces:
  * 3 sources, 20 personas, 200 posts
  * four market_alpha vendors who reappear on market_gamma under new handles,
    sharing a PGP fingerprint or a wallet and writing in the same voice
  * two deliberate near-misses that stylometry should rate highly and the full
    attribution formula should still refuse to confirm
  * one persona below the 300-character stylometry floor, and one whose wallets
    fail their checksums, so the negative cases are testable
  * onion-side fingerprints and Shodan-shaped clearnet observations for the
    correlation demo
  * ground_truth.json — the answer key, which is never loaded into the database

Nothing here touches the network or the database.
"""

from __future__ import annotations

import hashlib
import json
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "legacy"))

from content_templates import (  # noqa: E402
    BEATS,
    FILLERS,
    FORUM_IDENTIFIER_KIND,
    FORUM_KINDS,
    MARKET_IDENTIFIER_KIND,
    MARKET_KINDS,
    PRODUCTS,
    STRUCTURES,
    TITLE_NOUNS,
    TITLES,
)
from obfuslex_engine import leet_decode  # noqa: E402  (v1 alias normalisation)
from style_profiles import PROFILES, check_profiles_distinct, render  # noqa: E402
from wallet_codec import (  # noqa: E402
    BTC_P2PKH_VERSION,
    LTC_P2PKH_VERSION,
    base58check_encode,
    bech32_encode,
    monero_address,
    onion_v3_address,
    self_test as codec_self_test,
    to_checksum_address,
    verify_btc_legacy,
    verify_eip55,
)

FIXTURE_SEED = 1337
OUT = ROOT / "fixtures"


# ─────────────────────────────────────────────────────────────────────────────
# Deterministic key material
# ─────────────────────────────────────────────────────────────────────────────

def det_bytes(label: str, length: int) -> bytes:
    """Reproducible pseudo-random bytes derived from a label."""
    out = b""
    counter = 0
    while len(out) < length:
        out += hashlib.sha256(f"{FIXTURE_SEED}:{label}:{counter}".encode()).digest()
        counter += 1
    return out[:length]


def pgp_fingerprint(label: str) -> str:
    return det_bytes(f"pgp:{label}", 20).hex().upper()


def btc_bech32(label: str) -> str:
    return bech32_encode("bc", 0, det_bytes(f"btc:{label}", 20))


def btc_legacy(label: str) -> str:
    return base58check_encode(BTC_P2PKH_VERSION, det_bytes(f"btcl:{label}", 20))


def ltc_legacy(label: str) -> str:
    return base58check_encode(LTC_P2PKH_VERSION, det_bytes(f"ltc:{label}", 20))


def eth_address(label: str) -> str:
    return to_checksum_address("0x" + det_bytes(f"eth:{label}", 20).hex())


def xmr_address(label: str) -> str:
    return monero_address(det_bytes(f"xmr-s:{label}", 32), det_bytes(f"xmr-v:{label}", 32))


def session_id(label: str) -> str:
    return "05" + det_bytes(f"session:{label}", 32).hex()


def onion(label: str) -> str:
    return onion_v3_address(det_bytes(f"onion:{label}", 32))


def corrupt_base58(address: str) -> str:
    """Change one character so the base58check checksum no longer matches."""
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    index = len(address) // 2
    current = address[index]
    replacement = alphabet[(alphabet.index(current) + 1) % len(alphabet)]
    return address[:index] + replacement + address[index + 1:]


def corrupt_eip55(address: str) -> str:
    """Flip the case of one letter so the EIP-55 checksum no longer matches."""
    for i, ch in enumerate(address[2:], start=2):
        if ch.isalpha():
            return address[:i] + ch.swapcase() + address[i + 1:]
    raise ValueError("address has no alphabetic character to flip")


# ─────────────────────────────────────────────────────────────────────────────
# Identifier catalogue
#
# Shared values are what make the four migrations findable. Everything else is
# unique to one persona.
# ─────────────────────────────────────────────────────────────────────────────

BAD_BTC = corrupt_base58(btc_legacy("cryovault"))
BAD_ETH = corrupt_eip55(eth_address("cryovault"))

IDS = {
    # actor_001 — Dread
    "PGP_A1":       pgp_fingerprint("dread"),
    "BTC_A1_ALPHA": btc_bech32("dread-alpha"),
    "BTC_A1_GAMMA": btc_bech32("dread-gamma"),
    "JABBER_A1":    "dreadpirate@jabber.calyxinstitute.net",
    "TG_A1":        "@dread_fam",
    "SESSION_A1":   session_id("dread"),

    # actor_002 — Nordic
    "PGP_A2":       pgp_fingerprint("nordic"),
    "BTC_A2":       btc_legacy("nordic"),
    "ETH_A2":       eth_address("nordic-gamma"),
    "EMAIL_A2":     "nordicpharm.supply@protonmail.com",
    "TG_A2":        "@nordic_supply",

    # actor_003 — Vector
    "PGP_A3":       pgp_fingerprint("vector"),
    "ETH_A3":       eth_address("vector"),
    "LTC_A3":       ltc_legacy("vector-gamma"),
    "MIRROR_A3":    onion("vector-mirror"),

    # actor_004 — Silk
    "XMR_A4":       xmr_address("silk"),
    "TG_A4":        "@silkhands",
    "BTC_A4_GAMMA": btc_bech32("silk-gamma"),

    # singletons
    "BTC_P5":       btc_bech32("quietcourier"),
    "EMAIL_P5":     "quiet.courier@tutanota.com",
    "PGP_P6":       pgp_fingerprint("obsidian"),
    "XMR_P6":       xmr_address("obsidian"),
    "EMAIL_P8":     "cryovault@protonmail.com",
    "BAD_BTC_P8":   BAD_BTC,
    "BAD_ETH_P8":   BAD_ETH,
    "PGP_P11":      pgp_fingerprint("hexweaver"),
    "SESSION_P11":  session_id("hexweaver"),
    "BTC_P12":      btc_legacy("mtlbroker"),
    "EMAIL_P12":    "mtl.broker@mail2tor.com",
    "TG_P13":       "@zerocool99",
    "ETH_P13":      eth_address("zerocool"),
    "JABBER_P14":   "graypigeon@xmpp.jp",
    "BTC_P15":      btc_bech32("plainbagel"),
    "TG_P15":       "@plain_bagel",
    "PGP_P20":      pgp_fingerprint("atlasmeds"),
    "BTC_P20":      btc_legacy("atlasmeds"),
    "EMAIL_P20":    "atlas.meds@protonmail.com",
}


# ─────────────────────────────────────────────────────────────────────────────
# Sources
# ─────────────────────────────────────────────────────────────────────────────

SOURCES = [
    {
        "id": 1,
        "name": "market_alpha",
        "url": f"http://{onion('market_alpha')}",
        "kind": "market",
        "is_onion": True,
        "reliability": 0.72,
        "first_seen": "2025-08-20T00:00:00",
        "last_scan_at": "2026-03-18T09:00:00",
        "active": True,
    },
    {
        "id": 2,
        "name": "forum_beta",
        "url": f"http://{onion('forum_beta')}",
        "kind": "forum",
        "is_onion": True,
        "reliability": 0.61,
        "first_seen": "2025-09-14T00:00:00",
        "last_scan_at": "2026-08-22T09:00:00",
        "active": True,
    },
    {
        "id": 3,
        "name": "market_gamma",
        "url": f"http://{onion('market_gamma')}",
        "kind": "market",
        "is_onion": True,
        "reliability": 0.68,
        "first_seen": "2026-02-25T00:00:00",
        "last_scan_at": "2026-08-27T09:00:00",
        "active": True,
    },
]

SOURCE_BY_NAME = {s["name"]: s for s in SOURCES}


# ─────────────────────────────────────────────────────────────────────────────
# The cast
#
# `ident` entries are (type, catalogue key). A value appearing under two personas
# is the evidence that links them.
# ─────────────────────────────────────────────────────────────────────────────

WINDOWS = {
    "market_alpha": ("2025-09-01", "2026-03-08"),
    "forum_beta":   ("2025-10-01", "2026-08-20"),
    "market_gamma": ("2026-03-01", "2026-08-25"),
}

CAST = [
    # id, source, handle, actor, trust, posts, identifiers
    (1,  "market_alpha", "Dr3adPirat3",   "actor_001", 4.7, 11,
     [("pgp_fpr", "PGP_A1"), ("btc", "BTC_A1_ALPHA"), ("jabber", "JABBER_A1")]),
    (2,  "market_alpha", "NordicPharm",   "actor_002", 4.9, 11,
     [("pgp_fpr", "PGP_A2"), ("btc", "BTC_A2"), ("email", "EMAIL_A2")]),
    (3,  "market_alpha", "Vect0rShop",    "actor_003", 4.4, 11,
     [("pgp_fpr", "PGP_A3"), ("eth", "ETH_A3"), ("onion_mirror", "MIRROR_A3")]),
    (4,  "market_alpha", "silk_hands",    "actor_004", 4.6, 11,
     [("xmr", "XMR_A4"), ("telegram", "TG_A4")]),
    (5,  "market_alpha", "QuietCourier",  "actor_005", 4.2, 11,
     [("btc", "BTC_P5"), ("email", "EMAIL_P5")]),
    (6,  "market_alpha", "ObsidianLab",   "actor_006", 4.8, 11,
     [("pgp_fpr", "PGP_P6"), ("xmr", "XMR_P6")]),
    (7,  "market_alpha", "paperghost",    "actor_007", 3.1, 2, []),
    (8,  "market_alpha", "CryoVault",     "actor_008", 4.0, 11,
     [("email", "EMAIL_P8"), ("btc", "BAD_BTC_P8"), ("eth", "BAD_ETH_P8")]),

    (9,  "forum_beta",   "Dread_P1rate",  "actor_001", None, 10,
     [("jabber", "JABBER_A1"), ("telegram", "TG_A1")]),
    (10, "forum_beta",   "nordic_pharm",  "actor_002", None, 10,
     [("email", "EMAIL_A2"), ("telegram", "TG_A2")]),
    (11, "forum_beta",   "hexweaver",     "actor_010", None, 10,
     [("pgp_fpr", "PGP_P11"), ("session", "SESSION_P11")]),
    (12, "forum_beta",   "mtl_broker",    "actor_011", None, 10,
     [("btc", "BTC_P12"), ("email", "EMAIL_P12")]),
    (13, "forum_beta",   "zeroCool99",    "actor_012", None, 10,
     [("telegram", "TG_P13"), ("eth", "ETH_P13")]),
    (14, "forum_beta",   "graypigeon",    "actor_013", None, 10,
     [("jabber", "JABBER_P14")]),
    (15, "forum_beta",   "plainbagel",    "actor_014", None, 10,
     [("btc", "BTC_P15"), ("telegram", "TG_P15")]),

    (16, "market_gamma", "BlackSailsRX",  "actor_001", 4.5, 11,
     [("pgp_fpr", "PGP_A1"), ("btc", "BTC_A1_GAMMA"), ("session", "SESSION_A1")]),
    (17, "market_gamma", "NordPharmaEU",  "actor_002", 4.8, 10,
     [("btc", "BTC_A2"), ("eth", "ETH_A2")]),
    (18, "market_gamma", "V3ct0r_Supply", "actor_003", 4.3, 10,
     [("pgp_fpr", "PGP_A3"), ("ltc", "LTC_A3"), ("onion_mirror", "MIRROR_A3")]),
    (19, "market_gamma", "SilkHands",     "actor_004", 4.6, 10,
     [("xmr", "XMR_A4"), ("btc", "BTC_A4_GAMMA")]),
    (20, "market_gamma", "AtlasMeds",     "actor_009", 4.1, 10,
     [("pgp_fpr", "PGP_P20"), ("btc", "BTC_P20"), ("email", "EMAIL_P20")]),
]

#: Actors who sell on at least one market. Their forum personas write like
#: vendors rather than like buyers.
VENDOR_ACTORS = frozenset(
    actor for _, source, _, actor, _, _, _ in CAST
    if SOURCE_BY_NAME[source]["kind"] == "market"
)

#: Identifiers that fail their checksum. The loader must drop these rather than
#: store them (CLAUDE.md), and Phase 1's validator must reject them.
INVALID_IDENTIFIERS = {"BAD_BTC_P8", "BAD_ETH_P8"}

ACTOR_LABELS = {
    "actor_001": "Dread (market_alpha → market_gamma)",
    "actor_002": "Nordic (market_alpha → market_gamma)",
    "actor_003": "Vector (market_alpha → market_gamma)",
    "actor_004": "Silk (market_alpha → market_gamma)",
    "actor_005": "QuietCourier",
    "actor_006": "ObsidianLab",
    "actor_007": "paperghost",
    "actor_008": "CryoVault",
    "actor_009": "AtlasMeds",
    "actor_010": "hexweaver",
    "actor_011": "mtl_broker",
    "actor_012": "zeroCool99",
    "actor_013": "graypigeon",
    "actor_014": "plainbagel",
}


# ─────────────────────────────────────────────────────────────────────────────
# Phrasing preference
#
# A real author reuses their own turns of phrase. Each actor gets a private
# ordering over every beat's phrasings and samples from it with a decay, so two
# personas of one actor gravitate to the same wordings without ever repeating a
# fixed frame. Phrasings containing the actor's trade vocabulary are boosted.
# ─────────────────────────────────────────────────────────────────────────────

_preference_cache: dict[tuple[str, str], list[int]] = {}


def _preference(actor: str, beat: str) -> list[int]:
    cached = _preference_cache.get((actor, beat))
    if cached is None:
        order = list(range(len(BEATS[beat])))
        random.Random(f"{FIXTURE_SEED}:{actor}:{beat}").shuffle(order)
        _preference_cache[(actor, beat)] = order
        cached = order
    return cached


def pick_phrasing(actor: str, beat: str, rng: random.Random,
                  used: set[str] | None = None) -> str:
    """Choose one phrasing for a beat, favouring this actor's habitual wordings.

    `used` holds the phrasings already spent on the current post; a structure
    that visits the same beat twice must not print the same sentence twice.
    """
    options = BEATS[beat]
    order = _preference(actor, beat)
    vocab = PROFILES[actor].trade_vocab

    available = [i for i in order if used is None or options[i] not in used]
    if not available:
        available = list(order)

    weights = []
    for rank, index in enumerate(available):
        weight = 0.55 ** rank
        if any(term.lower() in options[index].lower() for term in vocab):
            weight *= 3.0
        weights.append(weight)

    chosen = options[rng.choices(available, weights=weights, k=1)[0]]
    if used is not None:
        used.add(chosen)
    return chosen


# ─────────────────────────────────────────────────────────────────────────────
# Post assembly
# ─────────────────────────────────────────────────────────────────────────────

def _slot_values(persona: dict, rng: random.Random) -> dict[str, str]:
    category = persona["category"]
    products = PRODUCTS.get(category, PRODUCTS["drugs"])
    values = {
        "product": rng.choice(products),
        "qty": rng.choice(FILLERS["qty"]),
        "price": rng.choice(FILLERS["price"]),
        "coin": rng.choice(FILLERS["coin"]),
        "origin": rng.choice(FILLERS["origin"]),
        "days": rng.choice(FILLERS["days"]),
        "stealth": rng.choice(FILLERS["stealth"]),
        "percent": rng.choice(FILLERS["percent"]),
        "market": rng.choice(FILLERS["market"]),
    }
    # a persona with no published contact address simply has no contact beat;
    # the missing slot drops it rather than printing "reach me at market messages"
    if persona["_channel"]:
        values["channel"] = persona["_channel"]
    if persona["_pgp"]:
        values["pgp"] = persona["_pgp"]
    if persona["_wallet"]:
        values["wallet"] = persona["_wallet"]
    return values


def _build_sentences(persona: dict, structure: tuple[str, ...],
                     rng: random.Random) -> list[str]:
    values = _slot_values(persona, rng)
    sentences = []
    used: set[str] = set()
    for beat in structure:
        template = pick_phrasing(persona["actor"], beat, rng, used)
        try:
            sentences.append(template.format(**values))
        except KeyError:
            # the persona has no value for a slot this phrasing needs (no PGP
            # key, no published wallet, no contact address) — drop the beat
            # rather than emit a hole or a placeholder
            continue
    return sentences


def _weighted_kind(kinds: tuple[tuple[str, int], ...], rng: random.Random) -> str:
    names = [name for name, _ in kinds]
    weights = [weight for _, weight in kinds]
    return rng.choices(names, weights=weights, k=1)[0]


def _timestamp(persona: dict, rng: random.Random) -> datetime:
    start, end = (datetime.fromisoformat(d) for d in WINDOWS[persona["source"]])
    span = int((end - start).total_seconds())
    moment = start + timedelta(seconds=rng.randrange(span))
    profile = PROFILES[persona["actor"]]
    # 85% of activity inside the author's usual hours; the rest is noise, so the
    # histogram is a real signal rather than a perfect fingerprint
    hour = rng.choice(profile.hours) if rng.random() < 0.85 else rng.randrange(24)
    return moment.replace(hour=hour, minute=rng.randrange(60),
                          second=rng.randrange(60), microsecond=0)


#: Personas whose whole purpose is to have too little text to score. Written out
#: rather than generated, because the point is the absence of a corpus: an almost
#: dormant account with a couple of one-line notices. Phase 2's stylometry must
#: return None for these rather than a confident number from 90 characters.
MINIMAL_POSTS: dict[int, list[tuple[str, str]]] = {
    7: [
        ("first listings soon", "setting up shop. first listings go live next week."),
        ("still setting up", "still getting the profile sorted. two more days."),
    ],
}


def build_posts(persona: dict, rng: random.Random) -> list[dict]:
    profile = PROFILES[persona["actor"]]
    is_market = SOURCE_BY_NAME[persona["source"]]["kind"] == "market"

    if persona["id"] in MINIMAL_POSTS:
        posts = [
            {
                "kind": "listing",
                "title": title,
                "body": body,
                "category": persona["category"],
                "posted_at": _timestamp(persona, rng),
            }
            for title, body in MINIMAL_POSTS[persona["id"]]
        ]
        posts.sort(key=lambda p: p["posted_at"])
        return posts

    kinds = MARKET_KINDS if is_market else FORUM_KINDS
    identifier_kind = MARKET_IDENTIFIER_KIND if is_market else FORUM_IDENTIFIER_KIND

    # A vendor who also has a forum account talks about their own operation
    # there — shipping, escrow, stealth — not only about other people's markets.
    # Without that overlap a vendor's market listings and their forum replies
    # share almost no vocabulary, and the two personas end up looking like
    # different writers for reasons that have nothing to do with style.
    if is_market:
        mix_rate = 0.0
    else:
        mix_rate = 0.4 if persona["actor"] in VENDOR_ACTORS else 0.15

    plan = []
    for _ in range(persona["post_count"]):
        pool = MARKET_KINDS if (mix_rate and rng.random() < mix_rate) else kinds
        plan.append(_weighted_kind(pool, rng))

    if persona["identifiers"] and persona["post_count"] >= 3:
        plan[rng.randrange(len(plan))] = identifier_kind

    posts = []
    seen: set[str] = set()
    for kind in plan:
        # A persona posting the same text twice is a generator artefact, and
        # posts is uniquely indexed on (persona_id, body_hash), so the loader
        # would reject the second copy. Re-roll the structure and the phrasings
        # until the body is new.
        for attempt in range(12):
            structure = rng.choice(STRUCTURES[kind])
            sentences = _build_sentences(persona, structure, rng)
            if not sentences:
                structure = STRUCTURES["listing" if is_market else "opsec_tip"][0]
                sentences = _build_sentences(persona, structure, rng)

            body = render(sentences, profile, rng, title_nouns=TITLE_NOUNS,
                          verbatim=persona["_verbatim"])
            if body not in seen:
                break
        else:
            raise RuntimeError(
                f"persona {persona['id']} ({persona['handle']}) cannot produce a "
                f"distinct {kind} post after 12 attempts — the beat pool for this "
                f"kind is too small for {persona['post_count']} posts"
            )
        seen.add(body)

        title_template = rng.choice(TITLES[kind])
        title = title_template.format(**_slot_values(persona, rng))

        posts.append({
            "kind": kind,
            "title": title,
            "body": body,
            "category": persona["category"],
            "posted_at": _timestamp(persona, rng),
        })

    posts.sort(key=lambda p: p["posted_at"])
    return posts


def build_bio(persona: dict, rng: random.Random) -> str:
    profile = PROFILES[persona["actor"]]
    if persona["id"] == 7:
        return "no listings yet"

    sentences = [
        rng.choice([
            "vendor here since 2023 and every order has shipped",
            "trading since 2022, mostly repeat customers",
            "active on three markets, this is the main one",
            "small operation, i answer my own messages",
        ]),
    ]
    used: set[str] = set()
    for beat in ("pgp_notice", "wallet_notice", "contact"):
        template = pick_phrasing(persona["actor"], beat, rng, used)
        try:
            sentences.append(template.format(**_slot_values(persona, rng)))
        except KeyError:
            continue
    return render(sentences, profile, rng, title_nouns=TITLE_NOUNS,
                  verbatim=persona["_verbatim"])


# ─────────────────────────────────────────────────────────────────────────────
# Recon fixtures
# ─────────────────────────────────────────────────────────────────────────────

SHARED_FAVICON = "-1274392844"   # market_alpha and market_gamma serve the same favicon
GAMMA_TLS_SERIAL = "0F3A9C1D77B54E2A"
ALPHA_ETAG = 'W/"5f2a1c-1b4e"'


def build_infra_findings() -> list[dict]:
    alpha, beta, gamma = SOURCES[0], SOURCES[1], SOURCES[2]
    return [
        {
            "source_id": alpha["id"],
            "onion_url": alpha["url"],
            "server_banner": "nginx/1.18.0 (Ubuntu)",
            "powered_by": "PHP/7.4.33",
            "etag": ALPHA_ETAG,
            "favicon_hash": SHARED_FAVICON,
            "status_exposed": True,
            "default_page": False,
            "dir_listing": False,
            "tls_subject": None,
            "tls_issuer": None,
            "tls_serial": None,
            "tls_sans": None,
            "tls_not_before": None,
            "robots_txt": "User-agent: *\nDisallow: /admin\nDisallow: /backup\n",
            "sitemap_xml": None,
            "html_comments": ["<!-- build 2024.11 staging -->",
                              "<!-- TODO: remove cdn reference before launch -->"],
            "generator_meta": None,
            "clearnet_refs": ["https://cdn-static-eu.hostvault.net/assets/app.css"],
            "headers": {
                "Server": "nginx/1.18.0 (Ubuntu)",
                "X-Powered-By": "PHP/7.4.33",
                "ETag": ALPHA_ETAG,
                "Content-Type": "text/html; charset=UTF-8",
            },
            "misconfig_score": 0.72,
            "scanned_at": "2026-03-18T09:04:11",
        },
        {
            "source_id": beta["id"],
            "onion_url": beta["url"],
            "server_banner": "Apache/2.4.41 (Debian)",
            "powered_by": None,
            "etag": 'W/"1a03d2-9f1"',
            "favicon_hash": "1893450732",
            "status_exposed": False,
            "default_page": False,
            "dir_listing": True,
            "tls_subject": None,
            "tls_issuer": None,
            "tls_serial": None,
            "tls_sans": None,
            "tls_not_before": None,
            "robots_txt": "User-agent: *\nDisallow:\n",
            "sitemap_xml": None,
            "html_comments": ["<!-- powered by a forum script, v3.2 -->"],
            "generator_meta": "phpBB 3.2.11",
            "clearnet_refs": [],
            "headers": {
                "Server": "Apache/2.4.41 (Debian)",
                "ETag": 'W/"1a03d2-9f1"',
                "Content-Type": "text/html; charset=UTF-8",
            },
            "misconfig_score": 0.41,
            "scanned_at": "2026-08-22T09:12:40",
        },
        {
            "source_id": gamma["id"],
            "onion_url": gamma["url"],
            "server_banner": "nginx/1.18.0 (Ubuntu)",
            "powered_by": "PHP/7.4.33",
            "etag": 'W/"7c8b02-2d19"',
            # same favicon as market_alpha: the infrastructure evidence that the
            # same operator runs both markets
            "favicon_hash": SHARED_FAVICON,
            "status_exposed": True,
            "default_page": False,
            "dir_listing": False,
            "tls_subject": "CN=gamma-mirror.hostvault.net",
            "tls_issuer": "C=US, O=Let's Encrypt, CN=R3",
            "tls_serial": GAMMA_TLS_SERIAL,
            "tls_sans": ["gamma-mirror.hostvault.net", "www.gamma-mirror.hostvault.net"],
            "tls_not_before": "2026-02-14T00:00:00",
            "robots_txt": "User-agent: *\nDisallow: /admin\nDisallow: /backup\n",
            "sitemap_xml": "<?xml version=\"1.0\"?><urlset><url><loc>/listings</loc></url></urlset>",
            "html_comments": ["<!-- build 2024.11 staging -->"],
            "generator_meta": None,
            "clearnet_refs": ["https://cdn-static-eu.hostvault.net/assets/app.css",
                              "https://gamma-mirror.hostvault.net/status"],
            "headers": {
                "Server": "nginx/1.18.0 (Ubuntu)",
                "X-Powered-By": "PHP/7.4.33",
                "ETag": 'W/"7c8b02-2d19"',
                "Content-Type": "text/html; charset=UTF-8",
            },
            "misconfig_score": 0.68,
            "scanned_at": "2026-08-27T09:07:52",
        },
    ]


def build_clearnet_observations() -> list[dict]:
    """Shodan-shaped host records.

    Three of these correlate with the onion fingerprints (favicon hash, TLS
    serial, ETag), one is a banner-only coincidence that should score low, and
    the rest are noise so the correlator has to discriminate.
    """
    def record(ip, hostnames, port, product, version, favicon, etag,
               serial=None, cn=None, sans=None, org="HostVault B.V.",
               asn="AS201814", country="NL", city="Amsterdam"):
        http = {
            "server": f"{product}/{version}",
            "title": "Index",
            "favicon": {"hash": favicon, "location": "/favicon.ico"},
        }
        headers = {"Server": f"{product}/{version}"}
        if etag:
            headers["ETag"] = etag
        rec = {
            "ip_str": ip,
            "hostnames": hostnames,
            "port": port,
            "transport": "tcp",
            "product": product,
            "version": version,
            "timestamp": "2026-08-19T04:22:07.114000",
            "asn": asn,
            "org": org,
            "location": {"country_code": country, "city": city},
            "http": http,
            "headers": headers,
        }
        if serial:
            rec["ssl"] = {
                "cert": {
                    "serial": serial,
                    "subject": {"CN": cn},
                    "issuer": {"C": "US", "O": "Let's Encrypt", "CN": "R3"},
                    "expired": False,
                    "fingerprint": {"sha256": det_bytes(f"cert:{serial}", 32).hex()},
                },
                "versions": ["TLSv1.2", "TLSv1.3"],
                "sans": sans or [],
            }
        return rec

    return [
        # strong: same favicon hash as market_alpha and market_gamma
        record("185.212.44.19", ["cdn-static-eu.hostvault.net"], 443, "nginx", "1.18.0",
               SHARED_FAVICON, 'W/"5f2a1c-1b4e"',
               serial="4B12FE0099AA31C7", cn="cdn-static-eu.hostvault.net",
               sans=["cdn-static-eu.hostvault.net"]),
        # strong: certificate serial matches market_gamma's
        record("185.212.44.23", ["gamma-mirror.hostvault.net"], 443, "nginx", "1.18.0",
               "884321197", None,
               serial=GAMMA_TLS_SERIAL, cn="gamma-mirror.hostvault.net",
               sans=["gamma-mirror.hostvault.net", "www.gamma-mirror.hostvault.net"]),
        # medium: ETag matches market_alpha
        record("185.212.44.31", ["staging.hostvault.net"], 80, "nginx", "1.18.0",
               "1120043377", ALPHA_ETAG),
        # weak: banner only — a very common one, so this must score low
        record("91.219.238.7", ["web07.cheaphost.example"], 80, "nginx", "1.18.0",
               "-733991204", 'W/"aa10b2-4c8"', org="CheapHost Ltd", asn="AS49505",
               country="RU", city="Moscow"),
        # noise
        record("203.0.113.14", ["mail.example-corp.net"], 25, "Postfix", "3.4.13",
               "0", None, org="Example Corp", asn="AS64500", country="DE", city="Berlin"),
        record("198.51.100.77", ["shop.retailer.example"], 443, "Apache", "2.4.52",
               "1993451209", 'W/"77d2a-1f0"', serial="7C09AB4411FE2200",
               cn="shop.retailer.example", org="Retailer SA", asn="AS64501",
               country="FR", city="Paris"),
        record("203.0.113.90", ["api.fintech.example"], 443, "nginx", "1.24.0",
               "556123409", None, serial="119ADE7723BC0041", cn="api.fintech.example",
               org="Fintech Oy", asn="AS64502", country="FI", city="Helsinki"),
        record("192.0.2.55", ["files.university.example"], 21, "vsftpd", "3.0.3",
               "0", None, org="University", asn="AS64503", country="SE", city="Uppsala"),
        record("198.51.100.201", ["blog.personal.example"], 80, "Apache", "2.4.41",
               "-1002233114", 'W/"3b21-88a"', org="Static Hosting", asn="AS64504",
               country="US", city="Dallas"),
        record("203.0.113.240", ["cache.mediacdn.example"], 443, "nginx", "1.20.2",
               "742119008", None, serial="55DE01AA7734BC19", cn="cache.mediacdn.example",
               org="MediaCDN", asn="AS64505", country="IE", city="Dublin"),
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Assembly
# ─────────────────────────────────────────────────────────────────────────────

def build_personas() -> list[dict]:
    personas = []
    for pid, source, handle, actor, trust, post_count, idents in CAST:
        profile = PROFILES[actor]
        resolved = []
        for kind, key in idents:
            resolved.append({
                "type": kind,
                "value": IDS[key],
                "valid": key not in INVALID_IDENTIFIERS,
                "catalogue_key": key,
            })

        pgp = next((i["value"] for i in resolved
                    if i["type"] == "pgp_fpr" and i["valid"]), None)
        wallet = next((i["value"] for i in resolved
                       if i["type"] in {"btc", "eth", "xmr", "ltc"} and i["valid"]), None)
        channel = next((i["value"] for i in resolved
                        if i["type"] in {"jabber", "telegram", "email", "session"}
                        and i["valid"]), None)

        personas.append({
            "id": pid,
            "key": f"{source}/{handle}",
            "source": source,
            "source_id": SOURCE_BY_NAME[source]["id"],
            "handle": handle,
            "actor": actor,
            "category": profile.categories[0],
            "trust_score": trust,
            "post_count": post_count,
            "identifiers": resolved,
            "_pgp": pgp,
            "_wallet": wallet,
            "_channel": channel,
            # identifiers must survive rendering byte-for-byte
            "_verbatim": tuple(i["value"] for i in resolved),
        })
    return personas


def build_corpus() -> tuple[list[dict], list[dict]]:
    rng = random.Random(FIXTURE_SEED)
    personas = build_personas()

    all_posts: list[dict] = []
    post_id = 1
    for persona in personas:
        source = SOURCE_BY_NAME[persona["source"]]
        base = f"{source['url']}/{'vendor' if source['kind'] == 'market' else 'user'}/{persona['handle']}"
        persona["profile_url"] = base
        persona["bio"] = build_bio(persona, rng)

        posts = build_posts(persona, rng)
        for post in posts:
            all_posts.append({
                "id": post_id,
                "persona_id": persona["id"],
                "source_id": persona["source_id"],
                "url": f"{base}/post/{post_id}",
                "title": post["title"],
                "body": post["body"],
                "category": post["category"],
                "posted_at": post["posted_at"].isoformat(),
            })
            post_id += 1

        persona["first_seen"] = posts[0]["posted_at"].isoformat()
        persona["last_seen"] = posts[-1]["posted_at"].isoformat()
        persona["last_scan_at"] = source["last_scan_at"]

        for identifier in persona["identifiers"]:
            identifier["context"] = _context_for(identifier["value"], persona, all_posts)

        for private in ("_pgp", "_wallet", "_channel", "_verbatim"):
            persona.pop(private)

    return personas, all_posts


def _context_for(value: str, persona: dict, posts: list[dict]) -> str:
    """A snippet of the text the identifier was observed in."""
    haystacks = [persona["bio"]] + [
        p["body"] for p in posts if p["persona_id"] == persona["id"]
    ]
    for text in haystacks:
        index = text.find(value)
        if index >= 0:
            start = max(0, index - 45)
            end = min(len(text), index + len(value) + 45)
            return ("..." if start else "") + text[start:end] + ("..." if end < len(text) else "")
    return f"declared on the {persona['source']} profile of {persona['handle']}"


def build_ground_truth(personas: list[dict]) -> dict:
    by_actor: dict[str, list[int]] = {}
    for persona in personas:
        by_actor.setdefault(persona["actor"], []).append(persona["id"])

    positive_pairs = []
    for members in by_actor.values():
        for i, a in enumerate(sorted(members)):
            for b in sorted(members)[i + 1:]:
                positive_pairs.append([a, b])
    positive_pairs.sort()

    return {
        "note": ("Answer key for scripts/evaluate.py. Never loaded into the "
                 "database — personas.actor_id stays NULL after load_fixtures so "
                 "the resolver has to derive these clusters itself."),
        "generated_by": "scripts/gen_fixtures.py",
        "seed": FIXTURE_SEED,
        "actor_count": len(by_actor),
        "persona_count": len(personas),
        "actors": {
            actor: {
                "label": ACTOR_LABELS[actor],
                "personas": sorted(members),
                "sources": sorted({p["source"] for p in personas
                                   if p["actor"] == actor}),
                "migration": ("market_alpha -> market_gamma"
                              if len(members) > 1 else None),
            }
            for actor, members in sorted(by_actor.items())
        },
        "persona_to_actor": {str(p["id"]): p["actor"] for p in personas},
        "personas": {
            str(p["id"]): {
                "key": p["key"],
                "source": p["source"],
                "handle": p["handle"],
                "handle_normalized": leet_decode(p["handle"]).replace(" ", ""),
            }
            for p in personas
        },
        "expected_positive_pairs": positive_pairs,
        "hard_negatives": [
            {
                "pair": [4, 15],
                "reason": ("silk_hands and plainbagel share a sentence-length band "
                           "and two discourse markers but no identifier, no "
                           "misspellings and no posting hours. Stylometry should "
                           "rate them highly; the full formula must not confirm."),
                "max_band": "POSSIBLE",
            },
            {
                "pair": [2, 20],
                "reason": ("NordicPharm and AtlasMeds share a formal register, a "
                           "greeting and a category. AtlasMeds is a genuinely new "
                           "vendor: different contractions, punctuation, "
                           "misspellings and posting hours."),
                "max_band": "POSSIBLE",
            },
        ],
        "refusal_cases": [
            {
                "persona_id": 7,
                "reason": ("paperghost has under 300 characters of text in total, "
                           "so stylometry must return None rather than a score."),
            },
            {
                "persona_id": 8,
                "reason": ("CryoVault publishes a BTC address with a broken "
                           "base58check checksum and an ETH address with a broken "
                           "EIP-55 checksum. Both must be dropped, not stored."),
                "dropped_values": sorted(IDS[k] for k in INVALID_IDENTIFIERS),
            },
        ],
        "notes_on_evidence": {
            "actor_001": ("1<->16 share a PGP fingerprint; 1<->9 share a jabber id "
                          "and a normalised handle; 9<->16 share nothing hard and "
                          "must be resolved through the cluster."),
            "actor_002": ("2<->17 share a BTC wallet; 2<->10 share an email and a "
                          "normalised handle; 10<->17 share nothing hard."),
            "actor_003": "3<->18 share a PGP fingerprint and a mirror onion.",
            "actor_004": "4<->19 share an XMR wallet and a normalised handle.",
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Self-checks — the corpus must actually have the properties it claims
# ─────────────────────────────────────────────────────────────────────────────

def _normalise(handle: str) -> str:
    return leet_decode(handle).replace(" ", "")


def self_check(personas: list[dict], posts: list[dict]) -> None:
    codec_self_test()
    check_profiles_distinct()

    assert len(personas) == 20, f"expected 20 personas, got {len(personas)}"
    assert len(posts) == 200, f"expected 200 posts, got {len(posts)}"
    assert len({p["id"] for p in personas}) == 20, "persona ids are not unique"

    by_id = {p["id"]: p for p in personas}

    # the four migrations must share a real, checksum-valid identifier
    for alpha_id, gamma_id in ((1, 16), (2, 17), (3, 18), (4, 19)):
        alpha = {(i["type"], i["value"]) for i in by_id[alpha_id]["identifiers"] if i["valid"]}
        gamma = {(i["type"], i["value"]) for i in by_id[gamma_id]["identifiers"] if i["valid"]}
        shared = alpha & gamma
        assert shared, f"personas {alpha_id} and {gamma_id} share no identifier"
        assert any(t in {"pgp_fpr", "btc", "eth", "xmr", "ltc"} for t, _ in shared), (
            f"personas {alpha_id} and {gamma_id} share only soft identifiers: {shared}"
        )

    # intended handle collisions, and intended non-collisions
    for a, b in ((1, 9), (2, 10), (4, 19)):
        assert _normalise(by_id[a]["handle"]) == _normalise(by_id[b]["handle"]), (
            f"handles {by_id[a]['handle']} and {by_id[b]['handle']} should normalise alike"
        )
    for a, b in ((1, 16), (3, 18)):
        assert _normalise(by_id[a]["handle"]) != _normalise(by_id[b]["handle"]), (
            f"handles {by_id[a]['handle']} and {by_id[b]['handle']} must not collide — "
            f"this pair is meant to be found by evidence other than the handle"
        )

    # wallet checksums are real
    for persona in personas:
        for identifier in persona["identifiers"]:
            if identifier["type"] == "btc" and identifier["value"].startswith("1"):
                assert verify_btc_legacy(identifier["value"]) == identifier["valid"], (
                    f"BTC validity mismatch on persona {persona['id']}"
                )
            if identifier["type"] == "eth":
                assert verify_eip55(identifier["value"]) == identifier["valid"], (
                    f"ETH validity mismatch on persona {persona['id']}"
                )
    assert not verify_btc_legacy(IDS["BAD_BTC_P8"]), "the corrupted BTC address still validates"
    assert not verify_eip55(IDS["BAD_ETH_P8"]), "the corrupted ETH address still validates"

    # the stylometry floor: everyone who must be linked clears it, paperghost does not
    text_by_persona: dict[int, int] = {}
    for post in posts:
        text_by_persona[post["persona_id"]] = text_by_persona.get(post["persona_id"], 0) + len(post["body"])
    # every persona except the deliberate refusal case must be scoreable —
    # including the two near-misses, whose whole point is that stylometry rates
    # them highly and the full formula still declines to confirm them
    for persona in personas:
        pid = persona["id"]
        if pid in MINIMAL_POSTS:
            continue
        assert text_by_persona[pid] >= 300, (
            f"persona {pid} ({persona['handle']}) has only {text_by_persona[pid]} "
            f"chars — below the stylometry floor"
        )
    assert text_by_persona[7] < 300, (
        f"paperghost has {text_by_persona[7]} chars; the refusal case needs under 300"
    )

    # posts is uniquely indexed on (persona_id, body_hash): a persona repeating
    # itself verbatim would be rejected at load time
    seen: set[tuple[int, str]] = set()
    for post in posts:
        key = (post["persona_id"], post["body"])
        assert key not in seen, (
            f"persona {post['persona_id']} posts the same body twice; the loader "
            f"would reject the duplicate"
        )
        seen.add(key)

    # identifiers that must appear in prose, not only in the declared list
    for persona in personas:
        if not persona["identifiers"]:
            continue
        body_text = persona["bio"] + " ".join(
            p["body"] for p in posts if p["persona_id"] == persona["id"]
        )
        embedded = [i for i in persona["identifiers"] if i["value"] in body_text]
        assert embedded, (
            f"persona {persona['id']} publishes identifiers but none appear in its text; "
            f"Phase 1's extractor would have nothing to find"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Output
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# Buyer feedback (Phase 6)
#
# Written to fixtures/feedback.json ONLY. Nothing here touches personas, posts
# or the answer key, and it draws from its own RNG stream rather than the one
# build_corpus() threads through every persona in order — a single extra draw
# from that stream would shift every subsequent value and rewrite the whole
# corpus. scripts/check_fixture_hashes.py is the gate that proves it did not.
#
# BUYERS ARE NOT PERSONAS. They exist as a handle on a feedback row and never
# enter personas.json, so the link graph, the 190 pairs and every published
# figure are untouched by construction. A buyer is a counterparty, not a
# candidate identity: the whole premise of a feedback edge is that the two ends
# are different people, which is the opposite of what `links` asserts.
#
# LOYALTY IS A DIAL, AND THAT MATTERS FOR WHAT A MEASUREMENT MEANS. Buyers are
# drawn from a per-market pool; a small share follow a vendor across a
# migration. Any "does shared-buyer overlap separate true pairs" number
# measured on this corpus is therefore a measurement of CROSS_MARKET_LOYALTY
# below, not a discovery about darknet markets. Stylometry is different — the
# generator plants writing quirks and measuring recovers them, which genuinely
# tests the extractor. Here the generator plants the answer directly.
# ─────────────────────────────────────────────────────────────────────────────

#: Buyers per market. Small enough that vendors on one market share customers,
#: which is the realistic case and the one that makes shared-buyer overlap a
#: poor identity signal.
BUYER_POOL_SIZE = 18

#: Of a migrated vendor's feedback on the new market, roughly this share comes
#: from a buyer who also bought from them on the old one. Set deliberately low:
#: a vendor who moves market gets a new customer base.
CROSS_MARKET_LOYALTY = 0.15

FEEDBACK_PHRASES = (
    ("5", "landed in 3 days, packaging was discreet. will reorder."),
    ("5", "exactly as described. good comms throughout."),
    ("4", "slower than quoted but arrived intact. no complaints."),
    ("4", "product fine, shipping label was a bit obvious."),
    ("5", "second order from this vendor, same quality both times."),
    ("3", "took two weeks and needed a nudge. eventually sorted."),
    ("5", "fast, clean, answered every message. recommended."),
    ("4", "good stealth. would use again."),
    ("2", "dispute opened, vendor resolved it but it took a while."),
    ("5", "no issues at all. escrow released early."),
)


def _buyer_handles(rng: random.Random) -> dict[str, list[str]]:
    """A buyer pool per market. Handles are deliberately unlike any persona."""
    stems = (
        "quietcart", "nightporch", "bluefinch", "oakstep", "tinroof", "palegull",
        "shortwave", "drybrook", "eastvane", "coldpress", "lowtide", "runegate",
        "mossbank", "flintrow", "wireframe", "slateharbour", "duskline", "farrow",
        "irongate", "pinewharf", "harbourlamp", "greyfen",
    )
    pools: dict[str, list[str]] = {}
    available = list(stems)
    rng.shuffle(available)
    for source in SOURCES:
        pool = []
        for _ in range(BUYER_POOL_SIZE):
            stem = available[rng.randrange(len(available))]
            pool.append(f"{stem}{rng.randrange(10, 99)}")
        pools[source["name"]] = sorted(dict.fromkeys(pool))
    return pools


def build_feedback(personas: list[dict]) -> list[dict]:
    """Buyer→vendor feedback rows. Markets only; forums have no listings."""
    rng = random.Random(f"{FIXTURE_SEED}:feedback")
    pools = _buyer_handles(rng)

    # Who traded where, so a migrated vendor can retain a few customers.
    by_actor: dict[str, list[dict]] = {}
    for persona in personas:
        by_actor.setdefault(persona["actor"], []).append(persona)
    previous_buyers: dict[int, list[str]] = {}

    rows: list[dict] = []
    feedback_id = 1
    for persona in personas:
        source = SOURCE_BY_NAME[persona["source"]]
        if source["kind"] != "market":
            continue  # a forum has no listings to leave feedback on

        pool = pools[persona["source"]]
        count = rng.randrange(3, 9)

        # A minority of a migrated vendor's buyers followed them across.
        carried: list[str] = []
        for sibling in by_actor.get(persona["actor"], []):
            if sibling["id"] == persona["id"]:
                continue
            for handle in previous_buyers.get(sibling["id"], []):
                if rng.random() < CROSS_MARKET_LOYALTY:
                    carried.append(handle)

        buyers = list(dict.fromkeys(
            carried + [pool[rng.randrange(len(pool))] for _ in range(count)]
        ))[:count]
        previous_buyers[persona["id"]] = buyers

        for buyer in buyers:
            rating, body = FEEDBACK_PHRASES[rng.randrange(len(FEEDBACK_PHRASES))]
            rows.append({
                "id": feedback_id,
                "persona_id": persona["id"],
                "source_id": persona["source_id"],
                "buyer_handle": buyer,
                "rating": int(rating),
                "body": body,
                "category": persona["category"],
                "posted_at": _timestamp(persona, rng).isoformat(),
                "url": f"{persona['profile_url']}/feedback",
            })
            feedback_id += 1
    return rows


def write_json(path: Path, payload) -> None:
    """Write UTF-8 with LF endings on every platform.

    `Path.write_text` translates \\n to \\r\\n on Windows, which would make the
    corpus differ byte-for-byte depending on who regenerated it. `.gitattributes`
    normalises on commit, but the working tree would still diverge.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False) + "\n"
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(body)


def main() -> int:
    personas, posts = build_corpus()
    self_check(personas, posts)

    write_json(OUT / "sources.json", SOURCES)

    for source in SOURCES:
        name = source["name"]
        members = [p for p in personas if p["source"] == name]
        member_ids = {p["id"] for p in members}
        write_json(OUT / name / "personas.json", members)
        write_json(OUT / name / "posts.json",
                   [p for p in posts if p["persona_id"] in member_ids])

    write_json(OUT / "infra_findings.json", build_infra_findings())
    write_json(OUT / "clearnet_obs" / "shodan_observations.json",
               build_clearnet_observations())
    write_json(OUT / "ground_truth.json", build_ground_truth(personas))
    # Phase 6. A new file; nothing above is re-read or rewritten.
    feedback = build_feedback(personas)
    write_json(OUT / "feedback.json", feedback)

    chars = sum(len(p["body"]) for p in posts)
    print(f"fixtures written to {OUT}")
    print(f"  sources  : {len(SOURCES)}")
    print(f"  personas : {len(personas)}  ({len({p['actor'] for p in personas})} actors)")
    print(f"  posts    : {len(posts)}  ({chars:,} characters, "
          f"mean {chars // len(posts)} per post)")
    print(f"  clearnet : {len(build_clearnet_observations())} shodan-shaped records")
    buyers = len({f["buyer_handle"] for f in feedback})
    print(f"  feedback : {len(feedback)} rows from {buyers} buyers "
          f"(buyers are NOT personas)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
