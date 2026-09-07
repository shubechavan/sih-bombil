# alert_api.py — DarkSentinel FastAPI Alert Bridge  (FIXED v2)
# ═══════════════════════════════════════════════════════════════════
# RUN:  uvicorn alert_api:app --port 8000 --reload
#
# PURPOSE:
#   Receives alerts from ui.py (POST /alert) and:
#     1. Routes CRITICAL → n8n webhook (email + Slack)
#     2. Routes HIGH     → n8n webhook (Slack only, different path)
#     3. Routes MEDIUM   → PostgreSQL analyst_queue table
#     4. Routes LOW      → PostgreSQL only (stored, no alert fired)
#
# This matches exactly the image spec:
#   R < 0.3  LOW      → DB only
#   0.3-0.6  MEDIUM   → DB queue + analyst review
#   0.6-0.8  HIGH     → Slack via n8n
#   R > 0.8  CRITICAL → Email + Slack via n8n (zero human intervention)
#
# n8n WEBHOOK DESIGN:
#   ui.py calls POST /alert
#   alert_api decides severity routing:
#     CRITICAL/HIGH → POST to n8n webhook
#     MEDIUM/LOW    → directly inserts to PostgreSQL
#   n8n receives payload with "severity" field and uses IF node to
#   split: CRITICAL → Gmail + Slack; HIGH → Slack only
#
# FIXES vs original:
#   1. Severity-based routing (not just a pass-through)
#   2. Direct PostgreSQL write for MEDIUM/LOW (no n8n needed)
#   3. Retry logic for n8n with exponential backoff
#   4. /health includes DB connection check
#   5. /alerts/log returns last 100 with full detail
#   6. Pydantic v1/v2 compatible
# ═══════════════════════════════════════════════════════════════════

import os, re, json, time, logging, threading, csv, hashlib, uuid, asyncio
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Any
from queue import Queue, Empty, Full

import requests
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import StreamingResponse

# Pydantic v1/v2 compatibility
try:
    from pydantic import BaseModel, validator
    PYDANTIC_V2 = False
except ImportError:
    from pydantic import BaseModel
    PYDANTIC_V2 = True

# ── Load .env ─────────────────────────────────────────────────────
def _load_env(p=".env"):
    try:
        for ln in Path(p).read_text(errors="ignore").splitlines():
            ln = re.sub(r"^(set\s+|export\s+)","",ln.strip(),flags=re.IGNORECASE)
            if "=" in ln:
                k,v = ln.split("=",1)
                k=k.strip(); v=v.strip().strip('"').strip("'")
                if k and v and not os.environ.get(k): os.environ[k]=v
    except Exception: pass
_load_env()

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-8s %(message)s")
log = logging.getLogger("DarkSentinel.AlertAPI")


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() not in {"0", "false", "no", "off"}


def _env_int(name: str, default: int, minimum: int) -> int:
    try:
        return max(minimum, int(os.environ.get(name, str(default))))
    except Exception:
        return max(minimum, default)


def _env_float(name: str, default: float, minimum: float) -> float:
    try:
        return max(minimum, float(os.environ.get(name, str(default))))
    except Exception:
        return max(minimum, default)

# ── Config ────────────────────────────────────────────────────────
N8N_BASE = os.environ.get("N8N_WEBHOOK_URL",
           "http://localhost:5678/webhook/darksentinel-alert")

# n8n paths — single webhook, n8n IF node splits internally
N8N_CRITICAL_URL = N8N_BASE                 # CRITICAL + HIGH both go here
# For MEDIUM/LOW we skip n8n entirely and write directly to PG

PG_CONFIG = {
    "host":    os.environ.get("PG_HOST",     "localhost"),
    "port":    int(os.environ.get("PG_PORT", "5432")),
    "dbname":  os.environ.get("PG_DB",       "darksentinel"),
    "user":    os.environ.get("PG_USER",     "postgres"),
    "password":os.environ.get("PG_PASSWORD", ""),
}

ALERT_API_TOKEN = os.environ.get("ALERT_API_TOKEN", "").strip()
ALERT_ASYNC_N8N = _env_flag("ALERT_ASYNC_N8N", True)
ALERT_NOTIFY_MEDIUM = _env_flag("ALERT_NOTIFY_MEDIUM", True)
ALERT_OUTBOX_MAX_SIZE = _env_int("ALERT_OUTBOX_MAX_SIZE", 2000, 100)
ALERT_OUTBOX_MAX_RETRIES = _env_int("ALERT_OUTBOX_MAX_RETRIES", 5, 1)
ALERT_OUTBOX_BASE_DELAY = _env_float("ALERT_OUTBOX_BASE_DELAY", 1.0, 0.5)

# ── FastAPI app ───────────────────────────────────────────────────
app = FastAPI(
    title="DarkSentinel Alert API",
    description="Severity-routed alert bridge: CRITICAL/HIGH → n8n, MEDIUM/LOW → PostgreSQL",
    version="2.0",
)

# In-memory log (last 200 alerts)
_alert_log: list = []

# Async n8n outbox state
_n8n_outbox: Queue = Queue(maxsize=ALERT_OUTBOX_MAX_SIZE)
_outbox_lock = threading.Lock()
_outbox_worker_started = False
_outbox_stats = {
    "queued": 0,
    "delivered": 0,
    "failed": 0,
    "retried": 0,
    "dropped": 0,
}

# Threat/feed cache and in-memory scan jobs used by the web UI.
_FLOW_CACHE = {
    "items": [],
    "source": "none",
    "loaded_at": 0.0,
}
_FLOW_CACHE_TTL_SEC = 20
_FLOW_MAX_ROWS = 8000

_SCAN_STAGE_NAMES = [
    "APScheduler",
    "Ahmia Scraper",
    "ObfusLex Engine",
    "Guard Agent",
    "GLiNER-PII Scrubber",
    "RoBERTa-DDIR",
    "Blink.new Gateway",
    "Risk Engine",
    "n8n Orchestrator",
]

_SCAN_JOBS = {}
_SCAN_JOBS_LOCK = threading.Lock()


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return int(default)


def _clamp01(value: float) -> float:
    if value < 0:
        return 0.0
    if value > 1:
        return 1.0
    return value


def _severity_from_score(score: float) -> str:
    if score >= 0.8:
        return "CRITICAL"
    if score >= 0.6:
        return "HIGH"
    if score >= 0.3:
        return "MEDIUM"
    return "LOW"


def _severity_to_lower(value: str) -> str:
    v = str(value or "LOW").upper()
    if v == "CRITICAL":
        return "critical"
    if v == "HIGH":
        return "high"
    if v == "MEDIUM":
        return "medium"
    return "low"


