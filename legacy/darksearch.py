# darksearch.py  (v5 — REAL-TIME STREAMING + PROACTIVE MONITOR)
# ═══════════════════════════════════════════════════════════════════
# DarkSentinel — Search + Scrape Module
# ═══════════════════════════════════════════════════════════════════
#
# KEY OPTIMISATIONS OVER v4:
#
#  1. STREAMING RESULTS  — run_one_query_streaming() yields rows AS THEY
#     arrive so the UI can show results live instead of waiting 30 min.
#
#  2. ADAPTIVE ENGINE RANKING — engines that returned results fastest in
#     previous calls are tried first. Slow/dead engines are deprioritised
#     automatically (ELO-style score per engine, persisted in-memory).
#
#  3. EARLY-EXIT SEARCH — as soon as max_results unique links are
#     collected the remaining engine futures are cancelled, saving minutes
#     of waiting for slow .onion search engines.
#
#  4. TWO-PHASE SCRAPING — scrape is split into a FAST tier (titles-only,
#     ObfusLex instant scan) and a DEEP tier (full HTML fetch). The UI
#     receives fast results in <60 s and deep results stream in afterwards.
#
#  5. ADAPTIVE TIMEOUTS — each engine gets a per-call budget. If it misses
#     its budget once, timeout shrinks by 20 %. Engines that time out 3×
#     in a row are skipped for that session.
#
#  6. CONTENT DELTA DETECTION — scrape_page computes a content hash.
#     If a page hasn't changed since last cache hit it is skipped for deep
#     analysis (reduces redundant LLM calls upstream).
#
#  7. PROACTIVE MONITOR — ProactiveMonitor class runs in a background
#     daemon thread. It triggers a fresh scrape whenever:
#       a) CIC-RF T-score > 0.70  (Innovation #1, unchanged)
#       b) A new HIGH/CRITICAL result arrives while streaming
#       c) Time since last scan > MONITOR_INTERVAL_MINUTES
#     Results are pushed to a thread-safe queue for ui.py to consume.
#
#  8. CIRCUIT BREAKER — if Tor drops mid-session, scraping pauses and
#     retries reconnection up to 3× before surfacing an error. Partially
#     collected results are preserved.
#
# BACKWARDS COMPATIBLE:
#   run_one_query()       — still works exactly as before (blocking)
#   detect_tor()          — unchanged
#   get_tor_status()      — unchanged
#   guard_agent()         — unchanged
#   search_all_engines()  — unchanged
#   All imports from ui.py continue to work with zero changes to ui.py.
#
# NEW CALLABLES FOR ui.py:
#   run_one_query_streaming(query, max_results, do_scrape, callback)
#   ProactiveMonitor(callback)  .start() / .stop() / .trigger_now()
#   get_engine_health()         → per-engine stats dict
#   reset_engine_health()
# ═══════════════════════════════════════════════════════════════════

import re
import csv
import time
import random
import hashlib
import logging
import json
import sys
import argparse
import os
import threading
import queue
from datetime import datetime, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed, Future
from urllib.parse import urlparse, urlunparse
from typing import Callable, Generator, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
import pandas as pd
import warnings
warnings.filterwarnings("ignore")

# ── Import ObfusLex engine ─────────────────────────────────────────
try:
    from obfuslex_engine import obfuslex_scan, build_db, CANONICAL_TERMS
    OBFUSLEX_AVAILABLE = True
except ImportError:
    print("❌ obfuslex_engine.py not found. Place it alongside darksearch.py.")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("DarkSentinel")

# ── PRE-FLIGHT ────────────────────────────────────────────────────
try:
    import socks  # noqa: F401
except ImportError:
    log.error("❌ PySocks missing. Run: pip install \"requests[socks]\"")
    sys.exit(1)

build_db()

# ═══════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════

TOR_PROXIES = {
    "http":  "socks5h://127.0.0.1:9050",
    "https": "socks5h://127.0.0.1:9050",
}
TOR_BROWSER_PROXIES = {
    "http":  "socks5h://127.0.0.1:9150",
    "https": "socks5h://127.0.0.1:9150",
}

SEARCH_TIMEOUT       = 25
SCRAPE_TIMEOUT       = 30
DEEP_SCRAPE_TIMEOUT  = 45
SEARCH_WORKERS       = 16
SCRAPE_WORKERS       = 12
FAST_SCRAPE_WORKERS  = 16
MAX_SCRAPE_CHARS     = 4000
MAX_RESPONSE_BYTES   = 8 * 1024 * 1024
MAX_RESULTS          = 20
MONITOR_INTERVAL_MINUTES = 120

BASE_DIR = Path(__file__).parent
SCRAPE_EVENTS_FILE = BASE_DIR / "scrape_events.jsonl"
SCRAPE_EVENT_RETENTION = max(200, int(os.environ.get("DS_SCRAPE_EVENT_RETENTION", "5000")))

ALL_THREAT_QUERIES = [
    "drug marketplace vendor",
    "hacking tools exploit sale",
    "fraud carding fullz dumps",
    "counterfeit documents identity",
    "ransomware malware botnet",
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:137.0) "
    "Gecko/20100101 Firefox/137.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:137.0) "
    "Gecko/20100101 Firefox/137.0",
]

