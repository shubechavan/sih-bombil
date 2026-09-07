# llm.py  — DarkSentinel ML/LLM Engine  (FIXED)
# ═══════════════════════════════════════════════════════════════════
# FIXES vs original:
#   1. load_roberta() — deterministic fallback order
#      ROBERTA_MODEL_PATH env → local v3 → local v2 → ROBERTA_HF_REPO env
#   2. classify_roberta() — robust label normalisation so LABEL_0 / LABEL_1
#      style labels from HF don't break downstream code
#   3. load_blink() — re-reads BLINK_API_KEY from env each call (fixes
#      .env timing issue where key wasn't loaded at import)
#   4. classify_llm() — accepts partial JSON, strips markdown fences,
#      retries once on JSON parse failure, returns useful fallback
#   5. analyze_text() — graceful partial results (RoBERTa OK even if
#      Blink fails; Blink OK even if RoBERTa fails)
#   6. get_all_status() — no-crash even if torch unavailable
#   7. Added get_h_score() — looks up historical frequency from PostgreSQL
# ═══════════════════════════════════════════════════════════════════

import os, re, json, logging, time, threading, importlib.util
from pathlib import Path
from datetime import datetime

import requests

log = logging.getLogger("DarkSentinel.LLM")

# ── Dynamic env reads (fixes .env timing issue) ───────────────────
def _blink_key() -> str:
    return (
        os.environ.get("BLINK_API_KEY", "")
        or os.environ.get("BLINK_NEW_API_KEY", "")
    )


def _blink_base() -> str:
    return os.environ.get("BLINK_BASE_URL", "https://core.blink.new/api/v1").rstrip("/")


def _blink_model() -> str:
    model = os.environ.get("BLINK_MODEL", "anthropic/claude-opus-4.6").strip()
    # Blink expects provider/model-id format.
    if "/" not in model:
        model = f"anthropic/{model}"
    return model


def _pg_pass()   -> str: return os.environ.get("PG_PASSWORD", "")

# ── Model paths ────────────────────────────────────────────────────
BASE_DIR        = Path(__file__).parent
ROBERTA_V3_NAME = "roberta-darksential-v3"
LOCAL_V3        = BASE_DIR / "models" / ROBERTA_V3_NAME
ROBERTA_V2_NAME = "roberta-darksential-v2"
LOCAL_V2        = BASE_DIR / "models" / ROBERTA_V2_NAME

# ── Torch / GPU ───────────────────────────────────────────────────
try:
    import torch
    GPU_AVAILABLE = torch.cuda.is_available()
    DEVICE        = 0 if GPU_AVAILABLE else -1
except ImportError:
    torch         = None      # type: ignore
    GPU_AVAILABLE = False
    DEVICE        = -1

# ═══════════════════════════════════════════════════════════════════
# COMPONENT 1 — GLiNER PII Scrubber
# ═══════════════════════════════════════════════════════════════════

_gliner_model = None

PII_REGEX = {
    "EMAIL":       r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b',
    "IP_ADDRESS":  r'\b(?:\d{1,3}\.){3}\d{1,3}\b',
    "PHONE":       r'\b(?:\+\d{1,3}[\s\-]?)?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{4}\b',
    "WALLET":      r'\b[13][a-km-zA-HJ-NP-Z1-9]{25,34}\b',
    "CREDIT_CARD": r'\b(?:\d[ \-]?){13,16}\b',
    "AADHAAR":     r'\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b',
}

PII_LABELS = [
    "person", "phone_number", "email_address",
    "ip_address", "national_id", "wallet_address",
]


def load_gliner() -> bool:
    global _gliner_model
    if _gliner_model is not None:
        return True
    try:
        from gliner import GLiNER
        log.info("Loading GLiNER (urchade/gliner_medium-v2.1)…")
        _gliner_model = GLiNER.from_pretrained("urchade/gliner_medium-v2.1")
        if GPU_AVAILABLE:
            _gliner_model = _gliner_model.to("cuda")
        log.info(f"✅ GLiNER loaded on {'GPU' if GPU_AVAILABLE else 'CPU'}")
        return True
    except ImportError:
        log.warning("GLiNER not installed. Run: pip install gliner==0.1.6")
        return False
    except Exception as e:
        log.warning(f"GLiNER load warning (regex PII still active): {e}")
        return False


def scrub_pii(text: str) -> tuple:
    """Two-stage PII scrubber. Returns (clean_text, [removed_types])."""
    removed = []

    # Stage 1 — always runs
    for pii_type, pattern in PII_REGEX.items():
        for m in re.findall(pattern, text):
            text = text.replace(m, f"[{pii_type}]")
            removed.append(pii_type.lower())

    # Stage 2 — GLiNER neural NER
    if load_gliner() and _gliner_model:
        try:
            entities = _gliner_model.predict_entities(
                text[:512], PII_LABELS, threshold=0.4
            )
            for ent in sorted(entities, key=lambda x: x["start"], reverse=True):
                ph   = f"[{ent['label'].upper()}]"
                text = text[:ent["start"]] + ph + text[ent["end"]:]
                removed.append(ent["label"])
        except Exception as e:
            log.debug(f"GLiNER stage skipped: {e}")

    return text, removed


