"""The AI call itself, with each provider's client stubbed: no network, no cost."""

import json
from types import SimpleNamespace

import polars as pl
import pytest

from app.config import get_settings
from app.enums import FileKind
from app.mapping import llm
from app.mapping.llm import AISuggestionError, suggest_with_ai
from app.mapping.profile import profile_column

PROFILES = [profile_column("Fee: Commission", pl.Series(["105.00", "90.00"]))]
ANSWER = {
    "fields": [
        {
            "field": "commission",
            "column": "Fee: Commission",
            "confidence": 0.93,
            "reason": "Commission fee column",
        }
    ],
    "personal_data_columns": [],
}


class FakeGroq:
    """Records the request and returns a canned completion."""

    last_request: dict = {}
    content: str | None = json.dumps(ANSWER)
    finish_reason = "stop"
    error: Exception | None = None

    def __init__(self, **kwargs):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **request):
        FakeGroq.last_request = request
        if FakeGroq.error:
            raise FakeGroq.error
        message = SimpleNamespace(content=FakeGroq.content)
        usage = SimpleNamespace(prompt_tokens=900, completion_tokens=120)
        choice = SimpleNamespace(message=message, finish_reason=FakeGroq.finish_reason)
        return SimpleNamespace(choices=[choice], usage=usage)


@pytest.fixture
def groq_on(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_enabled", True)
    monkeypatch.setattr(settings, "llm_provider", "groq")
    monkeypatch.setattr(settings, "groq_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_model", "")
    monkeypatch.setattr(llm.groq, "Groq", FakeGroq)
    FakeGroq.content, FakeGroq.finish_reason, FakeGroq.error = json.dumps(ANSWER), "stop", None
    return FakeGroq


def test_groq_request_is_strict_structured_output_with_no_values(groq_on):
    proposal = suggest_with_ai(FileKind.SETTLEMENT, "Marketplace 1", PROFILES)
    assert proposal.fields[0].column == "Fee: Commission"

    request = groq_on.last_request
    assert request["model"] == "openai/gpt-oss-120b"
    fmt = request["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"]["additionalProperties"] is False
    prompt = request["messages"][1]["content"]
    assert "Fee: Commission" in prompt and "105.00" not in prompt and "90.00" not in prompt


@pytest.mark.parametrize(
    ("content", "finish", "message"),
    [
        ("{not json", "stop", "expected shape"),
        (json.dumps({"fields": "nope"}), "stop", "expected shape"),
        (json.dumps(ANSWER)[:40], "length", "incomplete"),
        (None, "stop", "incomplete"),
    ],
)
def test_groq_bad_answers_become_a_fallback_message(groq_on, content, finish, message):
    groq_on.content, groq_on.finish_reason = content, finish
    with pytest.raises(AISuggestionError, match=message):
        suggest_with_ai(FileKind.SETTLEMENT, "Marketplace 1", PROFILES)


def test_groq_connection_failure_is_reported_plainly(groq_on):
    groq_on.error = llm.groq.APIConnectionError(request=None)
    with pytest.raises(AISuggestionError, match="Couldn't reach the AI service"):
        suggest_with_ai(FileKind.SETTLEMENT, "Marketplace 1", PROFILES)


def test_no_key_or_switched_off_means_rules_only(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_enabled", True)
    monkeypatch.setattr(settings, "llm_provider", "groq")
    monkeypatch.setattr(settings, "groq_api_key", "")
    assert not llm.is_configured()
    with pytest.raises(AISuggestionError, match="switched off"):
        suggest_with_ai(FileKind.SETTLEMENT, "Marketplace 1", PROFILES)
    monkeypatch.setattr(settings, "groq_api_key", "set")
    monkeypatch.setattr(settings, "ai_enabled", False)
    assert not llm.is_configured()


def test_model_follows_the_provider_unless_set(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_model", "")
    monkeypatch.setattr(settings, "llm_provider", "anthropic")
    assert settings.llm_model_name == "claude-opus-5-5"
    monkeypatch.setattr(settings, "llm_model", "openai/gpt-oss-20b")
    assert settings.llm_model_name == "openai/gpt-oss-20b"


class FlakyGroq(FakeGroq):
    """Fails with Groq's json_validate_failed the first time, then answers."""

    calls = 0

    def _create(self, **request):
        FlakyGroq.calls += 1
        if FlakyGroq.calls == 1:
            response = SimpleNamespace(status_code=400, headers={}, request=None)
            raise llm.groq.BadRequestError(
                "json_validate_failed: Failed to generate JSON", response=response, body=None
            )
        return super()._create(**request)


def test_groq_invalid_json_is_retried_once(groq_on, monkeypatch):
    FlakyGroq.calls = 0
    monkeypatch.setattr(llm.groq, "Groq", FlakyGroq)
    proposal = suggest_with_ai(FileKind.SETTLEMENT, "Marketplace 1", PROFILES)
    assert FlakyGroq.calls == 2 and proposal.fields[0].column == "Fee: Commission"