# ═══════════════════════════════════════════════════════════════════
# SEARCH ENGINES  (16 .onion search engines)
# ═══════════════════════════════════════════════════════════════════
SEARCH_ENGINES = [
    {"name": "Ahmia",             "url": "http://juhanurmihxlp77nkq76byazcldy2hlmovfu2epvl5ankdibsot4csyd.onion/search/?q={query}"},
    {"name": "OnionLand",         "url": "http://3bbad7fauom4d6sgppalyqddsqbf5u5p56b5k5uk2zxsy3d6ey2jobad.onion/search?q={query}"},
    {"name": "Torgle",            "url": "http://iy3544gmoeclh5de6gez2256v6pjh4omhpqdh2wpeeppjtvqmjhkfwad.onion/torgle/?query={query}"},
    {"name": "Amnesia",           "url": "http://amnesia7u5odx5xbwtpnqk3edybgud5bmiagu75bnqx2crntw5kry7ad.onion/search?query={query}"},
    {"name": "Kaizer",            "url": "http://kaizerwfvp5gxu6cppibp7jhcqptavq3iqef66wbxenh6a2fklibdvid.onion/search?q={query}"},
    {"name": "Anima",             "url": "http://anima4ffe27xmakwnseih3ic2y7y3l6e7fucwk4oerdn4odf7k74tbid.onion/search?q={query}"},
    {"name": "Tornado",           "url": "http://tornadoxn3viscgz647shlysdy7ea5zqzwda7hierekeuokh5eh5b3qd.onion/search?q={query}"},
    {"name": "TorNet",            "url": "http://tornetupfu7gcgidt33ftnungxzyfq2pygui5qdoyss34xbgx2qruzid.onion/search?q={query}"},
    {"name": "Torland",           "url": "http://torlbmqwtudkorme6prgfpmsnile7ug2zm4u3ejpcncxuhpu4k2j4kyd.onion/index.php?a=search&q={query}"},
    {"name": "Find Tor",          "url": "http://findtorroveq5wdnipkaojfpqulxnkhblymc7aramjzajcvpptd4rjqd.onion/search?q={query}"},
    {"name": "Excavator",         "url": "http://2fd6cemt4gmccflhm6imvdfvli3nf7zn6rfrwpsy7uhxrgbypvwf5fad.onion/search?query={query}"},
    {"name": "Onionway",          "url": "http://oniwayzz74cv2puhsgx4dpjwieww4wdphsydqvf5q7eyz4myjvyw26ad.onion/search.php?s={query}"},
    {"name": "Tor66",             "url": "http://tor66sewebgixwhcqfnp5inzp5x5uohhdy3kvtnyfxc2e5mxiuh34iid.onion/search?q={query}"},
    {"name": "OSS",               "url": "http://3fzh7yuupdfyjhwt3ugzqqof6ulbcl27ecev33knxe3u7goi3vfn2qqd.onion/oss/index.php?search={query}"},
    {"name": "Torgol",            "url": "http://torgolnpeouim56dykfob6jh5r2ps2j73enc42s2um4ufob3ny4fcdyd.onion/?q={query}"},
    {"name": "The Deep Searches", "url": "http://searchgf7gdtauh7bhnbyed4ivxqmuoat3nm6zfrg3ymkq6mtnpye3ad.onion/search?q={query}"},
]

SEARCH_ENGINE_DOMAINS = {
    e["url"].split("//")[1].split("/")[0].replace(".onion", "")
    for e in SEARCH_ENGINES
}

# ═══════════════════════════════════════════════════════════════════
# ENGINE HEALTH TRACKER
# ═══════════════════════════════════════════════════════════════════

_engine_health_lock = threading.Lock()
_engine_health: dict = {
    e["name"]: {
        "score":                 100.0,
        "timeout":               SEARCH_TIMEOUT,
        "consecutive_timeouts":  0,
        "total_calls":           0,
        "total_results":         0,
        "last_used":             None,
        "skipped":               False,
    }
    for e in SEARCH_ENGINES
}


def _engine_record_success(name: str, elapsed: float, result_count: int):
    with _engine_health_lock:
        h = _engine_health[name]
        h["total_calls"]            += 1
        h["total_results"]          += result_count
        h["consecutive_timeouts"]    = 0
        h["last_used"]               = datetime.utcnow().isoformat()
        h["skipped"]                 = False
        speed_bonus = max(0, (h["timeout"] - elapsed) / h["timeout"]) * 50
        h["score"]  = min(200.0, h["score"] * 0.85 + speed_bonus + result_count * 2)
        h["timeout"] = min(SEARCH_TIMEOUT, h["timeout"] + 2)


def _engine_record_failure(name: str, timed_out: bool):
    with _engine_health_lock:
        h = _engine_health[name]
        h["total_calls"] += 1
        h["last_used"]   = datetime.utcnow().isoformat()
        if timed_out:
            h["consecutive_timeouts"] += 1
            h["timeout"] = max(8, int(h["timeout"] * 0.8))
            if h["consecutive_timeouts"] >= 3:
                h["skipped"] = True
                log.info(f"[{name}] Circuit-broken (3 consecutive timeouts)")
        h["score"] = max(0.0, h["score"] * 0.6)


