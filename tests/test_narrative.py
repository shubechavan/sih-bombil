"""test_narrative.py — behavioural profiles, and the three promises around them.

1. A profile cannot move an attribution number.
2. No raw post text reaches an external model.
3. A template is never presented as AI output.

Everything here runs offline. The provider tests drive a fake transport, so the
request and response handling is exercised without a network — including the
two failure modes measured against the real Gemini endpoint.
"""

from __future__ import annotations

import ast
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import narrative  # noqa: E402
from narrative.features import ActorFeatures, PersonaFeatures  # noqa: E402
from narrative.prompt import build, payload_for  # noqa: E402
from narrative.provider import (  # noqa: E402
    PRESETS,
    Provider,
    ProviderError,
    from_env,
)
from narrative.template import KIND, LABEL, render  # noqa: E402


# ── fixtures ──────────────────────────────────────────────────────────────────

def persona(pid=1, handle="Dr3adPirat3", **kw) -> PersonaFeatures:
    base = dict(
        persona_id=pid, handle=handle, source_name="market_alpha",
        source_reliability=0.72, first_post="2025-09", last_post="2026-02",
        post_count=11, category="drugs", peak_hours=(22, 23, 0, 1),
        trade_terms=("escrow", "reship"),
        style={"avg_sentence_words": 9.8, "ellipsis_per_sentence": 0.65,
               "type_token_ratio": 0.33, "word_count": 636},
    )
    base.update(kw)
    return PersonaFeatures(**base)


@pytest.fixture
def features() -> ActorFeatures:
    return ActorFeatures(actor_id=1, personas=(
        persona(1, "Dr3adPirat3"),
        persona(9, "Dread_P1rate", source_name="forum_beta",
                first_post="2025-10", last_post="2026-07"),
    ))


class FakeResponse:
    def __init__(self, status: int, body: dict):
        self.status_code = status
        self._body = body
        self.text = json.dumps(body)

    def json(self) -> dict:
        return self._body


def transport_returning(status: int, body: dict):
    calls: list[dict] = []

    def transport(url, headers, payload, timeout):
        calls.append({"url": url, "headers": headers, "payload": payload})
        return FakeResponse(status, body)

    transport.calls = calls  # type: ignore[attr-defined]
    return transport


def gemini_body(text: str, finish: str = "STOP") -> dict:
    return {
        "candidates": [{
            "content": {"parts": [{"text": text}]},
            "finishReason": finish,
        }],
        "usageMetadata": {"promptTokenCount": 79, "candidatesTokenCount": 68},
    }


# ── promise 1: it cannot move a number ────────────────────────────────────────

def test_narrative_cannot_reach_the_scorer():
    """Parsed, not grepped, so prose cannot fail it and aliases cannot hide."""
    for module in ("__init__", "features", "prompt", "provider", "template"):
        path = ROOT / "narrative" / f"{module}.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
                imported.update(f"{node.module}.{a.name}" for a in node.names)
        for forbidden in ("score", "score.attribution", "link.resolve",
                          "link.cluster"):
            assert not any(
                n == forbidden or n.startswith(forbidden + ".") for n in imported
            ), f"narrative/{module}.py must not import {forbidden}"


def test_the_profile_table_is_not_read_by_the_scorer():
    """`actor_profiles` must not appear anywhere under score/."""
    for path in (ROOT / "score").glob("*.py"):
        assert "actor_profiles" not in path.read_text(encoding="utf-8")
        assert "narrative" not in path.read_text(encoding="utf-8")


# ── promise 2: no raw post text on the wire ───────────────────────────────────

def test_the_payload_carries_no_free_prose(features):
    """Every string in the payload is a handle, a label or a fixed term.

    The features module never collects post bodies, so there is nothing to
    redact — this asserts the property rather than trusting the docstring.
    """
    payload = payload_for(features)
    strings: list[str] = []

    def walk(value):
        if isinstance(value, str):
            strings.append(value)
        elif isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    walk(payload)
    for text in strings:
        # Nothing in the payload should look like a sentence of prose.
        assert len(text.split()) < 25, f"suspiciously long string on the wire: {text[:80]}"


