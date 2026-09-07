# obfuslex_engine.py
# ═══════════════════════════════════════════════════════════════════
# DarkSentinel — ObfusLex Engine  (v2 — expanded term DB)
# ═══════════════════════════════════════════════════════════════════
#
# PURPOSE:
#   Detects obfuscated illegal/threat terms in dark web text.
#   Handles leet-speak, spacing tricks, suffix variations.
#   Builds and persists SHA-256 hash DB to obfus_hashes.db (SQLite).
#
# IMPORT IN OTHER MODULES:
#   from obfuslex_engine import obfuslex_scan, build_db, CANONICAL_TERMS
#
# STANDALONE (builds DB + runs self-tests):
#   python obfuslex_engine.py
#   → creates obfus_hashes.db in current directory
# ═══════════════════════════════════════════════════════════════════

import re
import json
import hashlib
import sqlite3
import logging
from pathlib import Path

log = logging.getLogger("ObfusLex")

DB_PATH = "obfus_hashes.db"

# ═══════════════════════════════════════════════════════════════════
# LEET / OBFUSCATION MAP
# ═══════════════════════════════════════════════════════════════════
LEET_MAP = {
    "@": "a",  "4": "a",
    "3": "e",  "£": "e",
    "!": "i",  "1": "i",  "|": "i",
    "0": "o",
    "$": "s",  "5": "s",
    "+": "t",  "7": "t",
    "%": "x",
    "#": "h",
    "-": "",   # strip hyphens (meth-amphetamine → methamphetamine)
    "_": "",
    ".": "",
    "*": "",   # wildcard — strip
}

def leet_decode(token: str) -> str:
    """Apply leet substitution to a token."""
    result = token.lower()
    # Single-char subs
    return "".join(LEET_MAP.get(ch, ch) for ch in result)


# ═══════════════════════════════════════════════════════════════════
# TERM DATABASE — organised by category
# ═══════════════════════════════════════════════════════════════════

# ── Drugs: Opioids ─────────────────────────────────────────────────
DRUGS_OPIOIDS = [
    "fentanyl", "carfentanil", "acetylfentanyl", "sufentanil", "remifentanil",
    "heroin", "diacetylmorphine", "brown sugar", "smack", "china white",
    "morphine", "codeine", "oxycodone", "oxy", "oxycontin",
    "hydrocodone", "vicodin", "oxymorphone", "opana",
    "hydromorphone", "dilaudid",
    "buprenorphine", "suboxone",
    "tramadol", "ultram",
    "methadone",
    "tapentadol",
    "meperidine", "pethidine",
    "opium", "afim",
    "opioid", "opioids", "narcotic",
]

# ── Drugs: Stimulants ──────────────────────────────────────────────
DRUGS_STIMULANTS = [
    "cocaine", "coke", "blow", "snow", "flake", "fishscale",
    "crack", "rock",
    "methamphetamine", "meth", "crystal", "ice", "glass", "shabu", "tina",
    "amphetamine", "speed", "adderall", "dex", "dextroamphetamine",
    "mdma", "ecstasy", "molly", "xtc", "e", "rolls",
    "ephedrine", "pseudoephedrine",
    "cathinone", "mephedrone", "bath salts", "mc4",
    "khat",
]

# ── Drugs: Depressants / Benzodiazepines ───────────────────────────
DRUGS_DEPRESSANTS = [
    "xanax", "alprazolam", "diazepam", "valium",
    "clonazepam", "klonopin", "lorazepam", "ativan",
    "benzodiazepine", "benzo", "benzos",
    "flunitrazepam", "rohypnol", "roofie",
    "ghb", "gbl",
    "ketamine", "ket", "special k",
    "barbiturate", "phenobarbital",
    "zolpidem", "ambien",
    "zopiclone", "eszopiclone",
]