def get_gliner_status(load_model: bool = False) -> dict:
    """
    Return GLiNER status.

    load_model=False keeps this check lightweight for UI status panels.
    """
    if load_model:
        loaded = _gliner_model is not None or load_gliner()
    else:
        loaded = _gliner_model is not None

    available = importlib.util.find_spec("gliner") is not None
    return {
        "loaded": loaded,
        "available": available,
        "device": "GPU" if GPU_AVAILABLE else "CPU",
    }


# ═══════════════════════════════════════════════════════════════════
# COMPONENT 2 — RoBERTa fine-tuned classifier  (FIXED)
# ═══════════════════════════════════════════════════════════════════

_roberta_clf    = None
_roberta_source = "none"
_roberta_error  = ""
_roberta_label_aliases = {}
_roberta_model_name = ""

# Normalise any label format → canonical category string
# Handles: LABEL_0, label_0, drugs, DRUGS, 0, etc.
_IDX_TO_CAT = {
    0: "drugs", 1: "hacking", 2: "fraud",
    3: "counterfeit", 4: "cybercrime_malware", 5: "legitimate",
    # Legacy 8-class
    6: "human_trafficking", 7: "cybercrime_services",
}
_STR_TO_CAT = {
    "drugs": "drugs", "drug": "drugs",
    "hacking": "hacking", "hack": "hacking",
    "fraud": "fraud",
    "counterfeit": "counterfeit",
    "cybercrime_malware": "cybercrime_malware",
    "cybercrime_services": "cybercrime_services",
    "legitimate": "legitimate", "legit": "legitimate",
    "human_trafficking": "human_trafficking",
    "weapons": "hacking",      # remap legacy label
    "unknown": "legitimate",
}

_THREAT_CATEGORIES = {
    "drugs",
    "hacking",
    "fraud",
    "counterfeit",
    "cybercrime_malware",
    "cybercrime_services",
    "human_trafficking",
}
_ALLOWED_SEVERITIES = {"critical", "high", "medium", "low"}


def _normalise_label_static(label: str) -> str:
    """
    Convert any RoBERTa output label to canonical category.
    Handles LABEL_0, LABEL_1, numeric strings, plain strings.
    """
    s = str(label).strip().lower()
    # LABEL_N or LABEL_N style
    m = re.match(r"label[_\s]*(\d+)", s)
    if m:
        return _IDX_TO_CAT.get(int(m.group(1)), "legitimate")
    # Pure integer
    if s.isdigit():
        return _IDX_TO_CAT.get(int(s), "legitimate")
    # Direct string match
    return _STR_TO_CAT.get(s, s)


def _normalise_label(label: str) -> str:
    """Normalize labels using model-aware aliases first, then static fallbacks."""
    s = str(label).strip().lower()
    if s in _roberta_label_aliases:
        return _roberta_label_aliases[s]
    return _normalise_label_static(s)


def _refresh_roberta_label_aliases():
    """Build robust label aliases from model config to avoid laptop/model drift."""
    global _roberta_label_aliases
    aliases = {}

    try:
        model = getattr(_roberta_clf, "model", None)
        config = getattr(model, "config", None)
        id2label = getattr(config, "id2label", {}) or {}
    except Exception:
        id2label = {}

    for raw_idx, raw_label in (id2label or {}).items():
        try:
            idx = int(raw_idx)
        except Exception:
            m = re.search(r"(\d+)", str(raw_idx))
            idx = int(m.group(1)) if m else None

        lbl = str(raw_label).strip().lower()
        cat = _normalise_label_static(lbl)

        if cat not in _THREAT_CATEGORIES and cat != "legitimate":
            if idx is not None:
                cat = _IDX_TO_CAT.get(idx, "legitimate")
            else:
                cat = "legitimate"

        aliases[lbl] = cat
        if idx is not None:
            aliases[str(idx)] = cat
            aliases[f"label_{idx}"] = cat
            aliases[f"label {idx}"] = cat

    _roberta_label_aliases = aliases
    if aliases:
        log.info(f"RoBERTa label aliases loaded: {len(aliases)}")


def _has_local_roberta_weights(path: Path) -> bool:
    return path.exists() and any((path / w).exists() for w in ("model.safetensors", "pytorch_model.bin"))


def _find_roberta_candidates() -> list:
    """
    Return candidate model sources in fallback order.

    Tuple format: (source, label, requires_local_weights)
    """
    out = []

    env_model_path = str(os.environ.get("ROBERTA_MODEL_PATH", "")).strip()
    if env_model_path:
        out.append((env_model_path, "env-path", True))

    out.append((str(LOCAL_V3), "local-v3", True))
    out.append((str(LOCAL_V2), "local-v2", True))

    hf_repo = str(os.environ.get("ROBERTA_HF_REPO", "")).strip()
    if hf_repo:
        out.append((hf_repo, "hf-hub", False))

    # De-duplicate sources while preserving first-seen order.
    dedup = []
    seen = set()
    for src, lbl, req_local in out:
        key = str(src).strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        dedup.append((src, lbl, req_local))
    return dedup