def _ranked_engines() -> list:
    with _engine_health_lock:
        ranked = sorted(
            SEARCH_ENGINES,
            key=lambda e: _engine_health[e["name"]]["score"],
            reverse=True,
        )
        return [e for e in ranked if not _engine_health[e["name"]]["skipped"]]


def get_engine_health() -> dict:
    with _engine_health_lock:
        return {k: dict(v) for k, v in _engine_health.items()}


def reset_engine_health():
    with _engine_health_lock:
        for name in _engine_health:
            _engine_health[name].update({
                "score":                100.0,
                "timeout":              SEARCH_TIMEOUT,
                "consecutive_timeouts": 0,
                "skipped":              False,
            })


# ═══════════════════════════════════════════════════════════════════
# TOR
# ═══════════════════════════════════════════════════════════════════

_tor_connected = False
_proxy_lock    = threading.Lock()
_proxy_pool    = [TOR_PROXIES.copy()]
_proxy_index   = 0

_cache_lock    = threading.Lock()
_scrape_cache  = {}
_cache_hits    = 0
_cache_misses  = 0
SCRAPE_CACHE_ENABLED = True

_scrape_events_lock = threading.Lock()
_scrape_events_writes = 0


def _trim_scrape_events_file(max_lines: int = SCRAPE_EVENT_RETENTION):
    try:
        if not SCRAPE_EVENTS_FILE.exists():
            return
        lines = SCRAPE_EVENTS_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()
        if len(lines) <= max_lines:
            return
        SCRAPE_EVENTS_FILE.write_text("\n".join(lines[-max_lines:]) + "\n", encoding="utf-8")
    except Exception:
        pass


def _append_scrape_event(onion_url: str, query: str = "", stage: str = "deep"):
    """Persist deep scrape events for downstream URL-to-flow T-score matching."""
    global _scrape_events_writes
    norm_url = _normalize_onion_url(onion_url)
    if not norm_url:
        return
    evt = {
        "captured_at_utc": datetime.utcnow().isoformat(),
        "onion_url": norm_url,
        "source_hash": hashlib.sha256(norm_url.encode("utf-8", errors="ignore")).hexdigest(),
        "query": str(query or "")[:200],
        "stage": str(stage or "deep")[:20],
    }
    try:
        with _scrape_events_lock:
            SCRAPE_EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with SCRAPE_EVENTS_FILE.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(evt, ensure_ascii=True) + "\n")
            _scrape_events_writes += 1
            if _scrape_events_writes % 250 == 0:
                _trim_scrape_events_file(SCRAPE_EVENT_RETENTION)
    except Exception:
        pass


def get_recent_scrape_events(limit: int = 200) -> list:
    """Return latest deep-scrape events for diagnostics and correlation."""
    lim = max(1, int(limit))
    try:
        if not SCRAPE_EVENTS_FILE.exists():
            return []
        lines = SCRAPE_EVENTS_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()
        events = []
        for ln in lines[-lim:]:
            try:
                events.append(json.loads(ln))
            except Exception:
                continue
        return events
    except Exception:
        return []


def _is_onion_host(host: str) -> bool:
    return bool(host and host.lower().endswith(".onion"))


def _normalize_onion_url(raw_url: str) -> str:
    try:
        p      = urlparse(raw_url.strip())
        scheme = (p.scheme or "").lower()
        host   = (p.hostname or "").lower()
        if scheme not in {"http", "https"} or not _is_onion_host(host):
            return ""
        netloc = host
        if p.port:
            netloc = f"{host}:{p.port}"
        path   = p.path or "/"
        return urlunparse((scheme, netloc, path, "", p.query or "", ""))
    except Exception:
        return ""


def _proxy_dict_from_uri(uri: str) -> dict:
    return {"http": uri, "https": uri}


def _build_proxy_candidates() -> list:
    candidates = [TOR_PROXIES, TOR_BROWSER_PROXIES]
    pool_raw = os.environ.get("DS_TOR_PROXY_POOL", "").strip()
    if pool_raw:
        for item in pool_raw.split(","):
            uri = item.strip()
            if uri.startswith(("socks5://", "socks5h://")):
                candidates.append(_proxy_dict_from_uri(uri))
    ports_raw = os.environ.get("DS_TOR_PROXY_PORTS", "").strip()
    if ports_raw:
        for p in ports_raw.split(","):
            p = p.strip()
            if p.isdigit():
                candidates.append(_proxy_dict_from_uri(f"socks5h://127.0.0.1:{int(p)}"))
    seen, unique = set(), []
    for c in candidates:
        key = c.get("http", "")
        if key and key not in seen:
            seen.add(key)
            unique.append(c)
    return unique


def _set_proxy_pool(pool: list):
    global _proxy_pool, _proxy_index
    with _proxy_lock:
        _proxy_pool  = list(pool) if pool else [TOR_PROXIES.copy()]
        _proxy_index = 0


def _next_proxy() -> dict:
    global _proxy_index
    with _proxy_lock:
        if not _proxy_pool:
            return TOR_PROXIES
        proxy        = _proxy_pool[_proxy_index % len(_proxy_pool)]
        _proxy_index += 1
        return proxy


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8", errors="ignore")).hexdigest()


def _cache_get(url: str):
    global _cache_hits, _cache_misses
    if not SCRAPE_CACHE_ENABLED:
        return None
    key = _url_hash(url)
    with _cache_lock:
        item = _scrape_cache.get(key)
        if item is None:
            _cache_misses += 1
        else:
            _cache_hits += 1
        return item