# ── Drugs: Psychedelics ────────────────────────────────────────────
DRUGS_PSYCHEDELICS = [
    "lsd", "acid", "tabs", "blotter",
    "psilocybin", "psilocin", "mushroom", "shroom", "magic mushrooms",
    "dmt", "ayahuasca",
    "mescaline", "peyote",
    "nbome", "nbomes",
    "salvia",
    "pcp", "phencyclidine", "angel dust",
]

# ── Drugs: Cannabis ────────────────────────────────────────────────
DRUGS_CANNABIS = [
    "cannabis", "marijuana", "weed", "ganja", "pot", "reefer",
    "hash", "hashish", "charas",
    "thc", "cbd",
    "edibles", "dabs", "shatter", "wax", "live resin",
    "bhang",
]

# ── Drugs: Other / Steroids ────────────────────────────────────────
DRUGS_OTHER = [
    "steroid", "steroids", "anabolic",
    "hgh", "growth hormone",
    "prescription", "pills", "painkillers",
    "darkpharma", "pharmacy",
]

# ── Cybercrime: Malware (with named families) ──────────────────────
CYBER_MALWARE = [
    "malware", "ransomware", "spyware", "adware", "scareware",
    "trojan", "rat", "keylogger", "rootkit", "bootkit",
    "worm", "virus", "backdoor", "implant",
    "botnet", "zombie", "c2", "cnc",
    "stealer", "infostealer", "credential stealer",
    # Named malware families (high-value for threat intel)
    "redline", "raccoon", "vidar", "lumma", "agent tesla",
    "asyncrat", "njrat", "remcos", "darkcomet",
    "lockbit", "blackcat", "alphv", "conti", "revil", "ryuk",
    "emotet", "trickbot", "qakbot", "bazarloader",
    "cryptojacker", "cryptominer",
    "wiper", "destructive malware",
    "banker", "banking trojan",
]

# ── Cybercrime: Exploits / Hacking ────────────────────────────────
CYBER_EXPLOITS = [
    "exploit", "exploits", "zeroday", "zero day", "0day",
    "vulnerability", "cve", "poc",
    "rce", "remote code execution",
    "sqli", "sql injection",
    "xss", "cross site scripting",
    "lfi", "rfi", "ssti",
    "shellcode", "payload",
    "metasploit", "cobalt strike", "empire",
    "mimikatz", "bloodhound",
    "privilege escalation", "privesc",
    "lateral movement",
    "phishing", "spearphishing", "vishing", "smishing",
    "ddos", "dos", "flood", "booter", "stresser",
    "brute force", "password spraying",
    "hash cracking", "rainbow table",
]

# ── Cybercrime: Fraud / Financial (India-specific included) ────────
CYBER_FRAUD = [
    "carding", "carder", "cardshop",
    "fullz", "cvv", "dumps", "track2",
    "cashout", "money mule", "mule",
    "skimmer", "atm skimmer",
    "bank logs", "bank login",
    "paypal", "stripe", "chargeback",
    # India-specific fraud terms
    "upi", "upi fraud", "upi combo",
    "otp bypass", "sim swap",
    "fraud", "scam", "phishing kit",
    "fake invoice", "bec", "business email compromise",
    "identity theft", "synthetic identity",
    "money laundering", "hawala",
    "crypto mixer", "tumbler", "tornado cash",
]

# ── Cybercrime: Access / Credentials ──────────────────────────────
CYBER_ACCESS = [
    "rdp", "ssh", "vpn access",
    "credentials", "login", "account",
    "botnet access", "shell access", "webshell",
    "initial access", "persistence",
    "combo list", "wordlist",
    "database leak", "data breach", "database dump",
    "doxxing", "dox",
]

# ── Weapons ────────────────────────────────────────────────────────
WEAPONS = [
    "firearm", "pistol", "handgun", "revolver",
    "rifle", "shotgun", "assault rifle",
    "glock", "ak47", "ar15", "uzi",
    "sniper", "carbine",
    "silencer", "suppressor",
    "ammunition", "ammo", "bullets",
    "explosive", "grenade", "c4", "tnt", "ied",
    "knife", "switchblade",
    "ghost gun", "unregistered", "3d printed gun",
]

