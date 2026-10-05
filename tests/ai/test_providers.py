"""The three provider adapters, offline: fake SDK modules stand in for the network."""

import sys
import types

import pytest

from ailr.exceptions import LLMError
from ailr.llm import retry as retry_mod
from ailr.llm.base import ToolSchema
from ailr.llm.providers.anthropic import AnthropicClient
from ailr.llm.providers.gemini import GeminiClient, _is_retryable
from ailr.llm.providers.openai import OpenAIClient

_TOOL = ToolSchema(name="record", description="d", input_schema={"type": "object", "properties": {}})


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    monkeypatch.setattr(retry_mod.time, "sleep", lambda _seconds: None)


class _Calls:
    """Stands in for the SDK's create(): raises the queued errors in turn, then answers."""

    def __init__(self, reply, errors=()):
        self.reply, self.errors, self.kwargs = reply, list(errors), []

    def __call__(self, **kwargs):
        self.kwargs.append(kwargs)
        if self.errors:
            raise self.errors.pop(0)
        return self.reply


def _add_sdk_errors(mod):
    """The exception layout anthropic and openai share, down to which class subclasses which."""
    class APIError(Exception):
        pass

    class APIConnectionError(APIError):
        pass

    class APITimeoutError(APIConnectionError):
        pass

    class APIStatusError(APIError):
        def __init__(self, status_code=500):
            super().__init__(f"HTTP {status_code}")
            self.status_code = status_code

    class RateLimitError(APIStatusError):
        pass

    class AuthenticationError(APIStatusError):
        pass

    class BadRequestError(APIStatusError):
        pass

    class InternalServerError(APIStatusError):
        pass

    for cls in (APIError, APIConnectionError, APITimeoutError, APIStatusError, RateLimitError,
                AuthenticationError, BadRequestError, InternalServerError):
        setattr(mod, cls.__name__, cls)


def _ask(client):
    return client.complete_structured(system="s", user_message="u", tool_schema=_TOOL)


# ----- Anthropic -----


@pytest.fixture
def anthropic_sdk(monkeypatch):
    mod = types.ModuleType("anthropic")
    _add_sdk_errors(mod)
    mod.created = []

    class Anthropic:
        def __init__(self, **kwargs):
            mod.created.append(kwargs)
            self.messages = types.SimpleNamespace(create=mod.calls)

    mod.Anthropic = Anthropic
    monkeypatch.setitem(sys.modules, "anthropic", mod)
    return mod


def _anthropic_reply(blocks=None, **usage):
    blocks = blocks or [types.SimpleNamespace(type="tool_use", name=_TOOL.name, input={"answer": 1})]
    return types.SimpleNamespace(content=blocks, usage=types.SimpleNamespace(**{"input_tokens": 10, "output_tokens": 5, **usage}))


def _anthropic(mod, reply=None, errors=()):
    mod.calls = _Calls(reply or _anthropic_reply(), errors)
    return AnthropicClient(model="claude-x", max_retries=2)


class TestAnthropic:
    def test_cache_reads_and_writes_count_as_input(self, anthropic_sdk):
        reply = _anthropic_reply(input_tokens=100, cache_read_input_tokens=900, cache_creation_input_tokens=50,
                                 output_tokens=20)
        _out, meta = _ask(_anthropic(anthropic_sdk, reply))
        assert (meta.input_tokens, meta.cached_input_tokens, meta.cache_creation_tokens, meta.output_tokens) == (
            1050, 900, 50, 20)

    def test_the_sdk_leaves_retrying_to_ailr(self, anthropic_sdk):
        _anthropic(anthropic_sdk)
        assert anthropic_sdk.created[-1]["max_retries"] == 0

    def test_a_dropped_connection_is_retried(self, anthropic_sdk):
        client = _anthropic(anthropic_sdk, errors=[anthropic_sdk.APIConnectionError()])
        out, _meta = _ask(client)
        assert out == {"answer": 1} and len(anthropic_sdk.calls.kwargs) == 2

    def test_a_timeout_and_a_server_error_are_retried(self, anthropic_sdk):
        client = _anthropic(anthropic_sdk, errors=[anthropic_sdk.APITimeoutError(), anthropic_sdk.InternalServerError(503)])
        _ask(client)
        assert len(anthropic_sdk.calls.kwargs) == 3

    def test_a_bad_request_fails_at_once(self, anthropic_sdk):
        client = _anthropic(anthropic_sdk, errors=[anthropic_sdk.BadRequestError(400)])
        with pytest.raises(LLMError, match="rejected request"):
            _ask(client)
        assert len(anthropic_sdk.calls.kwargs) == 1

    def test_retrying_stops_at_the_limit(self, anthropic_sdk):
        client = _anthropic(anthropic_sdk, errors=[anthropic_sdk.RateLimitError(429)] * 5)
        with pytest.raises(LLMError):
            _ask(client)
        assert len(anthropic_sdk.calls.kwargs) == 3          # the first try plus max_retries=2

    def test_a_reply_without_the_tool_call_fails(self, anthropic_sdk):
        reply = _anthropic_reply(blocks=[types.SimpleNamespace(type="text", name=None)])
        with pytest.raises(LLMError, match="No tool_use block"):
            _ask(_anthropic(anthropic_sdk, reply))

    def test_the_system_prompt_is_marked_for_caching(self, anthropic_sdk):
        client = _anthropic(anthropic_sdk)
        _ask(client)
        [system] = anthropic_sdk.calls.kwargs[0]["system"]
        assert system["cache_control"] == {"type": "ephemeral"}