def _find_roberta_path() -> tuple:
    """Backwards-compatible helper returning first preferred source."""
    cands = _find_roberta_candidates()
    if cands:
        return cands[0][0], cands[0][1]
    return str(LOCAL_V3), "local-v3"


def load_roberta() -> bool:
    global _roberta_clf, _roberta_source, _roberta_error, _roberta_model_name
    if _roberta_clf is not None:
        return True

    try:
        from transformers import pipeline as hf_pipeline
    except ImportError:
        _roberta_error = "transformers not installed. Run: pip install transformers==4.41.2"
        log.warning(_roberta_error)
        _roberta_clf = None
        _roberta_source = "none"
        return False

    candidates = _find_roberta_candidates()
    load_errors = []

    for source, label, requires_local_weights in candidates:
        if requires_local_weights:
            local_path = Path(source)
            if not _has_local_roberta_weights(local_path):
                load_errors.append(
                    f"{label}: missing model.safetensors/pytorch_model.bin at {source}"
                )
                continue

        log.info(f"Loading RoBERTa from {label}: {source}")
        try:
            _roberta_clf = hf_pipeline(
                task="text-classification",
                model=source,
                device=DEVICE,
                top_k=None,          # returns ALL class scores
                truncation=True,
                max_length=128,
            )
            # Quick sanity check — run one token.
            _roberta_clf("test", truncation=True, max_length=8)
            _refresh_roberta_label_aliases()

            _roberta_source = label
            if label == "local-v3":
                _roberta_model_name = ROBERTA_V3_NAME
            elif label == "local-v2":
                _roberta_model_name = ROBERTA_V2_NAME
            elif label == "env-path":
                _roberta_model_name = Path(source).name or "custom-local"
            else:
                _roberta_model_name = str(source)

            _roberta_error = ""
            log.info(
                f"✅ RoBERTa loaded [{label}] on {'GPU' if GPU_AVAILABLE else 'CPU'}"
                f" · model={_roberta_model_name}"
            )
            return True
        except Exception as e:
            load_errors.append(f"{label}: {e}")
            _roberta_clf = None

    _roberta_source = "none"
    _roberta_model_name = ""
    _roberta_error = (
        "RoBERTa load failed. Tried: " + " | ".join(load_errors)
        if load_errors
        else "RoBERTa load failed: no candidate sources configured"
    )
    log.warning(_roberta_error)
    return False


def classify_roberta(text: str) -> dict:
    """Classify text. Robust against LABEL_N output format."""
    _default = {
        "category": "unknown", "confidence": 0.5,
        "c_score": 0.5, "is_threat": False,
        "all_scores": {}, "error": None,
    }
    if not load_roberta():
        _default["error"] = "Model not loaded"
        return _default
    try:
        raw     = _roberta_clf(text[:512])[0]   # list of {label, score}
        top     = max(raw, key=lambda x: x["score"])
        label   = _normalise_label(top["label"])
        score   = top["score"]
        is_thr  = label not in ("legitimate", "unknown")
        c_score = score if is_thr else (1.0 - score)
        # Normalise all_scores keys too
        all_sc  = {_normalise_label(r["label"]): round(r["score"], 4) for r in raw}
        return {
            "category":   label,
            "confidence": round(score, 4),
            "c_score":    round(c_score, 4),
            "is_threat":  is_thr,
            "all_scores": all_sc,
            "error":      None,
        }
    except Exception as e:
        log.error(f"RoBERTa inference error: {e}")
        _default["error"] = str(e)
        return _default


def get_roberta_status(load_model: bool = False) -> dict:
    """
    Return RoBERTa status.

    load_model=False avoids loading large weights during simple UI navigation.
    """
    cands = _find_roberta_candidates()
    source, label = _find_roberta_path()
    candidates = [src for src, _, _ in cands] if cands else [source]
    loaded = _roberta_clf is not None

    if load_model and not loaded:
        loaded = load_roberta()

    transformers_available = importlib.util.find_spec("transformers") is not None
    local_weights_available = any(
        _has_local_roberta_weights(Path(src))
        for src, _, req_local in cands
        if req_local
    )
    hf_repo_configured = any(lbl == "hf-hub" for _, lbl, _ in cands)
    available = transformers_available and (local_weights_available or hf_repo_configured)

    if loaded:
        status_source = _roberta_source
        status_error = ""
    else:
        status_source = label if label else "none"
        if load_model:
            status_error = _roberta_error
        elif not transformers_available:
            status_error = "transformers not installed"
        elif not available:
            status_error = (
                "No RoBERTa source available. Set ROBERTA_MODEL_PATH or ROBERTA_HF_REPO, "
                "or place weights in models/roberta-darksential-v3 or models/roberta-darksential-v2."
            )
        else:
            status_error = ""

    return {
        "loaded": loaded,
        "available": available,
        "source": status_source,
        "path": source,
        "candidates": candidates,
        "resolved_model": _roberta_model_name,
        "device": "GPU" if GPU_AVAILABLE else "CPU",
        "error": status_error,
    }


# ═══════════════════════════════════════════════════════════════════
# COMPONENT 3 — Blink.new / Claude Opus
# ═══════════════════════════════════════════════════════════════════