# ── Documents / Identity Fraud ────────────────────────────────────
DOCUMENTS = [
    "counterfeit", "fake id", "forged",
    "passport", "drivers license", "dl",
    "ssn", "aadhaar", "pan card",
    "identity document", "identity fraud",
    "fake diploma", "fake certificate",
    "template", "hologram",
]

# ── Dark Web Marketplace Terms ────────────────────────────────────
MARKETPLACE = [
    "darknet", "darkweb", "deepweb",
    "marketplace", "market", "vendor", "seller",
    "escrow", "multisig", "pgp",
    "monero", "xmr", "bitcoin", "btc", "crypto",
    "finalize early", "fe",
    "auto dispatch", "autoshop",
    "stealth shipping", "discreet", "vacuum sealed",
    "tracking number", "shipped",
    "trusted vendor", "verified vendor",
]

# ═══════════════════════════════════════════════════════════════════
# COMBINE ALL TERMS
# ═══════════════════════════════════════════════════════════════════

ALL_TERMS_GROUPED = {
    "drugs_opioids":      DRUGS_OPIOIDS,
    "drugs_stimulants":   DRUGS_STIMULANTS,
    "drugs_depressants":  DRUGS_DEPRESSANTS,
    "drugs_psychedelics": DRUGS_PSYCHEDELICS,
    "drugs_cannabis":     DRUGS_CANNABIS,
    "drugs_other":        DRUGS_OTHER,
    "cyber_malware":      CYBER_MALWARE,
    "cyber_exploits":     CYBER_EXPLOITS,
    "cyber_fraud":        CYBER_FRAUD,
    "cyber_access":       CYBER_ACCESS,
    "weapons":            WEAPONS,
    "documents":          DOCUMENTS,
    "marketplace":        MARKETPLACE,
}

# Flat deduplicated list + category lookup
CANONICAL_TERMS: list = []
TERM_CATEGORY:   dict = {}

for _category, _terms in ALL_TERMS_GROUPED.items():
    for _term in _terms:
        _norm = _term.lower().strip()
        if _norm not in TERM_CATEGORY:   # first category wins for duplicates
            CANONICAL_TERMS.append(_norm)
            TERM_CATEGORY[_norm] = _category

# In-memory hash lookups
HASH_DB: dict = {
    hashlib.sha256(t.encode()).hexdigest(): t
    for t in CANONICAL_TERMS
}
HASH_CATEGORY: dict = {
    hashlib.sha256(t.encode()).hexdigest(): TERM_CATEGORY[t]
    for t in CANONICAL_TERMS
}


# ═══════════════════════════════════════════════════════════════════
# SQLite PERSISTENCE — obfus_hashes.db
# ═══════════════════════════════════════════════════════════════════