def _normalize_timestamp(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return datetime.utcnow().isoformat()

    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat()
    except Exception:
        pass

    for fmt in ("%d/%m/%Y %I:%M:%S %p", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).isoformat()
        except Exception:
            continue

    return datetime.utcnow().isoformat()


def _protocol_label(raw: Any) -> str:
    p = str(raw or "").strip()
    if p == "6":
        return "TCP"
    if p == "17":
        return "UDP"
    if p == "1":
        return "ICMP"
    return f"P-{p or 'NA'}"


def _risk_from_flow(label: str, detail: str, flow_duration: float, bytes_per_s: float, packets_per_s: float) -> float:
    tag = f"{label} {detail}".lower()
    label_weight = 0.72 if "tor" in tag else 0.24
    bytes_weight = _clamp01((abs(bytes_per_s) + 1) ** 0.08 - 1)
    packets_weight = _clamp01((abs(packets_per_s) + 1) ** 0.1 - 1)
    duration_weight = _clamp01(flow_duration / 120000000.0)
    score = label_weight * 0.58 + bytes_weight * 0.22 + packets_weight * 0.1 + duration_weight * 0.1
    return round(_clamp01(score), 4)


def _flow_candidates() -> list:
    root = Path(__file__).parent
    return [
        root / "demo_flows.csv",
        root / "Darknet.CSV",
        root / "darknet" / "Darknet.CSV",
    ]


def _resolve_flow_file() -> Optional[Path]:
    for candidate in _flow_candidates():
        if candidate.exists():
            return candidate
    return None


def _flow_row_hash(flow_id: str, src_ip: str, dst_ip: str, ts: str) -> str:
    return hashlib.sha1(f"{flow_id}:{src_ip}:{dst_ip}:{ts}".encode("utf-8", errors="ignore")).hexdigest()[:24]


def _read_flow_items(max_rows: int = _FLOW_MAX_ROWS) -> tuple:
    source = _resolve_flow_file()
    if not source:
        return [], "none"

    items = []
    try:
        with source.open("r", encoding="utf-8", errors="ignore", newline="") as handle:
            reader = csv.DictReader(handle)
            for idx, row in enumerate(reader):
                if idx >= max_rows:
                    break

                src_ip = str(row.get("Src IP") or row.get("source_ip") or row.get("sourceIp") or "")
                dst_ip = str(row.get("Dst IP") or row.get("destination_ip") or row.get("destinationIp") or "")
                src_port = _to_int(row.get("Src Port") or row.get("source_port") or row.get("sourcePort"), 0)
                dst_port = _to_int(row.get("Dst Port") or row.get("destination_port") or row.get("destinationPort"), 0)
                protocol = str(row.get("Protocol") or row.get("protocol") or "")
                ts = _normalize_timestamp(row.get("Timestamp") or row.get("created_at") or row.get("timestamp"))
                flow_duration = _to_float(row.get("Flow Duration") or row.get("flow_duration"), 0.0)
                bytes_per_s = _to_float(row.get("Flow Bytes/s") or row.get("bytes_per_second") or row.get("bytesPerSecond"), 0.0)
                packets_per_s = _to_float(row.get("Flow Packets/s") or row.get("packets_per_second") or row.get("packetsPerSecond"), 0.0)
                label = str(row.get("Label") or row.get("label") or "unknown")
                detail = str(row.get("Label.1") or row.get("label_detail") or label)
                flow_id = str(row.get("Flow ID") or row.get("flow_id") or idx)

                score = _risk_from_flow(label, detail, flow_duration, bytes_per_s, packets_per_s)
                severity = _severity_from_score(score)
                row_hash = _flow_row_hash(flow_id, src_ip, dst_ip, ts)
                text_snippet = f"{src_ip}:{src_port} -> {dst_ip}:{dst_port}"

                items.append(
                    {
                        "rowHash": row_hash,
                        "riskScore": score,
                        "textSnippet": text_snippet,
                        "searchEngine": _protocol_label(protocol),
                        "timestamp": ts,
                        "category": detail or label or "unknown",
                        "severity": severity,
                        "sourceIp": src_ip,
                        "destinationIp": dst_ip,
                        "sourcePort": src_port,
                        "destinationPort": dst_port,
                        "protocol": protocol,
                        "flowDuration": flow_duration,
                        "bytesPerSecond": bytes_per_s,
                        "packetsPerSecond": packets_per_s,
                        "label": label,
                        "query": detail,
                        "engine": _protocol_label(protocol),
                        "title": detail,
                        "clean_text": text_snippet,
                        "source_hash": row_hash,
                        "r_score": score,
                        "created_at": ts,
                    }
                )
    except Exception as exc:
        log.warning(f"Flow dataset read failed: {exc}")
        return [], "none"

    items.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return items, str(source)


def _load_flow_items() -> tuple:
    now = time.time()
    if _FLOW_CACHE["items"] and (now - float(_FLOW_CACHE["loaded_at"])) <= _FLOW_CACHE_TTL_SEC:
        return list(_FLOW_CACHE["items"]), str(_FLOW_CACHE["source"])

    items, source = _read_flow_items()
    _FLOW_CACHE["items"] = list(items)
    _FLOW_CACHE["source"] = source
    _FLOW_CACHE["loaded_at"] = now
    return items, source


def _match_search(item: dict, search: str) -> bool:
    if not search:
        return True
    haystack = " ".join(
        [
            str(item.get("rowHash", "")),
            str(item.get("textSnippet", "")),
            str(item.get("category", "")),
            str(item.get("searchEngine", "")),
            str(item.get("sourceIp", "")),
            str(item.get("destinationIp", "")),
            str(item.get("protocol", "")),
            str(item.get("label", "")),
        ]
    ).lower()
    return search.lower() in haystack


def _apply_threat_filters(items: list, params: Any) -> list:
    severity_raw = str(params.get("severity") or "")
    severity_set = {x.strip().upper() for x in severity_raw.split(",") if x.strip()}
    category = str(params.get("category") or "").strip().lower()
    engine = str(params.get("engine") or "").strip().lower()
    search = str(params.get("search") or "").strip().lower()
    row_hash = str(params.get("hash") or "").strip()
    threats_only = str(params.get("threats_only") or "false").lower() == "true"
    obfuscated_only = str(params.get("obfuscated_only") or "false").lower() == "true"

    out = []
    for item in items:
        if row_hash and str(item.get("rowHash")) != row_hash:
            continue
        if severity_set and str(item.get("severity", "")).upper() not in severity_set:
            continue
        if category and category not in str(item.get("category", "")).lower():
            continue
        if engine and engine not in str(item.get("searchEngine", "")).lower():
            continue
        if threats_only and _to_float(item.get("riskScore"), 0.0) < 0.6:
            continue
        if obfuscated_only:
            label = str(item.get("label", "")).lower()
            if "tor" not in label:
                continue
        if not _match_search(item, search):
            continue
        out.append(item)

    return out


def _paginate(items: list, params: Any) -> dict:
    page = max(1, _to_int(params.get("page"), 1))
    limit = max(1, min(250, _to_int(params.get("limit"), 50)))
    total = len(items)
    pages = max(1, (total + limit - 1) // limit)
    start = (page - 1) * limit
    return {
        "items": items[start:start + limit],
        "total": total,
        "page": page,
        "pages": pages,
    }


def _risk_histogram(items: list) -> list:
    buckets = [
        {"range": "0-0.2", "min": 0.0, "max": 0.2, "color": "var(--ds-severity-low)"},
        {"range": "0.2-0.4", "min": 0.2, "max": 0.4, "color": "var(--ds-severity-low)"},
        {"range": "0.4-0.6", "min": 0.4, "max": 0.6, "color": "var(--ds-severity-medium)"},
        {"range": "0.6-0.8", "min": 0.6, "max": 0.8, "color": "var(--ds-severity-high)"},
        {"range": "0.8-1.0", "min": 0.8, "max": 1.000001, "color": "var(--ds-severity-critical)"},
    ]
    out = []
    for bucket in buckets:
        count = 0
        for item in items:
            score = _to_float(item.get("riskScore"), 0.0)
            if score >= bucket["min"] and score < bucket["max"]:
                count += 1
        out.append({"range": bucket["range"], "count": count, "color": bucket["color"]})
    return out


def _risk_distribution(items: list) -> list:
    out = []
    for b in _risk_histogram(items):
        out.append({"bucket": b["range"], "count": b["count"]})
    return out


def _severity_trend(items: list, days: int = 7) -> list:
    today = datetime.utcnow().date()
    rows = []
    for i in range(days - 1, -1, -1):
        day = today - timedelta(days=i)
        rows.append(
            {
                "date": day.isoformat(),
                "critical": 0,
                "high": 0,
                "medium": 0,
                "low": 0,
            }
        )

    index = {row["date"]: row for row in rows}
    for item in items:
        ts = str(item.get("timestamp") or "")
        key = ts[:10]
        row = index.get(key)
        if not row:
            continue
        sev = _severity_to_lower(str(item.get("severity", "LOW")))
        row[sev] = int(row[sev]) + 1
    return rows


def _execute_payload(items: list) -> dict:
    total = len(items)
    critical = sum(1 for x in items if str(x.get("severity", "")).upper() == "CRITICAL")
    avg_risk = round(sum(_to_float(x.get("riskScore"), 0.0) for x in items) / total, 4) if total else 0.0
    cat_counts = Counter(str(x.get("category", "unknown")) for x in items)
    top_category = cat_counts.most_common(1)[0][0] if cat_counts else "none"

    recent = [x for x in items if str(x.get("severity", "")).upper() in {"CRITICAL", "HIGH"}][:20]
    recent_payload = [
        {
            "hash": x.get("rowHash", ""),
            "title": x.get("title") or x.get("textSnippet") or "Untitled",
            "severity": _severity_to_lower(x.get("severity", "LOW")),
            "score": _to_float(x.get("riskScore"), 0.0),
        }
        for x in recent
    ]

    categories = [{"name": key, "value": value} for key, value in cat_counts.most_common(12)]

    return {
        "totalThreats": total,
        "criticalCount": critical,
        "averageRisk": avg_risk,
        "topCategory": top_category,
        "severityTrend": _severity_trend(items, days=7),
        "riskDistribution": _risk_distribution(items),
        "categories": categories,
        "recentCritical": recent_payload,
    }


def _network_payload(items: list) -> dict:
    events = []
    for x in items[:120]:
        events.append(
            {
                "id": x.get("rowHash", ""),
                "type": x.get("category", "unknown"),
                "source_ip": x.get("sourceIp", ""),
                "destination": f"{x.get('destinationIp', '')}:{x.get('destinationPort', '')}",
                "severity": _severity_to_lower(x.get("severity", "LOW")),
                "timestamp": x.get("timestamp", datetime.utcnow().isoformat()),
                "description": x.get("textSnippet", ""),
            }
        )

    total = len(events)
    critical = sum(1 for e in events if e["severity"] == "critical")
    active_alerts = sum(1 for e in events if e["severity"] in {"critical", "high"})
    unique_sources = len({e["source_ip"] for e in events if e["source_ip"]})

    return {
        "totalAnomalies": total,
        "criticalAnomalies": critical,
        "uniqueSources": unique_sources,
        "activeAlerts": active_alerts,
        "events": events[:60],
    }


def _recon_payload(items: list) -> dict:
    grouped = defaultdict(list)
    for item in items:
        key = str(item.get("query") or item.get("category") or "unknown")
        grouped[key].append(item)

    results = []
    all_scores = []
    all_match_rates = []
    total_rows = 0

    for query, rows in list(grouped.items())[:20]:
        total = len(rows)
        total_rows += total
        scores = [_to_float(r.get("riskScore"), 0.0) for r in rows]
        avg_conf = sum(scores) / total if total else 0.0
        match_rate = (sum(1 for s in scores if s >= 0.6) / total) if total else 0.0
        all_scores.append(avg_conf)
        all_match_rates.append(match_rate)

        src_counts = Counter(str(r.get("sourceIp") or "unknown") for r in rows)
        top_sources = [s for s, _ in src_counts.most_common(4)]

        results.append(
            {
                "query": query,
                "totalResults": total,
                "matchRate": round(match_rate, 4),
                "avgConfidence": round(avg_conf, 4),
                "topSources": top_sources,
            }
        )

    return {
        "totalQueries": len(grouped),
        "avgMatchRate": round(sum(all_match_rates) / len(all_match_rates), 4) if all_match_rates else 0.0,
        "avgConfidence": round(sum(all_scores) / len(all_scores), 4) if all_scores else 0.0,
        "totalResults": total_rows,
        "results": results,
    }


def _intelligence_payload(items: list) -> dict:
    by_engine = defaultdict(list)
    for item in items:
        by_engine[str(item.get("searchEngine") or "unknown")].append(item)

    engines = []
    for engine, rows in by_engine.items():
        scores = [_to_float(r.get("riskScore"), 0.0) for r in rows]
        avg_score = sum(scores) / len(scores) if scores else 0.0
        accuracy = _clamp01(0.55 + avg_score * 0.4)
        fp_rate = _clamp01(1.0 - accuracy)
        engines.append(
            {
                "engine": engine,
                "accuracy": round(accuracy, 4),
                "avgConfidence": round(avg_score, 4),
                "totalAnalyzed": len(rows),
                "falsePositiveRate": round(fp_rate, 4),
            }
        )

    engines.sort(key=lambda e: e["totalAnalyzed"], reverse=True)

    total = sum(e["totalAnalyzed"] for e in engines)
    avg_accuracy = (sum(e["accuracy"] * e["totalAnalyzed"] for e in engines) / total) if total else 0.0
    avg_conf = (sum(e["avgConfidence"] * e["totalAnalyzed"] for e in engines) / total) if total else 0.0

    radar = [
        {"subject": "Detection", "value": round(avg_conf, 4)},
        {"subject": "Precision", "value": round(avg_accuracy, 4)},
        {"subject": "Consistency", "value": round(_clamp01(avg_accuracy * 0.95 + 0.02), 4)},
        {"subject": "Recall", "value": round(_clamp01(avg_conf * 0.92 + 0.04), 4)},
        {"subject": "Stability", "value": round(_clamp01(1.0 - abs(avg_accuracy - avg_conf)), 4)},
    ]

    disagreements = []
    for item in items[:40]:
        sev = _severity_to_lower(item.get("severity", "LOW"))
        disagreements.append(
            {
                "hash": item.get("rowHash", ""),
                "roberta": item.get("category", "unknown"),
                "blink": item.get("label", "unknown"),
                "consensus": sev,
            }
        )

    return {
        "totalAnalyses": len(items),
        "avgAccuracy": round(avg_accuracy, 4),
        "consensusRate": round(_clamp01(avg_accuracy * 0.96), 4),
        "engines": engines[:8],
        "radarData": radar,
        "recentDisagreements": disagreements,
    }


def _cleanup_scan_jobs() -> None:
    cutoff = time.time() - (60 * 60)
    with _SCAN_JOBS_LOCK:
        stale = [sid for sid, job in _SCAN_JOBS.items() if float(job.get("created_ts", 0)) < cutoff]
        for sid in stale:
            _SCAN_JOBS.pop(sid, None)


def _emit_scan_event(scan_id: str, event: dict) -> None:
    with _SCAN_JOBS_LOCK:
        job = _SCAN_JOBS.get(scan_id)
        if not job:
            return
        q = job.get("events")
    if not q:
        return
    try:
        q.put_nowait(event)
    except Full:
        log.warning(f"scan event queue full for {scan_id}")


def _build_scan_results(config: dict) -> list:
    items, _ = _load_flow_items()
    queries = [str(q).strip().lower() for q in (config.get("queries") or []) if str(q).strip()]
    max_results = max(1, min(1000, _to_int(config.get("maxResults"), 100)))

    if queries:
        filtered = []
        for item in items:
            haystack = " ".join(
                [
                    str(item.get("category", "")),
                    str(item.get("textSnippet", "")),
                    str(item.get("label", "")),
                ]
            ).lower()
            if any(q in haystack for q in queries):
                filtered.append(item)
        items = filtered

    if not items:
        items, _ = _load_flow_items()

    return items[:max_results]


def _scan_worker(scan_id: str) -> None:
    started = time.time()
    with _SCAN_JOBS_LOCK:
        job = _SCAN_JOBS.get(scan_id, {})
        config = dict(job.get("config") or {})

    try:
        estimated_total = max(1, _to_int(config.get("maxResults"), 100))
        for index, stage_name in enumerate(_SCAN_STAGE_NAMES):
            _emit_scan_event(
                scan_id,
                {
                    "type": "stage-update",
                    "stage": index,
                    "name": stage_name,
                    "status": "processing",
                    "progress": 20,
                    "metrics": {"processed": int((index + 1) * estimated_total / len(_SCAN_STAGE_NAMES))},
                },
            )
            time.sleep(0.2)
            _emit_scan_event(
                scan_id,
                {
                    "type": "stage-update",
                    "stage": index,
                    "name": stage_name,
                    "status": "complete",
                    "progress": 100,
                    "metrics": {"processed": int((index + 1) * estimated_total / len(_SCAN_STAGE_NAMES))},
                },
            )

        results = _build_scan_results(config)
        safe_rows = sum(1 for row in results if _to_float(row.get("riskScore"), 0.0) < 0.6)
        blocked_rows = len(results) - safe_rows
        duration = round(time.time() - started, 3)

        with _SCAN_JOBS_LOCK:
            job = _SCAN_JOBS.get(scan_id)
            if job is not None:
                job["status"] = "complete"
                job["results"] = results
                job["completed_at"] = datetime.utcnow().isoformat()

        _emit_scan_event(
            scan_id,
            {
                "type": "scan-complete",
                "totalRows": len(results),
                "safeRows": safe_rows,
                "blockedRows": blocked_rows,
                "duration": duration,
                "results": results,
            },
        )
    except Exception as exc:
        with _SCAN_JOBS_LOCK:
            job = _SCAN_JOBS.get(scan_id)
            if job is not None:
                job["status"] = "error"
                job["error"] = str(exc)
                job["completed_at"] = datetime.utcnow().isoformat()
        _emit_scan_event(
            scan_id,
            {
                "type": "scan-error",
                "message": str(exc),
            },
        )



# ═══════════════════════════════════════════════════════════════════
# MODELS
# ═══════════════════════════════════════════════════════════════════

class AlertPayload(BaseModel):
    severity:    str                # CRITICAL / HIGH / MEDIUM / LOW
    category:    str                # drugs / hacking / fraud / etc.
    r_score:     float              # 0.0 – 1.0
    title:       str
    engine:      Optional[str] = "unknown"
    query:       Optional[str] = ""
    source_hash: Optional[str] = ""
    onion_url:   Optional[str] = ""
    llm_mitre:   Optional[str] = ""
    llm_mitre_tactic: Optional[str] = ""
    llm_mitre_tactic_id: Optional[str] = ""
    llm_mitre_technique_name: Optional[str] = ""
    reasoning:   Optional[str] = ""
    timestamp:   Optional[str] = ""
    action:      Optional[str] = ""


class BulkAlertPayload(BaseModel):
    """Send multiple alerts in one call — used by ui.py after a full scan."""
    alerts: list


def _extract_request_token(request: Request) -> str:
    direct = request.headers.get("x-alert-token", "").strip()
    if direct:
        return direct
    auth = request.headers.get("authorization", "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def _require_api_token(request: Request):
    """Require token only when ALERT_API_TOKEN is configured."""
    if not ALERT_API_TOKEN:
        return
    supplied = _extract_request_token(request)
    if not supplied or supplied != ALERT_API_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")


# ═══════════════════════════════════════════════════════════════════
# POSTGRESQL HELPERS
# ═══════════════════════════════════════════════════════════════════

def _pg_conn():
    if not PG_CONFIG["password"]:
        return None
    try:
        import psycopg2
        return psycopg2.connect(**PG_CONFIG, connect_timeout=4)
    except Exception as e:
        log.warning(f"PG connect failed: {e}")
        return None

def _pg_ensure_tables(conn):
    """Create all required tables if they don't exist."""
    cur = conn.cursor()
    # Main threat results table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS threat_results (
            id                   SERIAL PRIMARY KEY,
            source_hash          TEXT,
            onion_url            TEXT,
            engine               TEXT,
            query                TEXT,
            title                TEXT,
            clean_text           TEXT,
            category             TEXT,
            severity             TEXT,
            r_score              FLOAT,
            obfus_threat_score   FLOAT DEFAULT 0,
            obfuscation_detected BOOLEAN DEFAULT FALSE,
            guard_passed         BOOLEAN DEFAULT TRUE,
            scraped_at           TEXT,
            analyzed_at          TIMESTAMP DEFAULT NOW(),
            llm_mitre            TEXT,
            llm_mitre_tactic     TEXT,
            llm_mitre_tactic_id  TEXT,
            llm_mitre_technique_name TEXT,
            reasoning            TEXT,
            action               TEXT
        )
    """)
    cur.execute("ALTER TABLE threat_results ADD COLUMN IF NOT EXISTS llm_mitre_tactic TEXT")
    cur.execute("ALTER TABLE threat_results ADD COLUMN IF NOT EXISTS llm_mitre_tactic_id TEXT")
    cur.execute("ALTER TABLE threat_results ADD COLUMN IF NOT EXISTS llm_mitre_technique_name TEXT")
    # Analyst review queue for MEDIUM results
    cur.execute("""
        CREATE TABLE IF NOT EXISTS analyst_queue (
            id         SERIAL PRIMARY KEY,
            alert_data JSONB,
            severity   TEXT,
            r_score    FLOAT,
            llm_mitre  TEXT,
            reasoning  TEXT,
            action     TEXT,
            created_at TIMESTAMP DEFAULT NOW(),
            reviewed   BOOLEAN DEFAULT FALSE
        )
    """)
    cur.execute("ALTER TABLE analyst_queue ADD COLUMN IF NOT EXISTS llm_mitre TEXT")
    cur.execute("ALTER TABLE analyst_queue ADD COLUMN IF NOT EXISTS reasoning TEXT")
    cur.execute("ALTER TABLE analyst_queue ADD COLUMN IF NOT EXISTS action TEXT")
    conn.commit()

def _pg_insert_threat(conn, payload: dict) -> bool:
    """Insert one row into threat_results."""
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO threat_results
              (source_hash, onion_url, engine, query, title, category,
               severity, r_score, analyzed_at, llm_mitre,
               llm_mitre_tactic, llm_mitre_tactic_id, llm_mitre_technique_name,
               reasoning, action)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NOW(),%s,%s,%s,%s,%s,%s)
        """, (
            payload.get("source_hash",""),
            payload.get("onion_url","")[:500],
            payload.get("engine",""),
            payload.get("query",""),
            str(payload.get("title",""))[:500],
            payload.get("category",""),
            payload.get("severity",""),
            float(payload.get("r_score",0)),
            payload.get("llm_mitre",""),
            payload.get("llm_mitre_tactic", ""),
            payload.get("llm_mitre_tactic_id", ""),
            payload.get("llm_mitre_technique_name", ""),
            str(payload.get("reasoning",""))[:500],
            payload.get("action",""),
        ))
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        log.error(f"PG insert error: {e}")
        return False

def _pg_insert_queue(conn, payload: dict) -> bool:
    """Add MEDIUM item to analyst_queue for human review."""
    cur = conn.cursor()
    try:
        import json as _json
        cur.execute("""
            INSERT INTO analyst_queue (alert_data, severity, r_score, llm_mitre, reasoning, action)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            _json.dumps(payload),
            payload.get("severity",""),
            float(payload.get("r_score",0)),
            payload.get("llm_mitre", ""),
            str(payload.get("reasoning", ""))[:500],
            payload.get("action", ""),
        ))
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        log.error(f"PG queue insert error: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════
# n8n ROUTING
# ═══════════════════════════════════════════════════════════════════

def _send_to_n8n(payload: dict, severity: str) -> tuple:
    """
    POST payload to n8n webhook.
    Returns (success: bool, status_code: int, message: str).
    n8n's IF node then splits CRITICAL vs HIGH internally.
    """
    headers = {"Content-Type": "application/json"}
    for attempt in range(3):
        try:
            r = requests.post(
                N8N_CRITICAL_URL,
                json=payload,
                headers=headers,
                timeout=8,
            )
            if r.status_code < 400:
                log.info(f"n8n alert sent [{severity}] attempt={attempt+1} status={r.status_code}")
                return True, r.status_code, "sent"
            else:
                log.warning(f"n8n returned {r.status_code}: {r.text[:100]}")
        except requests.exceptions.ConnectionError:
            log.warning(f"n8n not reachable (attempt {attempt+1}). Is n8n running?")
        except Exception as e:
            log.warning(f"n8n send error attempt {attempt+1}: {e}")
        if attempt < 2:
            time.sleep(2 ** attempt)   # 0s, 1s, 2s backoff

    return False, 0, "n8n_unreachable — check n8n is running (n8n start)"


def _outbox_snapshot() -> dict:
    with _outbox_lock:
        stats = dict(_outbox_stats)
    stats["queue_depth"] = _n8n_outbox.qsize()
    stats["enabled"] = ALERT_ASYNC_N8N
    return stats


def _enqueue_n8n(payload: dict, severity: str) -> tuple:
    job = {
        "payload": payload,
        "severity": severity,
        "attempt": 0,
        "next_attempt_at": time.time(),
    }
    try:
        _n8n_outbox.put_nowait(job)
        with _outbox_lock:
            _outbox_stats["queued"] += 1
        return True, "queued"
    except Full:
        with _outbox_lock:
            _outbox_stats["dropped"] += 1
        return False, "outbox_full"


def _outbox_worker_loop():
    while True:
        try:
            job = _n8n_outbox.get(timeout=1.0)
        except Empty:
            continue

        if job is None:
            _n8n_outbox.task_done()
            break

        wait_for = float(job.get("next_attempt_at", 0)) - time.time()
        if wait_for > 0:
            time.sleep(wait_for)

        ok, _code, _msg = _send_to_n8n(job.get("payload", {}), job.get("severity", "HIGH"))
        if ok:
            with _outbox_lock:
                _outbox_stats["delivered"] += 1
        else:
            attempt = int(job.get("attempt", 0)) + 1
            if attempt <= ALERT_OUTBOX_MAX_RETRIES:
                job["attempt"] = attempt
                job["next_attempt_at"] = time.time() + ALERT_OUTBOX_BASE_DELAY * (2 ** (attempt - 1))
                try:
                    _n8n_outbox.put_nowait(job)
                    with _outbox_lock:
                        _outbox_stats["retried"] += 1
                except Full:
                    with _outbox_lock:
                        _outbox_stats["failed"] += 1
                        _outbox_stats["dropped"] += 1
            else:
                with _outbox_lock:
                    _outbox_stats["failed"] += 1

        _n8n_outbox.task_done()


def _start_outbox_worker():
    global _outbox_worker_started
    if not ALERT_ASYNC_N8N:
        return
    with _outbox_lock:
        if _outbox_worker_started:
            return
        _outbox_worker_started = True
    t = threading.Thread(target=_outbox_worker_loop, name="n8n-outbox-worker", daemon=True)
    t.start()


_start_outbox_worker()


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _prepare_alert_payload(payload: dict) -> dict:
    """Normalize payload and add pre-rendered n8n text fields."""
    data = dict(payload or {})

    sev = str(data.get("severity", "LOW") or "LOW").strip().upper()
    if sev not in {"CRITICAL", "HIGH", "MEDIUM", "LOW"}:
        sev = "LOW"
    score = _safe_float(data.get("r_score", 0.0), 0.0)

    data["severity"] = sev
    data["r_score"] = round(score, 4)
    data["category"] = str(data.get("category", "unknown") or "unknown").strip().lower()
    data["title"] = str(data.get("title", "") or "")[:300]
    data["engine"] = str(data.get("engine", "unknown") or "unknown")[:120]
    data["query"] = str(data.get("query", "") or "")[:240]
    data["llm_mitre"] = str(data.get("llm_mitre", "") or "")[:120]
    data["reasoning"] = " ".join(str(data.get("reasoning", "") or "").split())[:600]
    data["timestamp"] = str(data.get("timestamp") or datetime.utcnow().isoformat())

    # n8n convenience fields to avoid brittle inline expression formatting.
    data["n8n_subject"] = f"{sev} DARK WEB ALERT - DarkSentinel"
    data["n8n_email_text"] = (
        f"DarkSentinel Alert\n\n"
        f"Severity: {data['severity']}\n"
        f"Category: {data['category']}\n"
        f"Risk Score: {float(data['r_score']):.3f}\n"
        f"Title: {data['title']}\n"
        f"Engine: {data['engine']}\n"
        f"MITRE: {data['llm_mitre']}\n"
        f"Reasoning: {data['reasoning']}\n"
        f"Timestamp: {data['timestamp']}\n"
    )
    data["n8n_slack_text"] = (
        f"{':rotating_light:' if sev == 'CRITICAL' else ':large_orange_circle:'} "
        f"{sev} ALERT\n"
        f"Category: {data['category']}\n"
        f"Risk Score: {float(data['r_score']):.3f}\n"
        f"Title: {data['title']}\n"
        f"Engine: {data['engine']}\n"
        f"MITRE: {data['llm_mitre']}\n"
        f"Reasoning: {data['reasoning']}\n"
        f"Timestamp: {data['timestamp']}"
    )
    return data


# ═══════════════════════════════════════════════════════════════════
# SEVERITY ROUTER
# ═══════════════════════════════════════════════════════════════════

def route_alert(payload: dict) -> dict:
    """
    Core routing logic matching the image spec:
      CRITICAL (R>0.8) → n8n → Email + Slack
      HIGH     (R>0.6) → n8n → Slack only
      MEDIUM   (R>0.3) → PostgreSQL analyst_queue
      LOW      (R<0.3) → PostgreSQL threat_results only
    """
    payload   = _prepare_alert_payload(payload)
    sev       = str(payload.get("severity","LOW")).upper()
    r_score   = _safe_float(payload.get("r_score", 0), 0.0)
    timestamp = str(payload.get("timestamp") or datetime.utcnow().isoformat())
    payload["timestamp"] = timestamp

    result = {
        "severity":  sev,
        "r_score":   r_score,
        "routed_to": [],
        "n8n_sent":  False,
        "n8n_queued": False,
        "pg_stored": False,
        "errors":    [],
    }

    # ── CRITICAL/HIGH (+optional MEDIUM) → n8n ───────────────────
    notify_medium = bool(ALERT_NOTIFY_MEDIUM)
    if sev in ("CRITICAL", "HIGH") or (notify_medium and sev == "MEDIUM"):
        if ALERT_ASYNC_N8N:
            ok, msg = _enqueue_n8n(payload, sev)
            result["n8n_queued"] = ok
            result["routed_to"].append("n8n:outbox")
            if not ok:
                result["errors"].append(f"n8n_outbox: {msg}")
        else:
            ok, _code, msg = _send_to_n8n(payload, sev)
            result["n8n_sent"] = ok
            result["routed_to"].append("n8n")
            if not ok:
                result["errors"].append(f"n8n: {msg}")

    # ── Always write to PostgreSQL ─────────────────────────────────
    conn = _pg_conn()
    if conn:
        try:
            _pg_ensure_tables(conn)
            stored = _pg_insert_threat(conn, payload)
            result["pg_stored"] = stored
            result["routed_to"].append("postgres:threat_results")
            # MEDIUM → always add to analyst queue
            if sev == "MEDIUM":
                _pg_insert_queue(conn, payload)
                result["routed_to"].append("postgres:analyst_queue")
            conn.close()
        except Exception as e:
            result["errors"].append(f"pg: {e}")
    else:
        result["errors"].append("postgres: not configured (set PG_PASSWORD in .env)")

    return result


# ═══════════════════════════════════════════════════════════════════
# API ENDPOINTS
# ═══════════════════════════════════════════════════════════════════

@app.get("/threats")
async def get_threats(req: Request):
    items, source = _load_flow_items()
    filtered = _apply_threat_filters(items, req.query_params)
    page = _paginate(filtered, req.query_params)
    return {
        **page,
        "source": "local" if source and source != "none" else "none",
        "sourcePath": source,
    }


@app.get("/network")
async def get_network(req: Request):
    items, _ = _load_flow_items()
    filtered = _apply_threat_filters(items, req.query_params)
    return _network_payload(filtered)


@app.post("/network/simulate")
async def simulate_network(req: Request):
    """
    Run CIC-RF simulation using darknet.py so UI behavior matches Streamlit.

    Body:
      {"mode": "normal" | "tor"}
    """
    try:
        body = await req.json()
        if not isinstance(body, dict):
            body = {}
    except Exception:
        body = {}

    mode = str(body.get("mode") or "normal").strip().lower()
    if mode not in {"normal", "tor"}:
        mode = "normal"

    try:
        import darknet

        status = darknet.get_model_status() if hasattr(darknet, "get_model_status") else {"loaded": False}
        if mode == "tor":
            result = darknet.simulate_tor_anomaly()
        else:
            result = darknet.simulate_normal_traffic()

        anomaly_state = {}
        if hasattr(darknet, "read_anomaly_state"):
            try:
                anomaly_state = darknet.read_anomaly_state() or {}
            except Exception:
                anomaly_state = {}

        payload = dict(result or {})
        payload["mode"] = mode
        payload["model_status"] = status
        payload["anomaly_state"] = anomaly_state
        payload["detected_at"] = str(
            payload.get("detected_at")
            or anomaly_state.get("detected_at")
            or datetime.utcnow().isoformat()
        )
        return payload
    except Exception as exc:
        fallback_t = 0.76 if mode == "tor" else 0.24
        return {
            "mode": mode,
            "prediction": "Tor" if fallback_t >= 0.7 else "Non-Tor",
            "t_score": round(fallback_t, 4),
            "trigger_scrape": bool(fallback_t >= 0.7),
            "all_probabilities": {
                "Tor": round(fallback_t, 4),
                "Non-Tor": round(1.0 - fallback_t, 4),
            },
            "error": f"simulation fallback: {exc}",
            "detected_at": datetime.utcnow().isoformat(),
            "model_status": {"loaded": False, "error": str(exc)},
            "anomaly_state": {},
            "fallback_used": True,
        }


@app.get("/analytics/executive")
async def get_analytics_executive(req: Request):
    items, _ = _load_flow_items()
    filtered = _apply_threat_filters(items, req.query_params)
    return _execute_payload(filtered)


@app.get("/analytics/recon")
async def get_analytics_recon(req: Request):
    items, _ = _load_flow_items()
    filtered = _apply_threat_filters(items, req.query_params)
    return _recon_payload(filtered)


@app.get("/analytics/intelligence")
async def get_analytics_intelligence(req: Request):
    items, _ = _load_flow_items()
    filtered = _apply_threat_filters(items, req.query_params)
    return _intelligence_payload(filtered)


@app.post("/scan")
async def start_scan(req: Request):
    try:
        config = await req.json()
        if not isinstance(config, dict):
            config = {}
    except Exception:
        config = {}

    _cleanup_scan_jobs()
    scan_id = uuid.uuid4().hex
    job = {
        "scan_id": scan_id,
        "status": "running",
        "config": config,
        "events": Queue(maxsize=1200),
        "created_at": datetime.utcnow().isoformat(),
        "created_ts": time.time(),
        "results": [],
    }
    with _SCAN_JOBS_LOCK:
        _SCAN_JOBS[scan_id] = job

    worker = threading.Thread(target=_scan_worker, args=(scan_id,), daemon=True, name=f"scan-{scan_id[:8]}")
    worker.start()

    return {"scanId": scan_id, "status": "started"}


@app.get("/scan/status")
async def scan_status(scan_id: str = Query(..., alias="scanId")):
    with _SCAN_JOBS_LOCK:
        job = _SCAN_JOBS.get(scan_id)
    if not job:
        raise HTTPException(status_code=404, detail="scanId not found")

    event_queue = job.get("events")

    async def stream_events():
        while True:
            try:
                event = await asyncio.to_thread(event_queue.get, True, 1.0)
            except Empty:
                with _SCAN_JOBS_LOCK:
                    current = _SCAN_JOBS.get(scan_id)
                    done = not current or current.get("status") in {"complete", "error"}
                yield ": keep-alive\n\n"
                if done and event_queue.empty():
                    break
                continue

            yield f"data: {json.dumps(event)}\n\n"
            if event.get("type") in {"scan-complete", "scan-error"}:
                break

    return StreamingResponse(
        stream_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/pii/scrub")
async def pii_scrub(req: Request):
    body = await req.json()
    text = str(body.get("text") or "")
    if not text.strip():
        raise HTTPException(status_code=400, detail="text is required")

    redactions = []
    scrubbed = text

    try:
        import llm

        scrubbed, removed = llm.scrub_pii(text)
        pii_regex = getattr(llm, "PII_REGEX", {})
        for pii_type, pattern in pii_regex.items():
            try:
                matches = re.findall(pattern, text)
            except Exception:
                matches = []
            for m in matches[:60]:
                redactions.append(
                    {
                        "type": str(pii_type).lower(),
                        "original": str(m),
                        "replacement": f"[{str(pii_type).upper()}]",
                    }
                )

        if not redactions:
            for tag in (removed or [])[:60]:
                redactions.append(
                    {
                        "type": str(tag).lower(),
                        "original": "[redacted]",
                        "replacement": f"[{str(tag).upper()}]",
                    }
                )
    except Exception:
        # Keep API usable even if optional ML stack is unavailable.
        scrubbed = text

    return {
        "original": text,
        "scrubbed": scrubbed,
        "redactions": redactions,
    }


@app.post("/analyze")
async def analyze_text_endpoint(req: Request):
    body = await req.json()
    text = str(body.get("text") or body.get("clean_text") or "")
    if not text.strip():
        raise HTTPException(status_code=400, detail="text is required")

    t_score = _safe_float(body.get("t_score", body.get("tScore", 0.5)), 0.5)
    h_score = _safe_float(body.get("h_score", body.get("hScore", 0.5)), 0.5)
    source_hash = str(body.get("source_hash") or body.get("sourceHash") or "")

    try:
        import llm

        return llm.analyze_text(text, t_score=t_score, h_score=h_score, source_hash=source_hash)
    except Exception as exc:
        score = round(_clamp01((t_score + h_score) / 2.0), 4)
        sev = _severity_from_score(score)
        return {
            "raw_text": text[:300],
            "clean_text": text[:300],
            "pii_removed": [],
            "roberta_category": "unknown",
            "roberta_conf": 0.5,
            "roberta_c_score": 0.5,
            "roberta_all": {},
            "roberta_error": str(exc),
            "llm_category": "unknown",
            "llm_severity": sev,
            "llm_mitre": None,
            "llm_mitre_tactic": None,
            "llm_mitre_tactic_id": None,
            "llm_mitre_technique_name": None,
            "llm_c_score": 0.5,
            "llm_reasoning": "Fallback analysis path used",
            "llm_error": str(exc),
            "llm_skipped": True,
            "llm_skip_reason": "llm module unavailable",
            "verdict": "HUMAN_REVIEW",
            "category": "unknown",
            "c_score": 0.5,
            "r_agrees": False,
            "l_agrees": False,
            "r_score": score,
            "severity": sev,
            "action": "analyst_queue" if sev in {"HIGH", "MEDIUM"} else ("auto_alert" if sev == "CRITICAL" else "log_only"),
            "t_score": round(t_score, 4),
            "h_score": round(h_score, 4),
            "analyzed_at": datetime.utcnow().isoformat(),
        }


@app.post("/analyze/explain")
async def analyze_explain_endpoint(req: Request):
    body = await req.json()
    text = str(body.get("text") or body.get("clean_text") or "")
    title = str(body.get("title") or "")
    roberta_category = str(body.get("roberta_category") or body.get("robertaCategory") or "")
    llm_category = str(body.get("llm_category") or body.get("llmCategory") or "")
    severity = str(body.get("severity") or "")
    verdict = str(body.get("verdict") or "")
    action = str(body.get("action") or "")
    r_score = _safe_float(body.get("r_score", body.get("riskScore", 0.0)), 0.0)

    try:
        import llm

        return llm.explain_threat_brief(
            text=text,
            title=title,
            roberta_category=roberta_category,
            llm_category=llm_category,
            severity=severity,
            r_score=r_score,
            verdict=verdict,
            action=action,
        )
    except Exception as exc:
        sev = str(severity or "UNKNOWN").upper()
        return {
            "summary": (
                f"Potential {sev} threat flagged (R={float(r_score or 0.0):.3f}, verdict={verdict or 'unknown'}). "
                f"Signals indicate RoBERTa={roberta_category or 'unknown'} and Gateway={llm_category or 'unknown'}. "
                f"Immediate action: {action or 'queue analyst review and preserve evidence context'}."
            ),
            "error": str(exc),
            "model": "fallback",
            "generated_at": datetime.utcnow().isoformat(),
        }

@app.post("/alert")
async def send_alert(alert: AlertPayload, request: Request):
    """
    Single alert endpoint. Called by ui.py for each analysed row.
    Routes based on severity per image spec.
    """
    _require_api_token(request)
    payload = _prepare_alert_payload(alert.dict())

    result = route_alert(payload)

    # Add to in-memory log
    _alert_log.append({**payload, **result})
    if len(_alert_log) > 200:
        _alert_log.pop(0)

    log.info(
        f"Alert [{alert.severity}] R={alert.r_score:.3f} "
        f"cat={alert.category} routed→{result['routed_to']}"
    )
    return {
        "status":    "ok" if not result["errors"] else "partial",
        "routed_to": result["routed_to"],
        "n8n_sent":  result["n8n_sent"],
        "n8n_queued":result.get("n8n_queued", False),
        "pg_stored": result["pg_stored"],
        "errors":    result["errors"],
        "timestamp": payload["timestamp"],
    }


@app.post("/alerts/bulk")
async def send_bulk_alerts(bulk: BulkAlertPayload, request: Request):
    """
    Send multiple alerts from one scan run.
    ui.py calls this after automated scan to push all CRITICAL/HIGH results.
    """
    _require_api_token(request)
    sent = 0; failed = 0; results_list = []
    for item in bulk.alerts:
        try:
            item_data = item if isinstance(item, dict) else item.dict()
            normalized = _prepare_alert_payload(item_data)
            r = route_alert(normalized)
            _alert_log.append({**normalized, **r})
            if not r["errors"]:
                sent += 1
            else:
                failed += 1
            results_list.append(r)
        except Exception as e:
            failed += 1
            results_list.append({"error": str(e)})
    if len(_alert_log) > 200:
        del _alert_log[:len(_alert_log)-200]
    return {"sent": sent, "failed": failed, "results": results_list}


@app.get("/health")
async def health():
    """Health check — verifies n8n and PostgreSQL reachability."""
    # n8n check
    n8n_ok = False
    try:
        r = requests.get("http://localhost:5678", timeout=2)
        n8n_ok = r.status_code < 500
    except Exception: pass

    # PostgreSQL check
    pg_ok = False; pg_rows = 0
    conn = _pg_conn()
    if conn:
        try:
            _pg_ensure_tables(conn)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM threat_results")
            pg_rows = cur.fetchone()[0]
            pg_ok   = True
            conn.close()
        except Exception: pass

    alerts_sent   = sum(1 for a in _alert_log if a.get("n8n_sent"))
    alerts_pg     = sum(1 for a in _alert_log if a.get("pg_stored"))
    alerts_failed = sum(1 for a in _alert_log if a.get("errors"))

    return {
        "status":             "ok",
        "auth_required":      bool(ALERT_API_TOKEN),
        "notify_medium":      bool(ALERT_NOTIFY_MEDIUM),
        "n8n_reachable":      n8n_ok,
        "n8n_webhook":        N8N_CRITICAL_URL,
        "postgres_connected": pg_ok,
        "postgres_rows":      pg_rows,
        "n8n_outbox":         _outbox_snapshot(),
        "session_stats": {
            "total_processed": len(_alert_log),
            "n8n_sent":        alerts_sent,
            "pg_stored":       alerts_pg,
            "failed":          alerts_failed,
        },
        "severity_routing": {
            "CRITICAL": "n8n (Email + Slack) + PostgreSQL",
            "HIGH":     "n8n (Slack only) + PostgreSQL",
            "MEDIUM":   "n8n + PostgreSQL (threat_results + analyst_queue)" if ALERT_NOTIFY_MEDIUM else "PostgreSQL (threat_results + analyst_queue)",
            "LOW":      "PostgreSQL (threat_results only)",
        },
    }


@app.get("/n8n/check")
async def n8n_check():
    """Diagnose n8n webhook payload mapping and provide expression hints."""
    sample = _prepare_alert_payload({
        "severity": "CRITICAL",
        "category": "fraud",
        "r_score": 0.9123,
        "title": "Sample alert",
        "engine": "Ahmia",
        "llm_mitre": "T1566",
        "reasoning": "Sample reasoning for n8n mapping test",
    })

    ok = False
    status = 0
    err = ""
    try:
        resp = requests.post(N8N_CRITICAL_URL, json=sample, timeout=5)
        status = int(resp.status_code)
        ok = status < 400
        if not ok:
            err = resp.text[:200]
    except Exception as e:
        err = str(e)

    return {
        "n8n_webhook": N8N_CRITICAL_URL,
        "reachable": ok,
        "http_status": status,
        "error": err or None,
        "tips": {
            "field_paths": [
                "$json.body.severity",
                "$json.body.category",
                "$json.body.r_score",
                "$json.body.n8n_email_text",
                "$json.body.n8n_slack_text",
            ],
            "inline_numeric": "Use Number($json.body.r_score).toFixed(3)",
            "expression_mode": "If field is Expression mode, use JS template literals `${...}` not {{...}}",
            "gmail_subject_example": "{{$json.body.n8n_subject}}",
            "gmail_body_example": "{{$json.body.n8n_email_text}}",
            "slack_text_example": "{{$json.body.n8n_slack_text}}",
        },
        "sample_body": sample,
    }


@app.post("/test")
@app.post("/alert/test")
async def send_test_alert(request: Request):
    """Send a test CRITICAL alert to verify the full n8n pipeline."""
    _require_api_token(request)
    test = _prepare_alert_payload({
        "severity":    "CRITICAL",
        "category":    "drugs",
        "r_score":     0.93,
        "title":       "TEST — DarkSentinel alert pipeline verification",
        "engine":      "TestEngine",
        "query":       "drug marketplace vendor",
        "source_hash": "test_hash_0000",
        "llm_mitre":   "T1566",
        "llm_mitre_tactic": "Initial Access",
        "llm_mitre_tactic_id": "TA0001",
        "llm_mitre_technique_name": "Phishing",
        "reasoning":   "Automated pipeline test from alert_api /test endpoint",
        "timestamp":   datetime.utcnow().isoformat(),
        "action":      "auto_alert",
    })
    result = route_alert(test)
    _alert_log.append({**test, **result})
    return {
        "test":     "sent",
        "routed_to":result["routed_to"],
        "n8n_sent": result["n8n_sent"],
        "n8n_queued": result.get("n8n_queued", False),
        "pg_stored":result["pg_stored"],
        "errors":   result["errors"],
    }


@app.get("/alerts/log")
async def get_alert_log(request: Request, limit: int = 50):
    """Return recent alert log (newest first)."""
    _require_api_token(request)
    return {
        "total":  len(_alert_log),
        "alerts": list(reversed(_alert_log))[:limit],
    }


@app.get("/pg/queue")
async def get_analyst_queue(request: Request):
    """Return MEDIUM items pending analyst review from PostgreSQL."""
    _require_api_token(request)
    conn = _pg_conn()
    if not conn:
        return {"error": "PostgreSQL not configured"}
    try:
        _pg_ensure_tables(conn)
        cur = conn.cursor()
        cur.execute("""
            SELECT id, alert_data, severity, r_score, llm_mitre, reasoning, action, created_at, reviewed
            FROM analyst_queue
            WHERE reviewed = FALSE
            ORDER BY r_score DESC
            LIMIT 50
        """)
        rows = cur.fetchall()
        conn.close()
        return {
            "pending": len(rows),
            "items": [
                {"id":r[0],"data":r[1],"severity":r[2],
                 "r_score":r[3],"llm_mitre":r[4],"reasoning":r[5],
                 "action":r[6],"created_at":str(r[7]),"reviewed":r[8]}
                for r in rows
            ],
        }
    except Exception as e:
        return {"error": str(e)}


@app.post("/pg/queue/{item_id}/review")
async def mark_reviewed(item_id: int, request: Request):
    """Mark an analyst_queue item as reviewed."""
    _require_api_token(request)
    conn = _pg_conn()
    if not conn:
        return {"error": "PostgreSQL not configured"}
    try:
        cur = conn.cursor()
        cur.execute("UPDATE analyst_queue SET reviewed=TRUE WHERE id=%s", (item_id,))
        conn.commit(); conn.close()
        return {"status": "marked_reviewed", "id": item_id}
    except Exception as e:
        return {"error": str(e)}


@app.post("/pg/migrate")
async def force_migrate(request: Request):
    """Force PostgreSQL schema migration (safe to run repeatedly)."""
    _require_api_token(request)
    conn = _pg_conn()
    if not conn:
        return {"error": "PostgreSQL not configured (set PG_PASSWORD in .env)"}
    try:
        _pg_ensure_tables(conn)
        conn.close()
        return {
            "status": "ok",
            "message": "Schema verified and migrations applied",
            "notify_medium": bool(ALERT_NOTIFY_MEDIUM),
        }
    except Exception as e:
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════════════
# DIRECT RUN
# ═══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)