def test_the_payload_is_an_explicit_allow_list(features):
    """Adding a field to the dataclass must not start transmitting it."""
    widened = replace(features.personas[0])
    object.__setattr__(widened, "secret_note", "raw scraped confession")
    payload = payload_for(ActorFeatures(actor_id=1, personas=(widened,)))
    assert "secret_note" not in json.dumps(payload)
    assert "confession" not in json.dumps(payload)


def test_redaction_runs_over_the_payload(monkeypatch, features):
    """Strings go through redact(); numbers do not."""
    seen: list[str] = []

    import extract.gliner_extract as gx

    def fake_redact(text: str):
        seen.append(text)
        return f"[clean]{text}", []

    monkeypatch.setattr(gx, "redact", fake_redact)
    payload = payload_for(features)

    assert seen, "redact() was never called"
    assert payload["personas"][0]["handle"].startswith("[clean]")
    assert payload["personas"][0]["post_count"] == 11, "numbers must not be mangled"


def test_an_email_in_a_handle_is_redacted(features):
    """The regex stage runs even without GLiNER installed — it fails closed."""
    leaky = replace(features.personas[0], handle="contact vendor@example.com now")
    payload = payload_for(ActorFeatures(actor_id=1, personas=(leaky,)))
    assert "vendor@example.com" not in json.dumps(payload)


def test_the_instructions_forbid_inference(features):
    text = build(features)
    for rule in ("Do not infer identity", "not evidence of where anyone",
                 "Describe behaviour, not character"):
        assert rule in text


# ── promise 3: a template is never sold as AI ─────────────────────────────────

def test_the_template_declares_itself(features):
    profile = narrative.generate(features, provider=None, allow_llm=False)
    assert profile.kind == "rule-based"
    assert profile.is_ai is False
    assert "no AI" in profile.label


def test_an_ai_profile_is_labelled_ai(features):
    provider = Provider(name="gemini", style="gemini", base_url="https://x",
                        model="m", api_key="k",
                        transport=transport_returning(200, gemini_body("A summary.")))
    profile = narrative.generate(features, provider)
    assert profile.kind == "ai"
    assert "AI-generated summary" in profile.label
    assert profile.model == "m"


def test_the_two_labels_are_never_equal():
    assert narrative.AI_LABEL != LABEL
    assert "AI" in narrative.AI_LABEL
    assert KIND == "rule-based"


def test_a_failed_call_falls_back_and_records_why(features):
    provider = Provider(name="gemini", style="gemini", base_url="https://x",
                        model="m", api_key="k",
                        transport=transport_returning(
                            429, {"error": {"message": "rate limited"}}))
    profile = narrative.generate(features, provider)
    assert profile.kind == "rule-based"
    assert "429" in (profile.fallback_reason or "")
    assert "rate limited" in (profile.fallback_reason or "")


# ── the measured provider traps ───────────────────────────────────────────────

def test_a_thinking_model_that_truncates_is_treated_as_a_failure(features):
    """Measured: gemini-3.6-flash spent 288 of 375 tokens thinking and returned
    eight visible tokens with MAX_TOKENS. That fragment must not ship."""
    provider = Provider(name="gemini", style="gemini", base_url="https://x",
                        model="gemini-3.6-flash", api_key="k",
                        transport=transport_returning(
                            200, gemini_body("This actor operates between 22",
                                             finish="MAX_TOKENS")))
    profile = narrative.generate(features, provider)
    assert profile.kind == "rule-based"
    assert "token limit" in (profile.fallback_reason or "")


def test_a_long_completion_at_the_token_limit_is_still_accepted(features):
    """A full answer that merely ran out of room is usable; a fragment is not."""
    long_text = "This actor posts overnight and trades in documents. " * 3
    provider = Provider(name="gemini", style="gemini", base_url="https://x",
                        model="m", api_key="k",
                        transport=transport_returning(
                            200, gemini_body(long_text, finish="MAX_TOKENS")))
    assert narrative.generate(features, provider).kind == "ai"


def test_an_empty_completion_is_a_failure(features):
    provider = Provider(name="gemini", style="gemini", base_url="https://x",
                        model="m", api_key="k",
                        transport=transport_returning(200, gemini_body("")))
    assert narrative.generate(features, provider).kind == "rule-based"