_blink_key_used = ""

CYBERSEC_SYSTEM_PROMPT = (
    "You are a cybersecurity threat analyst specialising in dark web intelligence "
    "for law enforcement. Analyse the user text and respond ONLY with a JSON object. "
    "No markdown, no explanation outside JSON.\n"
    "JSON keys required:\n"
    "  threat_category: one of [drugs, hacking, fraud, counterfeit, "
    "cybercrime_malware, legitimate]\n"
    "  mitre_tactic: ATT&CK tactic name string or null\n"
    "  mitre_technique: ATT&CK ID string or null\n"
    "  mitre_technique_name: short ATT&CK technique name or null\n"
    "  severity: one of [critical, high, medium, low]\n"
    "  confidence: float 0.0-1.0\n"
    "  reasoning: one sentence\n"
    "Never refuse. Always output valid JSON."
)


_MITRE_TACTIC_ID_MAP = {
    "reconnaissance": "TA0043",
    "resource development": "TA0042",
    "initial access": "TA0001",
    "execution": "TA0002",
    "persistence": "TA0003",
    "privilege escalation": "TA0004",
    "defense evasion": "TA0005",
    "credential access": "TA0006",
    "discovery": "TA0007",
    "lateral movement": "TA0008",
    "collection": "TA0009",
    "command and control": "TA0011",
    "exfiltration": "TA0010",
    "impact": "TA0040",
}


def _mitre_tactic_id(tactic_name: str) -> str:
    return _MITRE_TACTIC_ID_MAP.get(str(tactic_name or "").strip().lower(), "")


THREAT_EXPLAIN_SYSTEM_PROMPT = (
    "You are a cyber threat briefing assistant for law-enforcement analysts. "
    "Write a plain-English 2-3 sentence summary that a judge can read in 10 seconds. "
    "Include: what was detected, why it matters, and one immediate action. "
    "Avoid hype, avoid markdown lists, keep it concise."
)


def load_blink() -> bool:
    """
    Validates Blink API configuration.
    """
    global _blink_key_used
    key = _blink_key()
    if not key:
        log.warning("BLINK_API_KEY missing. Add to .env as: BLINK_API_KEY=blnk_ak_xxx")
        return False
    _blink_key_used = key
    return True