def build_db(db_path: str = DB_PATH) -> None:
    """
    Build (or rebuild) the SQLite hash database from CANONICAL_TERMS.
    Creates table: hashes(hash TEXT PK, term TEXT, category TEXT).
    Safe to call multiple times — always rebuilds clean.
    """
    conn = sqlite3.connect(db_path)
    cur  = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS hashes (
            hash     TEXT PRIMARY KEY,
            term     TEXT NOT NULL,
            category TEXT NOT NULL
        )
    """)
    cur.execute("DELETE FROM hashes")
    rows = [
        (hashlib.sha256(t.encode()).hexdigest(), t, TERM_CATEGORY.get(t, "unknown"))
        for t in CANONICAL_TERMS
    ]
    cur.executemany("INSERT INTO hashes VALUES (?,?,?)", rows)
    conn.commit()
    conn.close()
    log.info(f"ObfusLex DB built: {len(rows)} terms → {db_path}")


def load_db(db_path: str = DB_PATH) -> dict:
    """Load hash DB from SQLite. Returns {hash: (term, category)}."""
    if not Path(db_path).exists():
        build_db(db_path)
    conn = sqlite3.connect(db_path)
    cur  = conn.cursor()
    cur.execute("SELECT hash, term, category FROM hashes")
    rows = cur.fetchall()
    conn.close()
    return {h: (t, c) for h, t, c in rows}


# ═══════════════════════════════════════════════════════════════════
# CORE SCAN FUNCTION
# ═══════════════════════════════════════════════════════════════════

def obfuslex_scan(text: str, use_db: bool = False) -> dict:
    """
    Scan text for obfuscated threat/illegal terms.

    Args:
        text    : Input string (cleaned/lowercased preferred)
        use_db  : Load from SQLite instead of in-memory dict
                  (use when running across separate processes)

    Returns:
        obfuscation_detected : bool
        flagged_count        : int
        flagged_tokens       : JSON — list of {original, decoded, matched, category}
        categories_hit       : JSON — list of unique categories detected
        threat_score         : float 0.0–1.0  (pre-ML heuristic signal)

    Threat score heuristic (feeds into Risk Engine R = 0.35T + 0.45C + 0.20H):
        +0.15 per unique category hit
        +0.05 per additional token beyond first in same category
        Capped at 1.0
    """
    if use_db:
        db           = load_db()
        _hash_db     = {h: t for h, (t, c) in db.items()}
        _hash_cat    = {h: c for h, (t, c) in db.items()}
    else:
        _hash_db  = HASH_DB
        _hash_cat = HASH_CATEGORY

    flagged      = []
    seen_decoded = set()

    # ── Token-level scan ──────────────────────────────────────────
    # Broad regex: captures leet-substituted tokens with special chars
    tokens = re.findall(r"[A-Za-z0-9@$!+|#£\-_.]+", text)

    for token in tokens:
        if len(token) < 2:
            continue
        decoded = leet_decode(token)
        if len(decoded) < 2 or decoded in seen_decoded:
            continue
        h = hashlib.sha256(decoded.encode()).hexdigest()
        if h in _hash_db:
            seen_decoded.add(decoded)
            flagged.append({
                "original": token,
                "decoded":  decoded,
                "matched":  _hash_db[h],
                "category": _hash_cat.get(h, "unknown"),
            })

    # ── Phrase-level scan (multi-word terms) ──────────────────────
    # Decode the whole text first, then search for multi-word entries
    decoded_text = leet_decode(text.lower())
    for term in CANONICAL_TERMS:
        if " " not in term:
            continue
        if term in decoded_text:
            if not any(f["matched"] == term for f in flagged):
                flagged.append({
                    "original": term,
                    "decoded":  term,
                    "matched":  term,
                    "category": TERM_CATEGORY.get(term, "unknown"),
                })

    # ── Scoring ───────────────────────────────────────────────────
    categories_hit = list({f["category"] for f in flagged})
    extra_tokens   = max(0, len(flagged) - len(categories_hit))
    threat_score   = min(1.0, len(categories_hit) * 0.15 + extra_tokens * 0.05)

    return {
        "obfuscation_detected": len(flagged) > 0,
        "flagged_count":        len(flagged),
        "flagged_tokens":       json.dumps(flagged),
        "categories_hit":       json.dumps(categories_hit),
        "threat_score":         round(threat_score, 3),
    }


# ═══════════════════════════════════════════════════════════════════
# STATS / REPORTING
# ═══════════════════════════════════════════════════════════════════

def print_db_stats():
    print("\n" + "="*57)
    print("  ObfusLex Engine v2 — Term Database")
    print("="*57)
    print(f"  Total canonical terms    : {len(CANONICAL_TERMS)}")
    print(f"  Total hash entries       : {len(HASH_DB)}")
    print()
    for category, terms in ALL_TERMS_GROUPED.items():
        print(f"  {category:<24}: {len(terms):>3} terms")
    print("="*57)
    print()


# ═══════════════════════════════════════════════════════════════════
# STANDALONE — builds DB and runs self-tests
# python obfuslex_engine.py
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)s  %(message)s",
    )

    print_db_stats()
    build_db()
    print(f"✅  obfus_hashes.db created — {len(CANONICAL_TERMS)} terms.\n")

    # ── Self-tests ────────────────────────────────────────────────
    TEST_CASES = [
        # Opioids — plain + obfuscated
        ("f3nt@ny1 for sale btc",              "opioid obfuscation (leet)"),
        ("brown sugar smack cheap delivery",   "opioid slang"),
        ("oxycontin oxy vicodin vendor",       "opioid brand names"),

        # Stimulants — plain + obfuscated
        ("c0ca1ne coke blow fishscale",        "stimulant obfuscation"),
        ("m3th ice glass shabu tina deals",    "stimulant slang obfuscated"),
        ("molly rolls ecstasy mdma xtc",       "mdma slang"),

        # Depressants
        ("b3nzo rohypnol roofie xanax pills",  "depressant obfuscated"),
        ("special k ket ghb gbl sale",         "dissociative slang"),

        # Psychedelics
        ("lsd acid tabs blotter sale",         "psychedelic plain"),
        ("magic mushrooms shroom psilocybin",  "psychedelic slang"),

        # Cannabis (India-specific)
        ("charas bhang ganja hashish weed",    "cannabis India terms"),

        # Malware — named families
        ("redline stealer lumma vidar raccoon","named malware families"),
        ("lockbit ransomware conti revil",     "ransomware families"),
        ("emotet trickbot qakbot loader",      "banking trojans"),

        # Exploits
        ("0day rce exploit poc shellcode",     "exploit terms"),
        ("cobalt strike mimikatz bloodhound",  "red team tools"),
        ("ddos booter stresser flood",         "ddos tools"),

        # Fraud — India-specific
        ("upi fraud upi combo otp bypass",     "India UPI fraud"),
        ("hawala money laundering crypto mixer","financial crime"),
        ("sim swap bank login cashout mule",   "account takeover fraud"),

        # Weapons
        ("glock ak47 ghost gun silencer",      "weapons"),
        ("ied explosive grenade c4",           "explosives"),

        # Documents
        ("fake id aadhaar pan card forged",    "India document fraud"),
        ("counterfeit passport hologram",      "document counterfeiting"),

        # Marketplace
        ("darknet market escrow monero xmr",   "marketplace terms"),
        ("trusted vendor stealth shipping fe", "marketplace ops"),
        ("vacuum sealed discreet autoshop",    "shipping terms"),

        # Clean — should detect nothing
        ("completely normal sentence about weather today", "clean text"),
        ("the cat sat on the mat",             "clean text 2"),
    ]

    print("  Self-test results:")
    print("  " + "─"*53)
    all_passed = True
    for text, description in TEST_CASES:
        result   = obfuslex_scan(text)
        detected = result["obfuscation_detected"]
        count    = result["flagged_count"]
        score    = result["threat_score"]
        cats     = json.loads(result["categories_hit"])
        tokens   = json.loads(result["flagged_tokens"])[:2]

        is_clean_test = "clean" in description
        # Clean tests should NOT detect; all others SHOULD detect
        passed = (detected == (not is_clean_test))
        if not passed:
            all_passed = False

        status = "✅" if passed else "❌"
        det_str = "DETECTED" if detected else "clean"
        print(f"\n  {status} [{det_str}] {description}")
        print(f"       \"{text[:60]}\"")
        if detected:
            print(f"       flagged={count} | score={score} | cats={cats}")
            for t in tokens:
                print(f"       → '{t['original']}' → '{t['matched']}' [{t['category']}]")

    print()
    if all_passed:
        print("  ✅  All self-tests passed.")
    else:
        print("  ⚠️   Some tests failed — review term list or leet map.")
    print()