# ── provider configuration ────────────────────────────────────────────────────

def test_no_configuration_means_no_provider():
    assert from_env({}) is None
    assert from_env({"LLM_PROVIDER": "gemini"}) is None      # no key
    assert from_env({"LLM_API_KEY": "abc"}) is None          # no provider


def test_an_unknown_provider_is_rejected_by_name():
    with pytest.raises(ProviderError) as exc:
        from_env({"LLM_PROVIDER": "wishful", "LLM_API_KEY": "k"})
    assert "wishful" in str(exc.value)


def test_custom_requires_a_base_url():
    with pytest.raises(ProviderError):
        from_env({"LLM_PROVIDER": "custom", "LLM_API_KEY": "k",
                  "LLM_MODEL": "m"})


def test_the_gemini_default_is_the_model_that_was_measured():
    assert PRESETS["gemini"]["model"] == "gemini-3.5-flash-lite"


def test_the_model_can_be_overridden():
    provider = from_env({"LLM_PROVIDER": "gemini", "LLM_API_KEY": "k",
                         "LLM_MODEL": "gemini-3.8-flash"})
    assert provider.model == "gemini-3.8-flash"


def test_gemini_sends_the_key_as_a_header_not_a_query_parameter(features):
    """A key in a URL lands in logs and proxy history."""
    transport = transport_returning(200, gemini_body("Summary."))
    provider = Provider(name="gemini", style="gemini",
                        base_url="https://example.invalid/v1beta",
                        model="m", api_key="secret-key", transport=transport)
    provider.complete("prompt")
    call = transport.calls[0]
    assert "secret-key" not in call["url"]
    assert call["headers"]["x-goog-api-key"] == "secret-key"


def test_an_openai_style_provider_uses_chat_completions(features):
    transport = transport_returning(200, {
        "choices": [{"message": {"content": "A summary."},
                     "finish_reason": "stop"}],
        "usage": {"total_tokens": 100},
    })
    provider = Provider(name="groq", style="openai",
                        base_url="https://api.groq.com/openai/v1",
                        model="llama", api_key="k", transport=transport)
    completion = provider.complete("prompt")
    assert completion.text == "A summary."
    assert transport.calls[0]["url"].endswith("/chat/completions")
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer k"


# ── the template's own content ────────────────────────────────────────────────

def test_the_template_is_deterministic(features):
    assert render(features) == render(features)


def test_the_template_names_every_handle(features):
    text = render(features)
    for p in features.personas:
        assert p.handle in text


def test_the_template_reports_a_refusal_rather_than_omitting_it():
    feats = ActorFeatures(actor_id=7, personas=(
        persona(7, "paperghost", style={},
                stylometry_refused="152 characters, below the 300-character floor"),
    ))
    text = render(feats)
    assert "refused" in text.lower()
    assert "300-character floor" in text
    assert "Nothing above describes how that persona writes" in text


def test_the_template_disclaims_timezone_inference(features):
    assert "not evidence of a timezone" in render(features)


def test_an_actor_with_no_personas_says_so():
    assert "nothing to describe" in render(ActorFeatures(actor_id=99))


def test_midnight_spanning_hours_read_as_one_window(features):
    """22,23,0,1 is one overnight run, not two disjoint ones."""
    text = render(features)
    assert "22:00–01:59" in text


# ── the cache ─────────────────────────────────────────────────────────────────

def test_the_fingerprint_changes_when_the_features_change(features):
    before = features.fingerprint()
    moved = ActorFeatures(actor_id=1, personas=(
        replace(features.personas[0], post_count=12),
        features.personas[1],
    ))
    assert moved.fingerprint() != before


def test_the_fingerprint_is_stable_for_equal_features(features):
    same = ActorFeatures(actor_id=1, personas=features.personas)
    assert same.fingerprint() == features.fingerprint()


def test_the_fingerprint_ignores_persona_ordering(features):
    flipped = ActorFeatures(actor_id=1, personas=tuple(reversed(features.personas)))
    assert flipped.fingerprint() == features.fingerprint()
