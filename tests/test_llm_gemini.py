"""GeminiProvider tests with a fake google-genai client — no network or API key required."""

import json
from types import SimpleNamespace

import pytest
from google.genai import errors

from app import config, llm
from app.llm import FailedAttempt, GeminiProvider, LLMError


def _response(text, finish="STOP"):
    return SimpleNamespace(text=text, prompt_feedback=None,
                           candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name=finish))])


def _api_error(code):
    return errors.APIError(code, {"error": {"code": code, "message": f"err {code}", "status": "X"}})


class FakeModels:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def generate_content(self, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


def make(outcomes):
    models = FakeModels(outcomes)
    sleeps = []
    provider = GeminiProvider(client=SimpleNamespace(models=models), sleep=sleeps.append)
    return provider, models, sleeps


def test_generate_sql_parses_structured_json():
    p, models, _ = make([_response(json.dumps({"reasoning": "count rows", "sql": "SELECT COUNT(*) FROM orders"}))])
    gen = p.generate_sql("how many orders?", "TABLE orders", [])
    assert gen.sql == "SELECT COUNT(*) FROM orders" and gen.reasoning == "count rows"
    cfg = models.calls[0]["config"]
    assert cfg.response_mime_type == "application/json"
    assert "additionalProperties" not in cfg.response_json_schema
    assert "TABLE orders" in cfg.system_instruction


def test_retry_history_is_sent():
    p, models, _ = make([_response(json.dumps({"reasoning": "", "sql": "SELECT 1"}))])
    p.generate_sql("q", "schema", [FailedAttempt(sql="SELECT total FROM orders", error_type="undefined_column",
                                                 error='column "total" does not exist', hint="Did you mean x?")])
    sent = models.calls[0]["contents"]
    assert "previous_attempts" in sent and "undefined_column" in sent and "Did you mean x?" in sent


def test_backs_off_on_rate_limit_then_succeeds():
    p, models, sleeps = make([_api_error(429), _api_error(503), _response('{"reasoning": "", "sql": "SELECT 1"}')])
    assert p.generate_sql("q", "s", []).sql == "SELECT 1"
    assert len(models.calls) == 3 and sleeps == [2, 4]


def test_gives_up_after_backoff_budget():
    p, _, sleeps = make([_api_error(429)] * 10)
    with pytest.raises(LLMError, match="quota"):
        p.generate_sql("q", "s", [])
    assert len(sleeps) == p.max_backoff_retries


def test_auth_error_not_retried():
    p, models, sleeps = make([_api_error(403)])
    with pytest.raises(LLMError, match="GEMINI_API_KEY"):
        p.generate_sql("q", "s", [])
    assert len(models.calls) == 1 and sleeps == []


def test_safety_block_raises():
    p, _, _ = make([_response(None, finish="SAFETY")])
    with pytest.raises(LLMError, match="declined"):
        p.generate_sql("q", "s", [])


def test_invalid_json_raises():
    p, _, _ = make([_response("not json")])
    with pytest.raises(LLMError, match="invalid JSON"):
        p.generate_sql("q", "s", [])


def test_summarize_returns_text():
    p, models, _ = make([_response("  Electronics led with $1,234.  ")])
    out = p.summarize("q", "SELECT 1", ["category", "revenue"], [["Electronics", 1234]], 1)
    assert out == "Electronics led with $1,234."
    assert models.calls[0]["config"].response_mime_type is None


def test_get_provider_selects_by_setting(monkeypatch):
    monkeypatch.setattr(llm, "get_settings", lambda: config.Settings(_env_file=None, llm_provider="gemini"))
    assert isinstance(llm.get_provider(), GeminiProvider)
    monkeypatch.setattr(llm, "get_settings", lambda: config.Settings(_env_file=None, llm_provider="anthropic"))
    assert isinstance(llm.get_provider(), llm.AnthropicProvider)


def test_default_model_follows_provider():
    assert config.Settings(_env_file=None, llm_provider="gemini").llm_model == "gemini-3.5-flash"
    assert config.Settings(_env_file=None, llm_provider="anthropic").llm_model == "claude-opus-5-5"
    assert config.Settings(_env_file=None, llm_provider="gemini", llm_model="x").llm_model == "x"
