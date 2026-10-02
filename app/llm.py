"""LLM provider layer. The agent depends on the LLMProvider protocol, so a different
provider (or a fake for tests) can be dropped in without touching agent logic."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Protocol

import anthropic

from .config import get_settings
from .prompts import RETRY_BLOCK, SQL_OUTPUT_SCHEMA, SQL_SYSTEM, SUMMARY_SYSTEM


@dataclass
class SQLGeneration:
    sql: str
    reasoning: str


@dataclass
class FailedAttempt:
    sql: str
    error_type: str
    error: str
    hint: str | None = None


class LLMError(Exception):
    pass


class LLMProvider(Protocol):
    def generate_sql(self, question: str, schema_text: str, history: list[FailedAttempt]) -> SQLGeneration: ...

    def summarize(self, question: str, sql: str, columns: list[str], rows: list[list[Any]],
                  total_rows: int) -> str: ...


def _format_history(history: list[FailedAttempt]) -> str:
    parts = []
    for i, h in enumerate(history, 1):
        block = f"Attempt {i}:\nSQL: {h.sql}\nError ({h.error_type}): {h.error}"
        if h.hint:
            block += f"\nHint: {h.hint}"
        parts.append(block)
    return "\n\n".join(parts)


class AnthropicProvider:
    def __init__(self, client: anthropic.Anthropic | None = None):
        s = get_settings()
        self.client = client or anthropic.Anthropic()
        self.model = s.llm_model
        self.effort = s.llm_effort

    def _text(self, response) -> str:
        if response.stop_reason == "refusal":
            raise LLMError("The model declined to answer this request.")
        if response.stop_reason == "max_tokens":
            raise LLMError("The model response was cut off (max_tokens).")
        return next((b.text for b in response.content if b.type == "text"), "")

    def _create(self, **kwargs):
        # The SDK already retries 408/409/429/5xx and connection errors before raising here.
        try:
            return self.client.messages.create(model=self.model, **kwargs)
        except anthropic.AuthenticationError as e:
            raise LLMError("Anthropic authentication failed - set ANTHROPIC_API_KEY in .env.") from e
        except anthropic.RateLimitError as e:
            raise LLMError("Anthropic rate limit hit; try again shortly.") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"Anthropic API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise LLMError("Could not reach the Anthropic API.") from e
        except TypeError as e:  # raised by the SDK when no credentials can be resolved
            raise LLMError(f"Anthropic client not configured (set ANTHROPIC_API_KEY in .env): {e}") from e

    def generate_sql(self, question: str, schema_text: str, history: list[FailedAttempt]) -> SQLGeneration:
        user = f"Question: {question}"
        if history:
            user += "\n" + RETRY_BLOCK.format(attempts=_format_history(history))
        response = self._create(
            max_tokens=16000,
            # Schema-bearing system prompt is identical across requests -> cacheable prefix.
            system=[{"type": "text", "text": SQL_SYSTEM.format(schema=schema_text),
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_config={"effort": self.effort,
                           "format": {"type": "json_schema", "schema": SQL_OUTPUT_SCHEMA}},
        )
        try:
            data = json.loads(self._text(response))
        except json.JSONDecodeError as e:
            raise LLMError(f"Model returned invalid JSON: {e}") from e
        return SQLGeneration(sql=data.get("sql", "").strip(), reasoning=data.get("reasoning", "").strip())

    def summarize(self, question: str, sql: str, columns: list[str], rows: list[list[Any]],
                  total_rows: int) -> str:
        response = self._create(
            max_tokens=4000,
            system=SUMMARY_SYSTEM,
            messages=[{"role": "user", "content": _summary_prompt(question, sql, columns, rows, total_rows)}],
            output_config={"effort": "low"},
        )
        return self._text(response).strip()


def _summary_prompt(question: str, sql: str, columns: list[str], rows: list[list[Any]], total_rows: int) -> str:
    shown = len(rows)
    table = json.dumps({"columns": columns, "rows": rows}, default=str)
    note = f"(showing {shown} of {total_rows} rows)" if shown < total_rows else f"({total_rows} rows)"
    return f"Question: {question}\n\nSQL:\n{sql}\n\nResults {note}:\n{table}"


class GeminiProvider:
    """Google Gemini via the google-genai SDK. Reads GEMINI_API_KEY (or GOOGLE_API_KEY) from the environment."""

    _RETRYABLE = {429, 500, 502, 503, 504}
    _BLOCKED = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION"}

    def __init__(self, client=None, sleep=time.sleep):
        s = get_settings()
        self.model = s.llm_model
        self.effort = s.llm_effort
        self.max_backoff_retries = s.llm_rate_limit_retries
        self._client = client
        self._sleep = sleep

    @property
    def client(self):
        if self._client is None:
            from google import genai
            try:
                self._client = genai.Client()
            except ValueError as e:  # raised when no API key can be found
                raise LLMError("Gemini client not configured: set GEMINI_API_KEY in .env.") from e
        return self._client

    def _config(self, system: str, json_schema: dict | None = None, max_tokens: int = 8192):
        from google.genai import types
        cfg: dict[str, Any] = {"system_instruction": system, "max_output_tokens": max_tokens, "temperature": 0}
        if json_schema is not None:
            cfg["response_mime_type"] = "application/json"
            cfg["response_json_schema"] = json_schema
        # Gemini 3+ accepts thinking_level; earlier models use budgets, so leave them on their defaults.
        if self.model.startswith("gemini-3"):
            cfg["thinking_config"] = types.ThinkingConfig(thinking_level=self.effort.upper())
        return types.GenerateContentConfig(**cfg)

    def _generate(self, contents: str, config) -> str:
        from google.genai import errors
        for attempt in range(self.max_backoff_retries + 1):
            try:
                response = self.client.models.generate_content(model=self.model, contents=contents, config=config)
                break
            except errors.APIError as e:
                if e.code in self._RETRYABLE and attempt < self.max_backoff_retries:
                    self._sleep(min(2 ** (attempt + 1), 30))  # 2, 4, 8, 16s — rides out free-tier RPM limits
                    continue
                if e.code == 429:
                    raise LLMError("Gemini rate limit / free-tier quota exhausted; wait a minute or switch "
                                   "LLM_MODEL to gemini-3.1-flash-lite.") from e
                if e.code in (401, 403):
                    raise LLMError("Gemini authentication failed: check GEMINI_API_KEY in .env.") from e
                raise LLMError(f"Gemini API error {e.code}: {e.message}") from e
            except Exception as e:  # httpx connection errors etc.
                if isinstance(e, LLMError):
                    raise
                raise LLMError(f"Could not reach the Gemini API: {e}") from e

        candidate = (response.candidates or [None])[0]
        reason = getattr(getattr(candidate, "finish_reason", None), "name", None)
        if reason in self._BLOCKED or (candidate is None and getattr(response, "prompt_feedback", None)):
            raise LLMError("The model declined to answer this request.")
        if reason == "MAX_TOKENS":
            raise LLMError("The model response was cut off (max_output_tokens).")
        return response.text or ""

    def generate_sql(self, question: str, schema_text: str, history: list[FailedAttempt]) -> SQLGeneration:
        user = f"Question: {question}"
        if history:
            user += "\n" + RETRY_BLOCK.format(attempts=_format_history(history))
        # Gemini's schema dialect doesn't need additionalProperties; drop it for compatibility.
        schema = {k: v for k, v in SQL_OUTPUT_SCHEMA.items() if k != "additionalProperties"}
        text = self._generate(user, self._config(SQL_SYSTEM.format(schema=schema_text), schema))
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMError(f"Model returned invalid JSON: {e}") from e
        return SQLGeneration(sql=(data.get("sql") or "").strip(), reasoning=(data.get("reasoning") or "").strip())

    def summarize(self, question: str, sql: str, columns: list[str], rows: list[list[Any]],
                  total_rows: int) -> str:
        text = self._generate(_summary_prompt(question, sql, columns, rows, total_rows),
                              self._config(SUMMARY_SYSTEM, max_tokens=2048))
        return text.strip()


def get_provider() -> LLMProvider:
    """Build the provider selected by the LLM_PROVIDER setting."""
    provider = get_settings().llm_provider
    if provider == "gemini":
        return GeminiProvider()
    if provider == "anthropic":
        return AnthropicProvider()
    raise LLMError(f"Unknown LLM_PROVIDER: {provider!r}")