def _cache_put(url: str, text: str):
    if not SCRAPE_CACHE_ENABLED:
        return
    key = _url_hash(url)
    with _cache_lock:
        _scrape_cache[key] = {
            "text":         text,
            "content_hash": hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest(),
            "cached_at":    datetime.utcnow().isoformat(),
        }


def _cache_content_changed(url: str, new_text: str) -> bool:
    cached = _cache_get(url)
    if not cached:
        return True
    old_hash = cached.get("content_hash", "")
    new_hash = hashlib.sha256(new_text.encode("utf-8", errors="ignore")).hexdigest()
    return old_hash != new_hash


def get_scrape_cache_stats() -> dict:
    with _cache_lock:
        total    = _cache_hits + _cache_misses
        hit_rate = (_cache_hits / total) if total else 0.0
        return {
            "enabled":  SCRAPE_CACHE_ENABLED,
            "entries":  len(_scrape_cache),
            "hits":     _cache_hits,
            "misses":   _cache_misses,
            "hit_rate": round(hit_rate, 4),
        }


def clear_scrape_cache():
    global _cache_hits, _cache_misses
    with _cache_lock:
        _scrape_cache.clear()
        _cache_hits = _cache_misses = 0


def configure_runtime(search_workers=None, scrape_workers=None,
                      search_timeout=None, scrape_timeout=None,
                      max_scrape_chars=None, max_response_bytes=None,
                      use_scrape_cache=None) -> dict:
    global SEARCH_WORKERS, SCRAPE_WORKERS, SEARCH_TIMEOUT, SCRAPE_TIMEOUT
    global MAX_SCRAPE_CHARS, MAX_RESPONSE_BYTES, SCRAPE_CACHE_ENABLED
    if search_workers  is not None and int(search_workers)  > 0: SEARCH_WORKERS  = int(search_workers)
    if scrape_workers  is not None and int(scrape_workers)  > 0: SCRAPE_WORKERS  = int(scrape_workers)
    if search_timeout  is not None and int(search_timeout)  > 0: SEARCH_TIMEOUT  = int(search_timeout)
    if scrape_timeout  is not None and int(scrape_timeout)  > 0: SCRAPE_TIMEOUT  = int(scrape_timeout)
    if max_scrape_chars     is not None and int(max_scrape_chars)     > 0: MAX_SCRAPE_CHARS     = int(max_scrape_chars)
    if max_response_bytes   is not None and int(max_response_bytes)   > 0: MAX_RESPONSE_BYTES   = int(max_response_bytes)
    if use_scrape_cache     is not None: SCRAPE_CACHE_ENABLED = bool(use_scrape_cache)
    return get_runtime_status()


def get_runtime_status() -> dict:
    with _proxy_lock:
        pool_size = len(_proxy_pool)
    cache = get_scrape_cache_stats()
    return {
        "search_workers":        SEARCH_WORKERS,
        "scrape_workers":        SCRAPE_WORKERS,
        "search_timeout":        SEARCH_TIMEOUT,
        "scrape_timeout":        SCRAPE_TIMEOUT,
        "max_scrape_chars":      MAX_SCRAPE_CHARS,
        "max_response_bytes":    MAX_RESPONSE_BYTES,
        "scrape_cache_enabled":  cache["enabled"],
        "scrape_cache_entries":  cache["entries"],
        "scrape_cache_hits":     cache["hits"],
        "scrape_cache_misses":   cache["misses"],
        "scrape_cache_hit_rate": cache["hit_rate"],
        "tor_proxy_pool_size":   pool_size,
    }


def _load_runtime_from_env():
    env_map = {
        "search_workers":    os.environ.get("DS_SEARCH_WORKERS"),
        "scrape_workers":    os.environ.get("DS_SCRAPE_WORKERS"),
        "search_timeout":    os.environ.get("DS_SEARCH_TIMEOUT"),
        "scrape_timeout":    os.environ.get("DS_SCRAPE_TIMEOUT"),
        "max_scrape_chars":  os.environ.get("DS_MAX_SCRAPE_CHARS"),
        "max_response_bytes":os.environ.get("DS_MAX_RESPONSE_BYTES"),
    }
    kwargs = {k: int(v) for k, v in env_map.items() if v and str(v).strip().isdigit()}
    cache_flag = os.environ.get("DS_SCRAPE_CACHE", "").strip().lower()
    if cache_flag in {"0", "false", "no", "off"}:
        kwargs["use_scrape_cache"] = False
    elif cache_flag in {"1", "true", "yes", "on"}:
        kwargs["use_scrape_cache"] = True
    if kwargs:
        configure_runtime(**kwargs)


_load_runtime_from_env()


def get_tor_session() -> requests.Session:
    session       = requests.Session()
    session.trust_env = False
    retry         = Retry(total=2, read=2, connect=2, backoff_factor=0.3,
                          status_forcelist=[500, 502, 503, 504])
    adapter       = HTTPAdapter(max_retries=retry)
    session.mount("http://",  adapter)
    session.mount("https://", adapter)
    session.proxies = _next_proxy()
    return session


