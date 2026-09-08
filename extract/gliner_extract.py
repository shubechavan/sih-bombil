"""
gliner_extract.py — GLiNER in EXTRACT mode, with redaction kept alongside.

v1 used GLiNER to *remove* entities: `scrub_pii()` in legacy/llm.py replaces 55+
kinds of PII with placeholders. This problem statement inverts the job. The same
model, the same labels, the same loading path — but the entities come back
instead of being overwritten, because a handle, a nickname or a location is
exactly the soft identifier an actor profile is built from.

`redact()` is still here and still matters: CLAUDE.md forbids raw scraped PII
leaving the box, so anything bound for a hosted LLM goes through it first.

Two engineering constraints shape this file:

  * **legacy/llm.py costs ~16 seconds to import** (it pulls torch). So it is
    imported lazily, inside load_model(), and extract/identifiers.py never
    imports this module at all. The regex extractor stays fast and torch-free.

  * **The model may not be there.** gliner is not installed in every environment
    and the weights need a download the first time. Every function here degrades
    to an empty result and says why through status(); nothing raises, and the
    regex path in extract/identifiers.py never depends on any of it.

    Concretely: `status()["installed"]` is False on a box without the package,
    `extract_entities()` returns [], and identifier extraction still reaches full
    recall on the fixtures corpus. GLiNER is an enrichment here, not a dependency.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Optional

_LEGACY = Path(__file__).resolve().parent.parent / "legacy"
if str(_LEGACY) not in sys.path:
    sys.path.insert(0, str(_LEGACY))

__all__ = [
    "ACTOR_LABELS",
    "extract_entities",
    "load_model",
    "redact",
    "status",
]

#: What we want *found*, as opposed to v1's PII_LABELS, which is what it wanted
#: hidden. The overlap is deliberate: under this problem statement an email
#: address is not a privacy hazard to scrub, it is the identifier being sought.
ACTOR_LABELS = [
    "person",
    "username",
    "nickname",
    "email_address",
    "phone_number",
    "wallet_address",
    "organization",
    "location",
    "messaging_handle",
]

DEFAULT_THRESHOLD = 0.4

#: GLiNER's own limit on a single forward pass, as used by legacy/llm.py.
CHUNK_CHARS = 512

_load_failure: Optional[str] = None


def _llm():
    """Import legacy/llm.py on demand. ~16s the first time; never at import."""
    import llm  # noqa: PLC0415 - deliberately lazy, see the module docstring

    return llm


def _installed() -> bool:
    return importlib.util.find_spec("gliner") is not None


def load_model() -> Optional[Any]:
    """The GLiNER singleton, or None with the reason recorded in status().

    Delegates to legacy/llm.py's `load_gliner()` — the model id, the GPU move and
    the error handling there already work, so this only reaches for the loaded
    object rather than repeating any of it.
    """
    global _load_failure

    if not _installed():
        _load_failure = "the gliner package is not installed (pip install gliner==0.1.6)"
        return None

    try:
        llm = _llm()
    except Exception as exc:  # noqa: BLE001 - a broken legacy import is not fatal here
        _load_failure = f"could not import legacy/llm.py: {type(exc).__name__}: {exc}"
        return None

    try:
        if llm.load_gliner():
            _load_failure = None
            return llm._gliner_model
    except Exception as exc:  # noqa: BLE001 - loading weights can fail any number of ways
        _load_failure = f"{type(exc).__name__}: {exc}"
        return None

    _load_failure = "gliner is installed but the model would not load (see the log)"
    return None


def status() -> dict:
    """Honest state. Never claims a model it does not have."""
    installed = _installed()
    loaded = False
    device = "CPU"

    if installed:
        try:
            llm = _llm()
            state = llm.get_gliner_status(load_model=False)
            loaded = bool(state.get("loaded"))
            device = state.get("device", "CPU")
        except Exception as exc:  # noqa: BLE001
            return {
                "installed": True,
                "loaded": False,
                "device": device,
                "reason": f"could not query legacy/llm.py: {type(exc).__name__}: {exc}",
            }

    if loaded:
        reason = None
    elif not installed:
        reason = "the gliner package is not installed (pip install gliner==0.1.6)"
    else:
        reason = _load_failure or "installed but not loaded yet; call load_model()"

    return {"installed": installed, "loaded": loaded, "device": device, "reason": reason}


def describe() -> str:
    """One line for a scan log."""
    state = status()
    if state["loaded"]:
        return f"gliner: loaded on {state['device']}"
    return f"gliner: unavailable ({state['reason']}) — regex extraction only"


def extract_entities(
    text: str,
    labels: Optional[list[str]] = None,
    threshold: float = DEFAULT_THRESHOLD,
) -> list[dict]:
    """Entities found in `text`, as [{label, text, start, end, score}].

    Returns [] — never raises — when the model is unavailable. Offsets are
    against the original string: the text is chunked to GLiNER's window and each
    chunk's offsets are shifted back.
    """
    if not text:
        return []

    model = load_model()
    if model is None:
        return []

    found: list[dict] = []
    seen: set[tuple[str, int, int]] = set()

    for offset in range(0, len(text), CHUNK_CHARS):
        chunk = text[offset:offset + CHUNK_CHARS]
        try:
            entities = model.predict_entities(chunk, labels or ACTOR_LABELS,
                                              threshold=threshold)
        except Exception:  # noqa: BLE001 - one bad chunk must not lose the rest
            continue
        for entity in entities:
            start = offset + int(entity.get("start", 0))
            end = offset + int(entity.get("end", 0))
            key = (entity.get("label", ""), start, end)
            if key in seen:
                continue
            seen.add(key)
            found.append({
                "label": entity.get("label", ""),
                "text": entity.get("text", text[start:end]),
                "start": start,
                "end": end,
                "score": float(entity.get("score", 0.0)),
            })

    found.sort(key=lambda e: (e["start"], e["end"]))
    return found


#: The regex stage, kept in step with legacy/llm.py's PII_REGEX and widened to
#: the address shapes this project actually meets (bech32 and 0x wallets).
#:
#: This is a copy rather than an import on purpose, and it is the one place in
#: Phase 1 that does not reuse legacy code. `import llm` costs ~16 seconds of
#: torch, and when gliner is not installed its neural stage is a no-op — so
#: delegating would buy nothing but the wait. When gliner *is* installed,
#: redact() calls llm.scrub_pii and gets both stages, which is the path that
#: matters before anything reaches a hosted model.
FALLBACK_PII_REGEX = {
    "EMAIL": r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b",
    "WALLET": r"\b(?:[13LM][1-9A-HJ-NP-Za-km-z]{25,34}|0x[0-9a-fA-F]{40}"
              r"|(?:bc|ltc)1[0-9a-z]{6,71})\b",
    "IP_ADDRESS": r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
    "PHONE": r"\b(?:\+\d{1,3}[\s\-]?)?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{4}\b",
    "SESSION": r"\b05[0-9a-fA-F]{64}\b",
}


def redact(text: str) -> tuple[str, list[str]]:
    """Mask PII before text leaves the box. Returns (clean_text, [removed types]).

    With gliner installed this is legacy/llm.py's two-stage `scrub_pii` — regex,
    then the model. Without it, the regex stage runs here (see
    FALLBACK_PII_REGEX). Either way it masks; a redaction function that fails
    open is worse than useless.
    """
    if not text:
        return "", []

    if _installed():
        try:
            return _llm().scrub_pii(text)
        except Exception:  # noqa: BLE001 - fall through to the regex stage
            pass

    import re  # noqa: PLC0415

    removed: list[str] = []
    for label, pattern in FALLBACK_PII_REGEX.items():
        for match in set(re.findall(pattern, text)):
            text = text.replace(match, f"[{label}]")
            removed.append(label.lower())
    return text, removed