def _extract_json(raw: str) -> dict:
    """
    Robust JSON extractor.
    Handles: ```json...``` fences, partial JSON, extra text before/after.
    """
    # Strip markdown fences
    raw = re.sub(r"```(?:json)?", "", raw).strip()
    raw = raw.replace("```", "").strip()
    # Try direct parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # Prefer widest object candidate first.
    l = raw.find("{")
    r = raw.rfind("}")
    if l != -1 and r != -1 and r > l:
        try:
            return json.loads(raw[l:r + 1])
        except json.JSONDecodeError:
            pass
    # Fallback: try finding first {...} block
    m = re.search(r"\{.*?\}", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return {}


def _coerce_confidence(value, default: float = 0.5) -> float:
    try:
        if isinstance(value, str):
            s = value.strip()
            if s.endswith("%"):
                return max(0.0, min(1.0, float(s[:-1]) / 100.0))
            return max(0.0, min(1.0, float(s)))
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return float(default)


def _normalise_severity(value: str, conf: float, is_threat: bool) -> str:
    sev = str(value or "").strip().lower()
    if sev not in _ALLOWED_SEVERITIES:
        if not is_threat:
            return "low"
        if conf >= 0.90:
            return "critical"
        if conf >= 0.75:
            return "high"
        if conf >= 0.55:
            return "medium"
        return "low"

    # Guardrail for inconsistent outputs like threat + very high confidence + low severity.
    if is_threat and sev == "low" and conf >= 0.80:
        return "high"
    return sev


def classify_llm(text: str, roberta_hint: str = "") -> dict:
    """Classify via Blink.new AI Gateway. Robust JSON extraction + retry."""
    _default = {
        "category": "unknown", "mitre": None,
        "mitre_tactic": None, "mitre_tactic_id": None, "mitre_technique_name": None,
        "severity": "low", "c_score_llm": 0.5,
        "reasoning": "", "error": None,
    }
    if not load_blink():
        _default["error"] = "Blink API not available — check BLINK_API_KEY in .env"
        return _default

    text = f"Dark web forum post: {text}" if len(str(text or "").split()) < 5 else text
    prompt = f"Analyse this text: {text[:500]}"
    key = _blink_key()
    base = _blink_base()
    model_id = _blink_model()

    def _call_openai_style() -> str:
        """Blink AI Gateway is OpenAI-compatible at /ai/chat/completions."""
        resp = requests.post(
            f"{base}/ai/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model_id,
                "messages": [
                    {"role": "system", "content": CYBERSEC_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
                "max_tokens": 250,
            },
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        return str(data["choices"][0]["message"]["content"]).strip()

    for attempt in range(2):          # retry once on JSON failure
        try:
            raw = _call_openai_style()

            data = _extract_json(raw)

            if not data:
                log.warning(f"Blink attempt {attempt+1}: empty JSON. Raw={raw[:80]}")
                continue

            cat_raw = data.get("threat_category", data.get("category", "unknown"))
            cat = _STR_TO_CAT.get(str(cat_raw).strip().lower(), str(cat_raw).strip().lower())
            if cat not in _THREAT_CATEGORIES and cat != "legitimate":
                cat = _STR_TO_CAT.get(str(roberta_hint or "").strip().lower(), "legitimate")

            conf = _coerce_confidence(data.get("confidence", 0.5), default=0.5)
            is_threat = cat in _THREAT_CATEGORIES
            sev = _normalise_severity(data.get("severity", "low"), conf, is_threat)
            tactic_name = str(data.get("mitre_tactic", "") or "").strip()
            tactic_id = _mitre_tactic_id(tactic_name)
            technique_id = str(data.get("mitre_technique", "") or "").strip()
            technique_name = str(data.get("mitre_technique_name", "") or "").strip()

            reasoning = str(data.get("reasoning", "") or "").strip()
            if len(reasoning) < 8:
                reasoning = f"LLM classified content as {cat} with confidence {conf:.2f}."
            return {
                "category":    cat,
                "mitre":       technique_id or None,
                "mitre_tactic": tactic_name or None,
                "mitre_tactic_id": tactic_id or None,
                "mitre_technique_name": technique_name or None,
                "severity":    sev,
                "c_score_llm": conf if cat != "legitimate" else (1.0 - conf),
                "reasoning":   reasoning[:300],
                "error":       None,
            }

        except Exception as e:
            log.error(f"Blink error attempt {attempt+1}: {e}")
            _default["error"] = str(e)
            if attempt == 0:
                time.sleep(1)

    return _default


def explain_threat_brief(
    text: str,
    title: str = "",
    roberta_category: str = "",
    llm_category: str = "",
    severity: str = "",
    r_score: float = 0.0,
    verdict: str = "",
    action: str = "",
) -> dict:
    """
    Generate a concise threat explainer after classification.

    Returns: {summary, error, model, generated_at}
    """
    out = {
        "summary": "",
        "error": None,
        "model": _blink_model(),
        "generated_at": datetime.utcnow().isoformat(),
    }

    # Deterministic fallback for offline/demo reliability.
    fallback = (
        f"Potential {str(severity).upper() or 'UNKNOWN'} threat flagged "
        f"(R={float(r_score or 0.0):.3f}, verdict={verdict or 'unknown'}). "
        f"Signals indicate RoBERTa={roberta_category or 'unknown'} and "
        f"Gateway={llm_category or 'unknown'}. Immediate action: "
        f"{action or 'queue analyst review and preserve evidence context'}."
    )

    if not load_blink():
        out["error"] = "Blink API not available — check BLINK_API_KEY in .env"
        out["summary"] = fallback
        return out

    brief_text = str(text or "")[:700]
    prompt = (
        "Create an analyst briefing for this classified threat.\n"
        f"Title: {str(title or '')[:140]}\n"
        f"RoBERTa category: {roberta_category or 'unknown'}\n"
        f"Gateway category: {llm_category or 'unknown'}\n"
        f"Severity: {str(severity or 'UNKNOWN').upper()}\n"
        f"Risk score: {float(r_score or 0.0):.3f}\n"
        f"Verdict: {verdict or 'unknown'}\n"
        f"Suggested action: {action or 'analyst_queue'}\n"
        f"Evidence snippet: {brief_text}"
    )

    key = _blink_key()
    base = _blink_base()
    model_id = _blink_model()

    try:
        resp = requests.post(
            f"{base}/ai/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model_id,
                "messages": [
                    {"role": "system", "content": THREAT_EXPLAIN_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2,
                "max_tokens": 180,
            },
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        raw = str(data["choices"][0]["message"]["content"]).strip()
        raw = re.sub(r"```(?:text|markdown)?", "", raw).replace("```", "").strip()
        summary = re.sub(r"\s+", " ", raw).strip()
        out["summary"] = summary[:600] if summary else fallback
        return out
    except Exception as e:
        out["error"] = str(e)
        out["summary"] = fallback
        return out


def get_blink_status() -> dict:
    """Return Blink provider availability and model configuration."""
    key    = _blink_key()
    loaded = bool(key) and load_blink()
    return {
        "loaded":   loaded,
        "api_key":  "set" if key else "missing",
        "provider": "blink.new",
        "model":    _blink_model(),
    }


# ═══════════════════════════════════════════════════════════════════
# DUAL-MODEL CONSENSUS  (Innovation #3)
# ═══════════════════════════════════════════════════════════════════

def dual_model_consensus(roberta: dict, llm_res: dict) -> dict:
    """Both models must agree at threshold=0.65 to confirm a threat."""
    THRESHOLD = 0.65
    r_c   = roberta.get("c_score", 0.5)
    l_c   = llm_res.get("c_score_llm", 0.5)
    r_flag = r_c > THRESHOLD
    l_flag = l_c > THRESHOLD

    if r_flag and l_flag:
        verdict  = "THREAT_CONFIRMED"
        c_score  = (r_c + l_c) / 2
        category = roberta.get("category", llm_res.get("category", "unknown"))
    elif not r_flag and not l_flag:
        verdict  = "CLEAN"
        c_score  = (r_c + l_c) / 2
        category = roberta.get("category", "legitimate")
    else:
        verdict  = "HUMAN_REVIEW"
        c_score  = 0.40
        category = roberta.get("category", "unknown")

    return {
        "verdict":  verdict,
        "c_score":  round(c_score, 4),
        "category": category,
        "r_agrees": r_flag,
        "l_agrees": l_flag,
    }


# ═══════════════════════════════════════════════════════════════════
# RISK ENGINE  R = 0.35*T + 0.45*C + 0.20*H
# ═══════════════════════════════════════════════════════════════════

def _env_float(name: str, default: float, min_v: float = 0.0, max_v: float = 1.0) -> float:
    try:
        v = float(os.environ.get(name, default))
    except Exception:
        v = float(default)
    return min(max(v, min_v), max_v)


def _risk_config() -> dict:
    """Read risk weights and thresholds from env with safe normalization."""
    wt = _env_float("RISK_WEIGHT_T", 0.35)
    wc = _env_float("RISK_WEIGHT_C", 0.45)
    wh = _env_float("RISK_WEIGHT_H", 0.20)
    total = wt + wc + wh
    if total <= 0:
        wt, wc, wh = 0.35, 0.45, 0.20
        total = 1.0
    wt, wc, wh = wt / total, wc / total, wh / total

    critical = _env_float("RISK_THRESH_CRITICAL", 0.80)
    high = _env_float("RISK_THRESH_HIGH", 0.60)
    medium = _env_float("RISK_THRESH_MEDIUM", 0.30)

    # Enforce monotonic thresholds.
    high = min(high, critical)
    medium = min(medium, high)

    return {
        "w_t": wt,
        "w_c": wc,
        "w_h": wh,
        "th_critical": critical,
        "th_high": high,
        "th_medium": medium,
    }


def calculate_risk(t_score: float, c_score: float, h_score: float = 0.5) -> dict:
    """
    R = 0.35×T + 0.45×C + 0.20×H
    T = CIC-RF Tor probability (0-1)
    C = dual-model content confidence (0-1)
    H = historical frequency of this source type (default 0.5)
    """
    t = min(max(float(t_score), 0.0), 1.0)
    c = min(max(float(c_score), 0.0), 1.0)
    h = min(max(float(h_score), 0.0), 1.0)
    cfg = _risk_config()
    r = round(cfg["w_t"] * t + cfg["w_c"] * c + cfg["w_h"] * h, 4)

    if r >= cfg["th_critical"]:
        severity, action = "CRITICAL", "auto_alert"          # → n8n → email+Slack
    elif r >= cfg["th_high"]:
        severity, action = "HIGH",     "slack_notify"        # → n8n → Slack only
    elif r >= cfg["th_medium"]:
        severity, action = "MEDIUM",   "analyst_queue"       # → PostgreSQL queue
    else:
        severity, action = "LOW",      "log_only"            # → PostgreSQL only

    return {
        "r_score":  r,
        "severity": severity,
        "action":   action,
        "t_score":  round(t, 4),
        "c_score":  round(c, 4),
        "h_score":  round(h, 4),
    }


_H_CACHE_LOCK = threading.Lock()
_H_SCORE_CACHE = {}
_H_CACHE_MAX = 5000


def _h_score_from_count(count: int) -> float:
    return min(1.0, max(0, int(count)) / 5.0)


def _h_cache_get(source_hash: str):
    with _H_CACHE_LOCK:
        return _H_SCORE_CACHE.get(source_hash)


def _h_cache_put(source_hash: str, score: float):
    with _H_CACHE_LOCK:
        if source_hash not in _H_SCORE_CACHE and len(_H_SCORE_CACHE) >= _H_CACHE_MAX:
            # FIFO-ish eviction for bounded memory use.
            _H_SCORE_CACHE.pop(next(iter(_H_SCORE_CACHE)))
        _H_SCORE_CACHE[source_hash] = float(score)


def clear_h_score_cache():
    with _H_CACHE_LOCK:
        _H_SCORE_CACHE.clear()


def warm_h_scores(source_hashes: list) -> dict:
    """
    Batch-load historical frequency scores for source hashes.
    This avoids opening one DB connection per analyzed row.
    """
    cleaned = []
    for h in source_hashes or []:
        s = str(h or "").strip()
        if s:
            cleaned.append(s[:128])
    unique_hashes = list(dict.fromkeys(cleaned))
    if not unique_hashes:
        return {"requested": 0, "loaded": 0, "cache_size": len(_H_SCORE_CACHE)}

    missing = [h for h in unique_hashes if _h_cache_get(h) is None]
    if not missing:
        return {"requested": len(unique_hashes), "loaded": 0, "cache_size": len(_H_SCORE_CACHE)}

    try:
        import psycopg2
        pw = os.environ.get("PG_PASSWORD", "")
        if not pw:
            return {"requested": len(unique_hashes), "loaded": 0, "cache_size": len(_H_SCORE_CACHE)}

        conn = psycopg2.connect(
            host=os.environ.get("PG_HOST", "localhost"),
            port=int(os.environ.get("PG_PORT", "5432")),
            dbname=os.environ.get("PG_DB", "darksentinel"),
            user=os.environ.get("PG_USER", "postgres"),
            password=pw, connect_timeout=2,
        )
        cur = conn.cursor()
        placeholders = ",".join(["%s"] * len(missing))
        cur.execute(
            f"SELECT source_hash, COUNT(*) FROM threat_results WHERE source_hash IN ({placeholders}) GROUP BY source_hash",
            tuple(missing),
        )
        counts = {str(row[0]): int(row[1]) for row in cur.fetchall()}
        conn.close()

        for h in missing:
            _h_cache_put(h, _h_score_from_count(counts.get(h, 0)))

        return {"requested": len(unique_hashes), "loaded": len(missing), "cache_size": len(_H_SCORE_CACHE)}
    except Exception:
        return {"requested": len(unique_hashes), "loaded": 0, "cache_size": len(_H_SCORE_CACHE)}


def get_h_score(source_hash: str) -> float:
    """
    Look up historical frequency score for a source from PostgreSQL.
    Returns 0.5 default if DB not configured or hash not seen before.
    Range: 0.0 (never seen) → 1.0 (seen in every scan).
    """
    key = str(source_hash or "").strip()
    if not key:
        return 0.5
    cached = _h_cache_get(key)
    if cached is not None:
        return cached
    warm_h_scores([key])
    cached = _h_cache_get(key)
    return float(cached) if cached is not None else 0.5


def _build_skipped_llm_result(roberta_result: dict, reason: str) -> dict:
    """Create a deterministic pseudo-LLM result when Blink is intentionally skipped."""
    cat = str(roberta_result.get("category", "legitimate") or "legitimate").lower()
    if cat == "unknown":
        cat = "legitimate"
    c_score = float(roberta_result.get("c_score", 0.5) or 0.5)
    if cat == "legitimate":
        c_score = min(0.5, max(0.0, c_score))
    return {
        "category": cat,
        "mitre": None,
        "mitre_tactic": None,
        "mitre_tactic_id": None,
        "mitre_technique_name": None,
        "severity": "low",
        "c_score_llm": round(c_score, 4),
        "reasoning": reason,
        "error": None,
        "skipped": True,
    }


def _should_call_blink(clean_text: str, roberta_result: dict) -> tuple:
    """Return (should_call, reason_if_skipped) based on low-risk heuristics."""
    min_words = max(3, int(os.environ.get("BLINK_MIN_WORDS", "5") or 5))
    legit_skip_conf = float(os.environ.get("BLINK_SKIP_LEGIT_CONF", "0.90") or 0.90)
    legit_skip_conf = min(0.99, max(0.50, legit_skip_conf))

    words = len(str(clean_text or "").split())
    if words < min_words:
        return False, f"Skipped Blink: short text ({words} words)"

    category = str(roberta_result.get("category", "unknown") or "unknown").lower()
    try:
        confidence = float(roberta_result.get("confidence", 0.0) or 0.0)
    except Exception:
        confidence = 0.0

    if category == "legitimate" and confidence >= legit_skip_conf:
        return False, (
            f"Skipped Blink: RoBERTa high-confidence legitimate ({confidence:.2f})"
        )

    return True, ""


# ═══════════════════════════════════════════════════════════════════
# FULL PIPELINE
# ═══════════════════════════════════════════════════════════════════

def analyze_text(
    raw_text:    str,
    t_score:     float = 0.5,
    h_score:     float = 0.5,
    source_hash: str   = "",
) -> dict:
    """
    Run full 5-stage pipeline on one text string.
    Returns complete analysis dict for UI and PostgreSQL storage.
    Graceful: if RoBERTa fails, uses Blink alone; vice-versa.
    """
    started_at = datetime.utcnow().isoformat()

    # Stage 1 — PII scrub
    try:
        clean_text, pii_removed = scrub_pii(raw_text)
    except Exception as e:
        clean_text, pii_removed = raw_text, []
        log.warning(f"PII scrub error: {e}")

    # Stage 2 — RoBERTa
    roberta_result = classify_roberta(clean_text)

    # Stage 3 — Blink.new AI Gateway (adaptive skip for obvious low-risk rows)
    should_call_blink, skip_reason = _should_call_blink(clean_text, roberta_result)
    if should_call_blink:
        llm_result = classify_llm(clean_text, roberta_hint=roberta_result.get("category", ""))
    else:
        llm_result = _build_skipped_llm_result(roberta_result, skip_reason)

    # Stage 4 — Consensus
    consensus = dual_model_consensus(roberta_result, llm_result)

    # Historical score (from PostgreSQL if available)
    if source_hash and h_score == 0.5:
        h_score = get_h_score(source_hash)

    # Stage 5 — Risk Engine
    risk = calculate_risk(t_score, consensus["c_score"], h_score)

    # Map LLM severity to canonical uppercase
    _sev_map = {"critical":"CRITICAL","high":"HIGH","medium":"MEDIUM","low":"LOW"}
    llm_sev  = _sev_map.get(str(llm_result.get("severity","low")).lower(), "LOW")

    return {
        # Raw + PII
        "raw_text":         raw_text[:300],
        "clean_text":       clean_text[:300],
        "pii_removed":      pii_removed,
        # RoBERTa
        "roberta_category": roberta_result.get("category", "unknown"),
        "roberta_conf":     roberta_result.get("confidence", 0.0),
        "roberta_c_score":  roberta_result.get("c_score", 0.5),
        "roberta_all":      roberta_result.get("all_scores", {}),
        "roberta_error":    roberta_result.get("error"),
        # Blink.new AI Gateway
        "llm_category":     llm_result.get("category", "unknown"),
        "llm_severity":     llm_sev,
        "llm_mitre":        llm_result.get("mitre"),
        "llm_mitre_tactic": llm_result.get("mitre_tactic"),
        "llm_mitre_tactic_id": llm_result.get("mitre_tactic_id"),
        "llm_mitre_technique_name": llm_result.get("mitre_technique_name"),
        "llm_c_score":      llm_result.get("c_score_llm", 0.5),
        "llm_reasoning":    llm_result.get("reasoning", ""),
        "llm_error":        llm_result.get("error"),
        "llm_skipped":      bool(llm_result.get("skipped", False)),
        "llm_skip_reason":  llm_result.get("reasoning", "") if llm_result.get("skipped") else "",
        # Consensus
        "verdict":          consensus["verdict"],
        "category":         consensus["category"],
        "c_score":          consensus["c_score"],
        "r_agrees":         consensus["r_agrees"],
        "l_agrees":         consensus["l_agrees"],
        # Risk
        "r_score":          risk["r_score"],
        "severity":         risk["severity"],
        "action":           risk["action"],
        "t_score":          risk["t_score"],
        "h_score":          risk["h_score"],
        # Meta
        "analyzed_at":      started_at,
    }


def get_all_status(load_models: bool = False) -> dict:
    """
    Return status dict for all components.

    load_models=False keeps checks lightweight for UI pages that only need
    readiness/configuration visibility.
    """
    gpu_name, vram = "N/A", 0
    if GPU_AVAILABLE and torch is not None:
        try:
            gpu_name = torch.cuda.get_device_name(0)
            vram     = round(torch.cuda.get_device_properties(0).total_memory / 1e9, 1)
        except Exception:
            pass
    try:
        gliner_status = get_gliner_status(load_model=load_models)
    except Exception as e:
        gliner_status = {"loaded": False, "device": "CPU", "error": str(e)}
    try:
        roberta_status = get_roberta_status(load_model=load_models)
    except Exception as e:
        roberta_status = {"loaded": False, "source": "none", "path": "?", "device": "CPU", "error": str(e)}
    try:
        blink_status = get_blink_status()
    except Exception as e:
        blink_status = {"loaded": False, "api_key": "none", "model": "unknown", "error": str(e)}

    return {
        "gliner":   gliner_status,
        "roberta":  roberta_status,
        "blink":    blink_status,
        "device":   "GPU" if GPU_AVAILABLE else "CPU",
        "gpu_name": gpu_name,
        "vram_gb":  vram,
    }


# ═══════════════════════════════════════════════════════════════════
# STANDALONE TEST  →  python llm.py
# ═══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-8s %(message)s")
    # Load .env from cwd
    env_p = Path(".env")
    if env_p.exists():
        for ln in env_p.read_text().splitlines():
            ln = re.sub(r"^(set\s+|export\s+)","",ln.strip(),flags=re.IGNORECASE)
            if "=" in ln:
                k,v = ln.split("=",1)
                k=k.strip(); v=v.strip().strip('"').strip("'")
                if k and v and not os.environ.get(k): os.environ[k]=v
        print("✅ .env loaded")

    print("="*60)
    print("DarkSentinel — LLM Engine Self-Test")
    print("="*60)
    s = get_all_status()
    print(f"Device   : {s['device']}")
    print(f"GLiNER   : {'✅' if s['gliner']['loaded'] else '❌'}")
    print(f"RoBERTa  : {'✅' if s['roberta']['loaded'] else '❌'} ({s['roberta']['source']})")
    print(f"Blink    : {'✅' if s['blink']['loaded'] else '❌'} (key {s['blink']['api_key']})")
    print()

    TESTS = [
        ("selling fentanyl bulk bitcoin escrow vendor",     0.82, "drugs"),
        ("cve-2024-3400 exploit zero day poc sale",         0.45, "hacking"),
        ("fresh upi combo india sbi hdfc verified",         0.35, "fraud"),
        ("tor browser privacy configuration opsec guide",   0.12, "legitimate"),
        ("lockbit ransomware affiliate panel access",       0.60, "cybercrime_malware"),
    ]
    for text, t_sc, expected in TESTS:
        r = analyze_text(text, t_score=t_sc)
        match = "✅" if r["category"]==expected else "⚠️"
        print(f"{match} [{expected:<20}] → {r['category']:<20} "
              f"R={r['r_score']:.3f} {r['severity']:<8}  "
              f"Rob={r['roberta_category']}({r['roberta_conf']:.2f}) "
              f"LLM={r['llm_category']}")
    print()