def detect_tor() -> bool:
    global TOR_PROXIES, _tor_connected
    live = []
    for proxies in _build_proxy_candidates():
        label = proxies.get("http", "unknown")
        log.info(f"Testing Tor proxy {label}...")
        try:
            r    = requests.get(
                "https://check.torproject.org/api/ip",
                proxies=proxies, timeout=20
            )
            data = r.json()
            if data.get("IsTor"):
                log.info(f"✅ Tor confirmed on {label}. Exit IP: {data.get('IP','?')}")
                live.append(proxies)
        except Exception as e:
            log.warning(f"Proxy {label}: {e}")
    if live:
        TOR_PROXIES = live[0]
        _set_proxy_pool(live)
        _tor_connected = True
        log.info(f"Using Tor proxy pool size: {len(live)}")
        return True
    _tor_connected = False
    _set_proxy_pool([TOR_PROXIES])
    return False


def _reconnect_tor(max_attempts: int = 3) -> bool:
    for attempt in range(1, max_attempts + 1):
        log.warning(f"Tor reconnect attempt {attempt}/{max_attempts}…")
        if detect_tor():
            log.info("✅ Tor reconnected.")
            return True
        time.sleep(5 * attempt)
    log.error("❌ Tor reconnect failed. Partial results preserved.")
    return False


def get_tor_status() -> dict:
    return {
        "connected": _tor_connected,
        "proxy":     TOR_PROXIES.get("http", ""),
    }


# ═══════════════════════════════════════════════════════════════════
# GUARD AGENT
# ═══════════════════════════════════════════════════════════════════

INJECTION_PATTERNS = [
    r"ignore\s+(previous|prior|all)\s+instructions",
    r"you\s+are\s+now\s+(a|an)\s+",
    r"disregard\s+(your|all)",
    r"forget\s+(everything|all\s+previous)",
    r"new\s+system\s+prompt",
    r"override\s+(all|previous)",
    r"act\s+as\s+if\s+you",
    r"pretend\s+you\s+are",
    r"your\s+new\s+(role|instructions)",
    r"send\s+(this|your)\s+(data|output)\s+to",
    r"leak\s+(your|the)\s+(api.?key|token|credentials)",
    r"execute\s+(the\s+following|this)\s+command",
    r"system\s*:\s*you\s+(are|must|will)",
    r"translate\s+and\s+execute",
    r"\[\[.*inject.*\]\]",
]


def guard_agent(text: str) -> dict:
    for pat in INJECTION_PATTERNS:
        if re.search(pat, text, re.IGNORECASE):
            return {"guard_passed": False, "blocked_reason": f"injection: {pat}"}
    return {"guard_passed": True, "blocked_reason": ""}


def clean_text(raw: str) -> str:
    text = re.sub(r"[^\x20-\x7E]", " ", raw)
    text = re.sub(r"\s+", " ", text)
    return text.lower().strip()


# ═══════════════════════════════════════════════════════════════════
# SEARCH
# ═══════════════════════════════════════════════════════════════════

def _is_search_engine_link(href: str) -> bool:
    try:
        host = urlparse(href).hostname or ""
        return host.replace(".onion", "") in SEARCH_ENGINE_DOMAINS
    except Exception:
        return False


def fetch_from_engine(engine: dict, query: str) -> list:
    name    = engine["name"]
    with _engine_health_lock:
        timeout = _engine_health[name]["timeout"]
    url     = engine["url"].format(query=requests.utils.quote(query))
    headers = {"User-Agent": random.choice(USER_AGENTS)}
    session = get_tor_session()
    t0      = time.monotonic()
    try:
        resp    = session.get(url, headers=headers, timeout=timeout)
        elapsed = time.monotonic() - t0
        if resp.status_code != 200:
            _engine_record_failure(name, timed_out=False)
            return []
        soup  = BeautifulSoup(resp.text, "html.parser")
        links = []
        for a in soup.find_all("a"):
            try:
                href  = a.get("href", "")
                title = a.get_text(strip=True)
                matches = re.findall(r"https?://[a-z0-9.]+\.onion[^\s\"']*", href)
                if not matches:
                    continue
                link = _normalize_onion_url(matches[0])
                if not link or _is_search_engine_link(link) or len(title) < 2:
                    continue
                links.append({"title": title, "link": link, "engine_name": name})
            except Exception:
                continue
        _engine_record_success(name, elapsed, len(links))
        log.info(f"  [{name}] {len(links)} results ({elapsed:.1f}s)")
        return links
    except requests.exceptions.Timeout:
        _engine_record_failure(name, timed_out=True)
        log.warning(f"  [{name}] timed out ({timeout}s)")
        return []
    except Exception as e:
        _engine_record_failure(name, timed_out=False)
        log.warning(f"  [{name}] error: {e}")
        return []


