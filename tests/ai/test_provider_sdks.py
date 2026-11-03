"""The provider adapters through their real SDKs over a mocked transport; each skips without its SDK."""

import importlib
import json

import pytest

from ailr.crosschecker import SCREENING_CHECK_TOOL, build_tool_schema
from ailr.exceptions import LLMError
from ailr.extraction import FieldSpec, build_extraction_tool_schema
from ailr.llm import retry as retry_mod
from ailr.llm.base import ToolSchema
from ailr.llm.providers.anthropic import AnthropicClient
from ailr.llm.providers.gemini import GeminiClient
from ailr.llm.providers.openai import OpenAIClient
from ailr.reviewers import _add_flag_check_to_schema, _build_screening_tool

_TOOL = ToolSchema(name="record", description="d", input_schema={"type": "object", "properties": {}})

_FIELDS = [
    FieldSpec(name="design", type="string", description="study design"),
    FieldSpec(name="n", type="number", description="sample size"),
    FieldSpec(name="modes", type="list", description="modes", enum=["audio", "video"]),
    FieldSpec(name="kind", type="string", description="kind", enum=["a", "b"]),
]

# every schema ailr sends, each with keywords some SDKs reject: minimum/maximum, ["string", "null"], a null enum
_SCHEMAS = {
    "screening": _build_screening_tool(["I1", "I2"]),
    "screening+flag": _add_flag_check_to_schema(_build_screening_tool(["I1", "I2"]), ["I1", "I2"]),
    "extraction": build_extraction_tool_schema(_FIELDS),
    "crosscheck-screening": SCREENING_CHECK_TOOL,
    "crosscheck-extraction": build_tool_schema(["design", "n"]),
}


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    monkeypatch.setattr(retry_mod.time, "sleep", lambda _seconds: None)


def _http_library(sdk_http_client):
    """httpx or httpx2, whichever the SDK's HTTP client is built on."""
    return importlib.import_module(sdk_http_client.__mro__[1].__module__.split(".")[0])


def _serve(hx, *replies):
    """A transport answering each request with the next reply, the last one repeating."""
    sent, queue = [], list(replies)

    def handle(request):
        body = json.loads(request.content)
        sent.append(body)
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(reply, Exception):
            raise reply
        status, payload = reply(body)
        return hx.Response(status, json=payload)

    return hx.MockTransport(handle), sent


def _ask(client, tool=_TOOL):
    return client.complete_structured(system="s", user_message="u", tool_schema=tool)


# ----- Anthropic -----


