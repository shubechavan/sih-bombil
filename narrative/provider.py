"""provider.py — an optional LLM behind one small interface.

Configured entirely by environment:

    LLM_PROVIDER   gemini | groq | openai | custom
    LLM_API_KEY
    LLM_BASE_URL   custom only
    LLM_MODEL      overrides the preset

With none of it set, `from_env()` returns None and the caller falls back to
`narrative/template.py`. That is the default and it is not an error path — CI
runs it, the offline demo runs it, and the label says so.

TWO THINGS MEASURED AGAINST THE REAL API, NOT ASSUMED
-----------------------------------------------------
**Thinking models silently truncate.** Asked for a two-sentence profile with
`maxOutputTokens: 300`, `gemini-3.6-flash` spent 288 of 375 tokens on internal
reasoning and returned `finishReason: MAX_TOKENS` with eight visible tokens —
half a sentence, which would have been labelled as an AI analysis and shipped.
`_extract` treats MAX_TOKENS with empty or near-empty text as a failure so the
caller falls back, rather than publishing a fragment.

**The model list lies.** `/v1beta/models` advertises models that return 404 on
use: `gemini-2.5-flash` is listed and is closed to new keys. So nothing here
validates `LLM_MODEL` against that list — it sends the request and reports what
comes back, including the useful part of Google's error, which names a
replacement.

The default is `gemini-3.5-flash-lite`: measured on the real endpoint returning
a clean grounded profile in 68 tokens with `finishReason: STOP`.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Callable, Optional

__all__ = [
    "PRESETS",
    "Completion",
    "Provider",
    "ProviderError",
    "from_env",
]


class ProviderError(RuntimeError):
    """The call did not produce usable text. Always caught by the caller."""


@dataclass(frozen=True)
class Completion:
    text: str
    provider: str
    model: str
    usage: dict = field(default_factory=dict)


#: `style` picks the wire format: "gemini" for generateContent, "openai" for
#: the chat/completions shape everything else speaks.
PRESETS: dict[str, dict] = {
    "gemini": {
        "style": "gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "model": "gemini-3.5-flash-lite",
    },
    "groq": {
        "style": "openai",
        "base_url": "https://api.groq.com/openai/v1",
        "model": "llama-3.3-70b-versatile",
    },
    "openai": {
        "style": "openai",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
    },
    "custom": {
        "style": "openai",
        "base_url": None,          # must come from LLM_BASE_URL
        "model": None,             # must come from LLM_MODEL
    },
}


def _post(url: str, headers: dict, payload: dict, timeout: float):
    import requests  # noqa: PLC0415

    return requests.post(url, headers=headers, json=payload, timeout=timeout)


@dataclass(frozen=True)
class Provider:
    """One configured endpoint. Construct through `from_env()`."""

    name: str
    style: str
    base_url: str
    model: str
    api_key: str
    #: Injected so tests can exercise the request and response handling without
    #: a network. Production passes nothing and gets `requests.post`.
    transport: Callable = _post

    def _request(self, prompt: str, *, max_tokens: int, temperature: float):
        if self.style == "gemini":
            url = f"{self.base_url}/models/{self.model}:generateContent"
            headers = {
                "x-goog-api-key": self.api_key,
                "Content-Type": "application/json",
            }
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "maxOutputTokens": max_tokens,
                    "temperature": temperature,
                },
            }
        else:
            url = f"{self.base_url}/chat/completions"
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        return url, headers, payload

    def _extract(self, body: dict) -> tuple[str, dict]:
        if self.style == "gemini":
            candidates = body.get("candidates") or []
            if not candidates:
                raise ProviderError(
                    f"no candidates returned: {json.dumps(body)[:200]}"
                )
            candidate = candidates[0]
            parts = (candidate.get("content") or {}).get("parts") or []
            text = "".join(p.get("text", "") for p in parts).strip()
            finish = str(candidate.get("finishReason") or "")
            usage = body.get("usageMetadata") or {}

            # The measured trap: budget consumed by internal reasoning, leaving
            # a fragment that reads like a finished sentence.
            if finish.upper() == "MAX_TOKENS" and len(text) < 40:
                raise ProviderError(
                    f"model stopped at the token limit with {len(text)} "
                    f"characters of visible output; the budget went to internal "
                    f"reasoning. Use a non-thinking model or raise max_tokens."
                )
            if not text:
                raise ProviderError(f"empty completion (finishReason={finish!r})")
            return text, usage

        choices = body.get("choices") or []
        if not choices:
            raise ProviderError(f"no choices returned: {json.dumps(body)[:200]}")
        message = choices[0].get("message") or {}
        text = (message.get("content") or "").strip()
        finish = str(choices[0].get("finish_reason") or "")
        if not text:
            raise ProviderError(f"empty completion (finish_reason={finish!r})")
        return text, body.get("usage") or {}

    def complete(
        self,
        prompt: str,
        *,
        max_tokens: int = 400,
        temperature: float = 0.2,
        timeout: float = 30.0,
    ) -> Completion:
        url, headers, payload = self._request(
            prompt, max_tokens=max_tokens, temperature=temperature
        )
        try:
            response = self.transport(url, headers, payload, timeout)
        except Exception as exc:  # noqa: BLE001 - any transport failure
            raise ProviderError(f"request to {self.name} failed: {exc}") from exc

        status = getattr(response, "status_code", 0)
        if status != 200:
            detail = ""
            try:
                body = response.json()
                detail = (body.get("error") or {}).get("message") or ""
            except Exception:  # noqa: BLE001 - not every error body is JSON
                detail = (getattr(response, "text", "") or "")[:200]
            raise ProviderError(f"{self.name} returned HTTP {status}: {detail}")

        text, usage = self._extract(response.json())
        return Completion(
            text=text, provider=self.name, model=self.model, usage=usage
        )


def from_env(env: Optional[dict] = None, *, transport: Callable = _post
             ) -> Optional[Provider]:
    """Build a provider from the environment, or None when unconfigured.

    None is the normal case, not a failure: it means the rule-based profile is
    what this deployment produces, and the label will say so.
    """
    source = os.environ if env is None else env

    name = (source.get("LLM_PROVIDER") or "").strip().lower()
    api_key = (source.get("LLM_API_KEY") or "").strip()
    if not name or not api_key:
        return None
    if name not in PRESETS:
        raise ProviderError(
            f"unknown LLM_PROVIDER {name!r}; expected one of "
            f"{', '.join(sorted(PRESETS))}"
        )

    preset = PRESETS[name]
    base_url = (source.get("LLM_BASE_URL") or preset["base_url"] or "").strip()
    model = (source.get("LLM_MODEL") or preset["model"] or "").strip()

    if not base_url:
        raise ProviderError(f"LLM_PROVIDER={name} needs LLM_BASE_URL")
    if not model:
        raise ProviderError(f"LLM_PROVIDER={name} needs LLM_MODEL")

    return Provider(
        name=name,
        style=preset["style"],
        base_url=base_url.rstrip("/"),
        model=model,
        api_key=api_key,
        transport=transport,
    )