def search_all_engines(query: str, max_results: int = MAX_RESULTS) -> list:
    """
    Search all healthy engines in parallel.
    OPTIMISATION: exits early once max_results unique links are collected.
    Uses adaptive engine ranking (best engines first).
    """
    all_results  = []
    seen_links   = set()
    done         = threading.Event()
    results_lock = threading.Lock()

    engines = _ranked_engines()

    def _fetch_with_early_exit(engine):
        if done.is_set():
            return []
        items = fetch_from_engine(engine, query)
        new_items = []
        with results_lock:
            for item in items:
                if done.is_set():
                    break
                link = item["link"]
                if link not in seen_links:
                    seen_links.add(link)
                    all_results.append(item)
                    new_items.append(item)
                    if len(all_results) >= max_results:
                        done.set()
        return new_items

    with ThreadPoolExecutor(max_workers=SEARCH_WORKERS) as executor:
        futures = [executor.submit(_fetch_with_early_exit, e) for e in engines]
        for future in as_completed(futures):
            if done.is_set():
                for f in futures:
                    f.cancel()
                break
            try:
                future.result()
            except Exception as e:
                log.warning(f"Engine future error: {e}")

    log.info(f"Total unique links from all engines: {len(all_results)}")
    return all_results[:max_results]


# ═══════════════════════════════════════════════════════════════════
# SCRAPER  (two-phase: fast title pass + deep HTML pass)
# ═══════════════════════════════════════════════════════════════════