# ----- OpenAI -----


@pytest.fixture
def openai_sdk(monkeypatch):
    mod = types.ModuleType("openai")
    _add_sdk_errors(mod)
    mod.created = []

    class OpenAI:
        def __init__(self, **kwargs):
            mod.created.append(kwargs)
            self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=mod.calls))

    mod.OpenAI = OpenAI
    monkeypatch.setitem(sys.modules, "openai", mod)
    return mod


def _openai_reply(arguments='{"answer": 1}', prompt_tokens=100, completion_tokens=20, cached_tokens=None):
    call = types.SimpleNamespace(function=types.SimpleNamespace(arguments=arguments))
    details = types.SimpleNamespace(cached_tokens=cached_tokens) if cached_tokens is not None else None
    usage = types.SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                                  prompt_tokens_details=details)
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(tool_calls=[call]))],
                                 usage=usage)


def _openai(mod, reply=None, errors=()):
    mod.calls = _Calls(reply or _openai_reply(), errors)
    return OpenAIClient(model="gpt-x", max_retries=2)


class TestOpenAI:
    def test_the_sdk_leaves_retrying_to_ailr(self, openai_sdk):
        _openai(openai_sdk)
        assert openai_sdk.created[-1]["max_retries"] == 0

    def test_a_dropped_connection_is_retried(self, openai_sdk):
        out, _meta = _ask(_openai(openai_sdk, errors=[openai_sdk.APIConnectionError()]))
        assert out == {"answer": 1} and len(openai_sdk.calls.kwargs) == 2

    def test_prompt_tokens_already_include_the_cached_ones(self, openai_sdk):
        _out, meta = _ask(_openai(openai_sdk, _openai_reply(prompt_tokens=100, cached_tokens=80)))
        assert (meta.input_tokens, meta.cached_input_tokens, meta.output_tokens) == (100, 80, 20)

    def test_arguments_that_are_not_json_fail(self, openai_sdk):
        with pytest.raises(LLMError, match="invalid JSON"):
            _ask(_openai(openai_sdk, _openai_reply(arguments="{not json")))

    def test_the_seed_is_sent(self, openai_sdk):
        client = _openai(openai_sdk)
        _ask(client)
        assert openai_sdk.calls.kwargs[0]["seed"] == client.effective_seed == 42


# ----- Gemini -----


class _GoogleError(Exception):
    def __init__(self, message, code=None):
        super().__init__(message)
        if code is not None:
            self.code = code


@pytest.mark.parametrize("message,code,retry", [
    ("504 Deadline of 400.0s exceeded", 504, True),
    ("Deadline of 400.0s exceeded", None, True),            # a duration is not a status code
    ("429 Resource has been exhausted", 429, True),
    ("503 The service is currently unavailable.", 503, True),
    ("400 Request contains an invalid argument.", 400, False),
    ("403 Permission denied", 403, False),
    ("404 models/gemini-x is not found", 404, False),       # no marker in the text: only the code says so
    ("Request contains an invalid argument.", None, False),
    ("API key not valid. Please pass a valid API key.", None, False),
])
def test_gemini_retries_what_could_succeed_next_time(message, code, retry):
    assert _is_retryable(_GoogleError(message, code)) is retry


@pytest.fixture
def genai(monkeypatch):
    mod = types.ModuleType("google.generativeai")
    google = types.ModuleType("google")
    google.generativeai = mod

    class GenerativeModel:
        def __init__(self, name, **kwargs):
            self.name, self.kwargs = name, kwargs

        def generate_content(self, message, **kwargs):
            return mod.calls(message=message, **kwargs)

    mod.GenerativeModel = GenerativeModel
    mod.configure = lambda **_kwargs: None
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.generativeai", mod)
    return mod


def _gemini_reply(prompt_token_count=100, candidates_token_count=20):
    call = types.SimpleNamespace(name=_TOOL.name, args={"answer": 1, "items": ["a"]})
    content = types.SimpleNamespace(parts=[types.SimpleNamespace(function_call=call)])
    usage = types.SimpleNamespace(prompt_token_count=prompt_token_count, candidates_token_count=candidates_token_count)
    return types.SimpleNamespace(candidates=[types.SimpleNamespace(content=content)], usage_metadata=usage)


def _gemini(mod, errors=()):
    mod.calls = _Calls(_gemini_reply(), errors)
    return GeminiClient(model="gemini-x", max_retries=2)


class TestGemini:
    def test_the_function_call_and_token_counts_come_back(self, genai):
        out, meta = _ask(_gemini(genai))
        assert out == {"answer": 1, "items": ["a"]}
        assert (meta.input_tokens, meta.output_tokens) == (100, 20)

    def test_a_deadline_is_retried(self, genai):
        out, _meta = _ask(_gemini(genai, errors=[_GoogleError("504 Deadline of 400.0s exceeded", 504)]))
        assert out["answer"] == 1 and len(genai.calls.kwargs) == 2

    def test_an_invalid_argument_fails_at_once(self, genai):
        with pytest.raises(LLMError):
            _ask(_gemini(genai, errors=[_GoogleError("400 Request contains an invalid argument.", 400)]))
        assert len(genai.calls.kwargs) == 1