def _anthropic_answer(body):
    return 200, {
        "id": "msg", "type": "message", "role": "assistant", "model": body["model"],
        "content": [
            {"type": "thinking", "thinking": "", "signature": "sig"},  # the current models think first
            {"type": "tool_use", "id": "t", "name": body["tools"][0]["name"], "input": {"answer": 1}},
        ],
        "stop_reason": "tool_use", "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


@pytest.fixture
def anthropic_api():
    anthropic = pytest.importorskip("anthropic")
    hx = _http_library(anthropic.DefaultHttpxClient)

    def connect(*replies):
        transport, sent = _serve(hx, *replies)
        client = AnthropicClient(model="claude-x", max_retries=2, api_key="k")
        client._client = anthropic.Anthropic(
            api_key="k", max_retries=0, http_client=anthropic.DefaultHttpxClient(transport=transport))
        return client, sent

    return connect


@pytest.mark.parametrize("tool", _SCHEMAS.values(), ids=_SCHEMAS.keys())
def test_anthropic_takes_every_schema_as_is(anthropic_api, tool):
    client, sent = anthropic_api(_anthropic_answer)
    out, _meta = _ask(client, tool)
    assert out == {"answer": 1} and sent[0]["tools"][0]["input_schema"] == tool.input_schema


# ----- OpenAI -----


def _openai_answer(body):
    call = {"id": "t", "type": "function",
            "function": {"name": body["tools"][0]["function"]["name"], "arguments": '{"answer": 1}'}}
    return 200, {
        "id": "c", "object": "chat.completion", "created": 1, "model": body["model"],
        "choices": [{"index": 0, "finish_reason": "tool_calls",
                     "message": {"role": "assistant", "content": None, "tool_calls": [call]}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


@pytest.fixture
def openai_api():
    openai = pytest.importorskip("openai")
    hx = _http_library(openai.DefaultHttpxClient)

    def connect(*replies):
        transport, sent = _serve(hx, *replies)
        client = OpenAIClient(model="gpt-x", max_retries=2, api_key="k")
        client._client = openai.OpenAI(
            api_key="k", max_retries=0, http_client=openai.DefaultHttpxClient(transport=transport))
        return client, sent

    return connect


@pytest.mark.parametrize("tool", _SCHEMAS.values(), ids=_SCHEMAS.keys())
def test_openai_takes_every_schema_as_is(openai_api, tool):
    client, sent = openai_api(_openai_answer)
    out, _meta = _ask(client, tool)
    assert out == {"answer": 1} and sent[0]["tools"][0]["function"]["parameters"] == tool.input_schema


# ----- Gemini -----


def _field(message, name):
    """SDK releases differ in camelCase or snake_case keys; the API reads both."""
    camel = name.split("_")[0] + "".join(word.title() for word in name.split("_")[1:])
    return message.get(camel, message.get(name))


def _declaration(body):
    return body["tools"][0]["functionDeclarations"][0]


def _gemini_answer(finish="STOP", **usage):
    def reply(body):
        call = {"functionCall": {"name": _declaration(body)["name"], "args": {"answer": 1}}}
        return 200, {
            "candidates": [{"content": {"role": "model", "parts": [call]}, "finishReason": finish}],
            "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 20, **usage},
        }
    return reply


def _gemini_error(code, status):
    return lambda _body: (code, {"error": {"code": code, "message": status.lower(), "status": status}})


@pytest.fixture
def gemini_api():
    pytest.importorskip("google.genai")
    from google import genai
    from google.genai import types
    hx = pytest.importorskip("httpx")

    def connect(*replies):
        transport, sent = _serve(hx, *replies)
        client = GeminiClient(model="gemini-x", max_retries=2, api_key="k")
        client._client = genai.Client(api_key="k", http_options=types.HttpOptions(client_args={"transport": transport}))
        return client, sent

    connect.hx = hx
    return connect


@pytest.mark.parametrize("tool", _SCHEMAS.values(), ids=_SCHEMAS.keys())
def test_gemini_takes_every_schema_as_is(gemini_api, tool):
    client, sent = gemini_api(_gemini_answer())
    out, _meta = _ask(client, tool)
    assert out == {"answer": 1} and _field(_declaration(sent[0]), "parameters_json_schema") == tool.input_schema


def test_gemini_is_forced_to_call_the_tool(gemini_api):
    client, sent = gemini_api(_gemini_answer())
    _ask(client)
    calling = _field(sent[0]["toolConfig"], "function_calling_config")
    assert calling["mode"] == "ANY" and _field(calling, "allowed_function_names") == [_TOOL.name]
    assert sent[0]["generationConfig"] == {"maxOutputTokens": 4096}


def test_gemini_thinking_counts_as_output(gemini_api):
    client, _sent = gemini_api(_gemini_answer(thoughtsTokenCount=300, cachedContentTokenCount=80))
    _out, meta = _ask(client)
    assert (meta.input_tokens, meta.cached_input_tokens, meta.output_tokens) == (100, 80, 320)


def test_gemini_retries_a_server_error_and_a_dropped_connection(gemini_api):
    client, sent = gemini_api(_gemini_error(503, "UNAVAILABLE"), gemini_api.hx.ConnectError("down"), _gemini_answer())
    out, _meta = _ask(client)
    assert out == {"answer": 1} and len(sent) == 3


def test_gemini_fails_a_bad_request_at_once(gemini_api):
    client, sent = gemini_api(_gemini_error(400, "INVALID_ARGUMENT"))
    with pytest.raises(LLMError, match="INVALID_ARGUMENT"):
        _ask(client)
    assert len(sent) == 1


def test_gemini_reply_cut_off_at_the_token_limit_fails(gemini_api):
    client, _sent = gemini_api(_gemini_answer(finish="MAX_TOKENS"))
    with pytest.raises(LLMError, match="output limit"):
        _ask(client)


def test_gemini_reply_without_the_call_names_the_reason(gemini_api):
    client, _sent = gemini_api(lambda _body: (200, {"candidates": [{"finishReason": "SAFETY"}]}))
    with pytest.raises(LLMError, match="SAFETY"):
        _ask(client)


def test_gemini_blocked_prompt_fails_with_the_reason(gemini_api):
    client, _sent = gemini_api(lambda _body: (200, {"promptFeedback": {"blockReason": "PROHIBITED_CONTENT"}}))
    with pytest.raises(LLMError, match="PROHIBITED_CONTENT"):
        _ask(client)