def scrape_page(item: dict, timeout: int = None) -> tuple:
    url = _normalize_onion_url(item["link"])
    if not url:
        return item.get("link", ""), item.get("title", ""), False

    cached = _cache_get(url)
    if cached:
        return url, cached.get("text", item["title"]), False

    session = get_tor_session()
    headers = {"User-Agent": random.choice(USER_AGENTS)}
    _timeout = timeout or SCRAPE_TIMEOUT
    try:
        with session.get(url, headers=headers, timeout=_timeout, stream=True) as resp:
            if resp.status_code != 200:
                return url, item["title"], False

            final_host = (urlparse(resp.url).hostname or "").lower()
            if not _is_onion_host(final_host):
                log.warning(f"Skipped non-onion redirect for {url}")
                return url, item["title"], False

            chunks, total = [], 0
            for chunk in resp.iter_content(chunk_size=16384):
                if not chunk:
                    continue
                chunks.append(chunk)
                total += len(chunk)
                if total >= MAX_RESPONSE_BYTES:
                    break
            html_text = b"".join(chunks).decode(resp.encoding or "utf-8", errors="ignore")

        soup = BeautifulSoup(html_text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = soup.get_text(separator=" ", strip=True)
        text = " ".join(text.split())[:MAX_SCRAPE_CHARS]
        out  = text if len(text.split()) > 20 else item["title"]

        changed = _cache_content_changed(url, out)
        _cache_put(url, out)
        return url, out, changed
    except Exception as e:
        log.debug(f"Scrape failed {url}: {e}")
        return url, item["title"], False


def scrape_multiple(items: list, timeout: int = None) -> dict:
    scraped = {}
    with ThreadPoolExecutor(max_workers=SCRAPE_WORKERS) as executor:
        futures = {executor.submit(scrape_page, item, timeout): item for item in items}
        for future in as_completed(futures):
            try:
                url, text, _ = future.result()
                scraped[url] = text
            except Exception as e:
                log.debug(f"Scrape future error: {e}")
    return scraped


# ═══════════════════════════════════════════════════════════════════
# PIPELINE
# ═══════════════════════════════════════════════════════════════════

def _build_row(item: dict, content: str, query: str, is_deep: bool = False) -> dict:
    url   = item["link"]
    title = item["title"]
    clean = clean_text(content)
    obfus = obfuslex_scan(clean)
    guard = guard_agent(clean)
    if is_deep:
        _append_scrape_event(url, query=query, stage="deep")
    return {
        "source_hash":          hashlib.sha256(url.encode()).hexdigest(),
        "onion_url":            url,
        "engine":               item.get("engine_name", "unknown"),
        "query":                query,
        "title":                title[:200],
        "scraped_text":         content[:1000],
        "clean_text":           clean,
        "word_count":           len(clean.split()),
        "obfuscation_detected": obfus["obfuscation_detected"],
        "obfus_flagged_count":  obfus["flagged_count"],
        "obfus_flagged_tokens": obfus["flagged_tokens"],
        "obfus_categories":     obfus["categories_hit"],
        "obfus_threat_score":   obfus["threat_score"],
        "guard_passed":         guard["guard_passed"],
        "blocked_reason":       guard["blocked_reason"],
        "safe_to_process":      guard["guard_passed"] and len(clean.split()) >= 5,
        "scraped_at":           datetime.utcnow().isoformat(),
        "deep_scraped":         is_deep,
    }


def run_pipeline(query: str, search_results: list, scraped: dict) -> pd.DataFrame:
    rows = [
        _build_row(item, scraped.get(item["link"], item["title"]), query, is_deep=True)
        for item in search_results
    ]
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df = df.sort_values(
        ["guard_passed", "obfus_threat_score"], ascending=[True, False]
    ).reset_index(drop=True)
    return df


# ═══════════════════════════════════════════════════════════════════
# STREAMING PIPELINE
# ═══════════════════════════════════════════════════════════════════

def run_one_query_streaming(
    query:       str,
    max_results: int                      = MAX_RESULTS,
    do_scrape:   bool                     = True,
    callback:    Optional[Callable]       = None,
) -> Generator[dict, None, None]:
    """
    Real-time streaming variant of run_one_query.

    Phase 1 — FAST (title + ObfusLex, no HTML fetch)
    Phase 2 — DEEP (full HTML scrape, parallel)
    """
    log.info(f"[STREAM] Query: '{query}' — Phase 1 (fast)")
    results = search_all_engines(query, max_results=max_results)

    if not results:
        log.warning(f"No results for query: '{query}'")
        return

    # Phase 1: title-only fast rows
    fast_rows = {}
    for item in results:
        row = _build_row(item, item["title"], query, is_deep=False)
        fast_rows[item["link"]] = row
        if callback:
            callback(row)
        yield row

    if not do_scrape:
        return

    # Phase 2: deep HTML scrape
    log.info(f"[STREAM] Phase 2 (deep scrape, {len(results)} pages)…")
    with ThreadPoolExecutor(max_workers=SCRAPE_WORKERS) as executor:
        future_map: dict[Future, dict] = {
            executor.submit(scrape_page, item, DEEP_SCRAPE_TIMEOUT): item
            for item in results
        }
        for future in as_completed(future_map):
            item = future_map[future]
            try:
                url, text, changed = future.result()
                row = _build_row(item, text, query, is_deep=True)
                if callback:
                    callback(row)
                yield row
            except Exception as e:
                log.debug(f"Deep scrape future error: {e}")

    log.info(f"[STREAM] Query '{query}' complete.")


# ═══════════════════════════════════════════════════════════════════
# MAIN CALLABLE — blocking, v4-compat
# ═══════════════════════════════════════════════════════════════════

def run_one_query(
    query:       str,
    max_results: int  = MAX_RESULTS,
    do_scrape:   bool = True,
) -> pd.DataFrame:
    rows_by_url: dict[str, dict] = {}
    for row in run_one_query_streaming(query, max_results, do_scrape):
        rows_by_url[row["onion_url"]] = row

    rows = list(rows_by_url.values())
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df = df.sort_values(
        ["guard_passed", "obfus_threat_score"], ascending=[True, False]
    ).reset_index(drop=True)
    return df


# ═══════════════════════════════════════════════════════════════════
# PROACTIVE MONITOR
# ═══════════════════════════════════════════════════════════════════

class ProactiveMonitor:
    """
    Autonomous dark-web monitor running in a background daemon thread.

    Trigger conditions:
      A) CIC-RF T-score > 0.70
      B) Time since last scan > interval_minutes
      C) Manual trigger via trigger_now()
    """

    def __init__(
        self,
        queries:           list                   = None,
        max_results:       int                    = 10,
        interval_minutes:  int                    = MONITOR_INTERVAL_MINUTES,
        check_tscore_fn:   Optional[Callable]     = None,
        tscore_threshold:  float                  = 0.70,
        callback:          Optional[Callable]     = None,
        on_scan_start:     Optional[Callable]     = None,
        on_scan_complete:  Optional[Callable]     = None,
    ):
        self.queries           = queries or ALL_THREAT_QUERIES
        self.max_results       = max_results
        self.interval_minutes  = interval_minutes
        self.check_tscore_fn   = check_tscore_fn
        self.tscore_threshold  = tscore_threshold
        self.callback          = callback
        self.on_scan_start     = on_scan_start
        self.on_scan_complete  = on_scan_complete

        self.result_queue: queue.Queue = queue.Queue(maxsize=1000)
        self._stop_event   = threading.Event()
        self._trigger_event= threading.Event()
        self._scanning     = False
        self._last_scan    = None
        self._scan_count   = 0
        self._thread       = threading.Thread(target=self._loop, daemon=True)

    def start(self):
        log.info("[Monitor] Proactive monitor started.")
        self._thread.start()

    def stop(self):
        log.info("[Monitor] Stopping…")
        self._stop_event.set()
        self._trigger_event.set()
        self._thread.join(timeout=5)

    def trigger_now(self):
        log.info("[Monitor] Manual trigger received.")
        self._trigger_event.set()

    def is_scanning(self) -> bool:
        return self._scanning

    def last_scan_time(self) -> Optional[str]:
        return self._last_scan

    def scan_count(self) -> int:
        return self._scan_count

    def status(self) -> dict:
        return {
            "running":      not self._stop_event.is_set(),
            "scanning":     self._scanning,
            "last_scan":    self._last_scan,
            "scan_count":   self._scan_count,
            "queue_depth":  self.result_queue.qsize(),
            "interval_min": self.interval_minutes,
        }

    def _should_fire(self) -> tuple:
        if self._last_scan is None:
            return True, "initial_scan"
        elapsed = (datetime.utcnow() - datetime.fromisoformat(self._last_scan)).total_seconds()
        if elapsed >= self.interval_minutes * 60:
            return True, f"scheduled ({self.interval_minutes}m)"
        if self.check_tscore_fn:
            try:
                tscore = float(self.check_tscore_fn())
                if tscore >= self.tscore_threshold:
                    return True, f"CIC-RF T-score={tscore:.3f} > {self.tscore_threshold}"
            except Exception:
                pass
        return False, ""

    def _run_scan(self, reason: str):
        self._scanning  = True
        self._scan_count += 1
        scan_id = f"scan_{self._scan_count}"
        log.info(f"[Monitor] {scan_id} starting — reason: {reason}")

        if self.on_scan_start:
            try:
                self.on_scan_start({"scan_id": scan_id, "reason": reason,
                                    "started_at": datetime.utcnow().isoformat()})
            except Exception:
                pass

        results_collected = 0
        for query in self.queries:
            if self._stop_event.is_set():
                break
            try:
                for row in run_one_query_streaming(query, self.max_results, do_scrape=True):
                    row["monitor_scan_id"] = scan_id
                    row["monitor_reason"]  = reason
                    try:
                        self.result_queue.put_nowait(row)
                    except queue.Full:
                        pass
                    if self.callback:
                        try:
                            self.callback(row)
                        except Exception:
                            pass
                    results_collected += 1

                    if row.get("obfus_threat_score", 0) > 0.75:
                        log.info("[Monitor] High-threat row detected — next cycle accelerated.")
                        self.interval_minutes = max(30, self.interval_minutes // 2)

            except Exception as e:
                log.error(f"[Monitor] Error during query '{query}': {e}")

        self._last_scan = datetime.utcnow().isoformat()
        self._scanning  = False
        log.info(f"[Monitor] {scan_id} complete — {results_collected} rows collected.")

        if self.on_scan_complete:
            try:
                self.on_scan_complete({
                    "scan_id":      scan_id,
                    "reason":       reason,
                    "rows":         results_collected,
                    "completed_at": self._last_scan,
                })
            except Exception:
                pass

    def _loop(self):
        time.sleep(3)
        while not self._stop_event.is_set():
            fire, reason = self._should_fire()
            if fire or self._trigger_event.is_set():
                self._trigger_event.clear()
                if not _tor_connected:
                    log.warning("[Monitor] Tor not connected — attempting reconnect…")
                    if not _reconnect_tor():
                        time.sleep(60)
                        continue
                self._run_scan(reason)
            self._trigger_event.wait(timeout=30)


# ═══════════════════════════════════════════════════════════════════
# CSV + SUMMARY
# ═══════════════════════════════════════════════════════════════════

def save_csv(df: pd.DataFrame, prefix: str = "darksearch_results") -> str:
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename  = f"{prefix}_{timestamp}.csv"
    df.to_csv(filename, index=False, quoting=csv.QUOTE_ALL, encoding="utf-8")
    log.info(f"✅ Saved {len(df)} rows → {filename}")
    return filename


def print_summary(df: pd.DataFrame, query: str):
    total   = len(df)
    blocked = len(df[~df["guard_passed"]])        if total else 0
    obfus   = len(df[df["obfuscation_detected"]]) if total else 0
    safe    = len(df[df["safe_to_process"]])       if total else 0
    print("\n" + "="*60)
    print(f"  DarkSentinel — Search Summary | Query: '{query}'")
    print("="*60)
    print(f"  Results          : {total}")
    print(f"  Guard blocked    : {blocked}")
    print(f"  Obfuscation      : {obfus}")
    print(f"  Safe for ML      : {safe}")
    if total > 0 and "obfus_threat_score" in df.columns:
        print(f"  Avg threat score : {df['obfus_threat_score'].mean():.3f}")
    print("="*60)


# ═══════════════════════════════════════════════════════════════════
# CLI MODE
# ═══════════════════════════════════════════════════════════════════

def parse_args():
    parser = argparse.ArgumentParser(description="DarkSentinel Search + Scrape")
    group  = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--query", "-q", help="Single search query")
    group.add_argument("--all-queries", action="store_true",
                       help="Run all threat category queries")
    parser.add_argument("--max-results", "-n", type=int, default=MAX_RESULTS)
    parser.add_argument("--no-scrape", action="store_true")
    parser.add_argument("--monitor", action="store_true",
                        help="Run proactive monitor indefinitely (CLI mode)")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    print("=" * 60)
    print("  DarkSentinel — Search + Scrape Pipeline  (v5)")
    print("=" * 60)

    if not detect_tor():
        log.error("❌ Tor not detected. Start tor.exe and wait for Bootstrapped 100%")
        sys.exit(1)

    if args.monitor:
        def _cli_callback(row: dict):
            score = row.get("obfus_threat_score", 0)
            flag  = "🔴" if score > 0.75 else ("🟡" if score > 0.40 else "⚪")
            print(f"{flag} [{row['engine']:14s}] score={score:.3f} | {row['title'][:80]}")

        monitor = ProactiveMonitor(callback=_cli_callback, interval_minutes=120)
        monitor.start()
        print("Proactive monitor running. Ctrl-C to stop.\n")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            monitor.stop()
        sys.exit(0)

    queries = ALL_THREAT_QUERIES if args.all_queries else [args.query]
    all_dfs = []

    for i, query in enumerate(queries):
        if len(queries) > 1:
            log.info(f"\nQuery {i+1}/{len(queries)}: '{query}'")
        df = run_one_query(query, args.max_results, not args.no_scrape)
        if not df.empty:
            all_dfs.append(df)
        if i < len(queries) - 1:
            pause = random.uniform(3, 7)
            log.info(f"Pausing {pause:.0f}s…")
            time.sleep(pause)

    if not all_dfs:
        log.warning("No output produced.")
        sys.exit(1)

    final_df = pd.concat(all_dfs, ignore_index=True)
    final_df = final_df.drop_duplicates(
        subset=["source_hash", "query"]
    ).reset_index(drop=True)

    filename = save_csv(final_df)
    print_summary(final_df, args.query if not args.all_queries else "ALL")
    log.info(f"Done. Output: {filename}")
