from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import create_autospec

import httpx
import pytest
from pydantic import ValidationError

from capitalcite.ask.endpoint import (
    chat_endpoint_for,
    host_class_for,
    normalize_chat_base_url,
    parse_chat_endpoint,
    post_chat_completion,
)
from capitalcite.ask.types import ModelNotConfigured, ModelUnavailable
from capitalcite.config import Settings
from capitalcite.retrieval.embedders import OpenAIEmbedder, spec_for
from capitalcite.retrieval.service import RetrievalSetup
from capitalcite.retrieval.types import EmbedderName
from capitalcite.trace import MemoryExporter, configure_for_tests, generation_attrs

_ORIGINAL_ASYNC = httpx.AsyncHTTPTransport.handle_async_request
_ORIGINAL_SYNC = httpx.HTTPTransport.handle_request
_SEC = "capitalcite-tests tests@example.com"


def _settings(**env: str) -> Settings:
    return Settings.model_validate({"sec_user_agent": _SEC, **env})


def test_blank_base_url_is_hosted_default() -> None:
    assert normalize_chat_base_url(None) == "https://api.openai.com/v1"
    assert normalize_chat_base_url("  ") == "https://api.openai.com/v1"
    endpoint = parse_chat_endpoint(None, "sk-test", "gpt-4o-mini")
    assert endpoint.label.provider == "hosted"
    assert endpoint.label.host_class == "openai"
    assert endpoint.label.billed is True
    assert endpoint.label.model_id == "gpt-4o-mini"
    assert endpoint.headers()["Authorization"] == "Bearer sk-test"
    assert "sk-test" not in repr(endpoint)
    assert "://" not in repr(endpoint)


def test_loopback_names_are_localhost_and_drop_the_key() -> None:
    for base in (
        "http://127.0.0.1:11434/v1",
        "http://[::1]:11434/v1",
        "http://localhost:11434/v1/",
        "HTTP://LocalHost.:11434/v1",
    ):
        endpoint = parse_chat_endpoint(base, "sk-hosted-secret", "lfm3:latest")
        assert endpoint.label.provider == "local"
        assert endpoint.label.host_class == "localhost"
        assert endpoint.label.billed is False
        assert endpoint.headers() == {}
        assert "sk-hosted-secret" not in repr(endpoint)
    bare = normalize_chat_base_url("http://127.0.0.1:11434/v1/")
    assert bare == "http://127.0.0.1:11434/v1"
    assert normalize_chat_base_url(bare) == bare


def test_other_host_is_compatible_and_unbilled() -> None:
    for base in (
        "https://example.com/v1",
        "http://0.0.0.0:11434/v1",
        "https://openrouter.ai/api/v1",
    ):
        endpoint = parse_chat_endpoint(base, "sk-secret", "lfm3:latest")
        assert endpoint.label.provider == "compatible"
        assert endpoint.label.host_class == "other"
        assert endpoint.label.billed is False
        assert endpoint.headers() == {}
        assert host_class_for(base) == "other"
    assert (
        normalize_chat_base_url("https://openrouter.ai/api/v1/") == "https://openrouter.ai/api/v1"
    )


def test_hosted_blank_key_uses_the_existing_message() -> None:
    with pytest.raises(ModelNotConfigured, match="ASK_MODEL=openai requires OPENAI_API_KEY"):
        parse_chat_endpoint(None, "  ", "gpt-4o-mini")
    local = parse_chat_endpoint("http://127.0.0.1:11434/v1", None, "lfm3:latest")
    assert local.headers() == {}


def test_userinfo_query_and_fragment_are_rejected_without_the_url() -> None:
    secret = "super-secret-token"
    samples = (
        f"http://user:{secret}@127.0.0.1:11434/v1",
        f"http://127.0.0.1:11434/v1?token={secret}",
        f"http://127.0.0.1:11434/v1#{secret}",
        "http://127.0.0.1:11434/v1/chat/completions",
        "http://api.openai.com/v1",
        "https://api.openai.com:8443/v1",
        "http://127.0.0.1:11434",
    )
    for sample in samples:
        with pytest.raises(ValueError) as caught:
            normalize_chat_base_url(sample)
        text = str(caught.value)
        assert secret not in text
        assert "://" not in text
        assert "11434" not in text
        assert "8443" not in text


def test_api_openai_empty_path_normalizes_to_v1() -> None:
    assert normalize_chat_base_url("https://API.OpenAI.com") == "https://api.openai.com/v1"
    assert normalize_chat_base_url("https://api.openai.com/v1") == "https://api.openai.com/v1"
    with pytest.raises(ValueError, match="must be /v1"):
        normalize_chat_base_url("https://api.openai.com/api/v1")


def test_settings_rejects_a_bad_base_url_at_load(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEC_USER_AGENT", _SEC)
    monkeypatch.setenv("ASK_OPENAI_BASE_URL", "  ")
    assert Settings().ask_openai_base_url is None
    monkeypatch.setenv("ASK_OPENAI_BASE_URL", "http://user:secret@127.0.0.1:9/v1?x=1")
    with pytest.raises(ValidationError) as caught:
        Settings()
    text = str(caught.value)
    assert "secret" not in text
    assert "127.0.0.1" not in text
    monkeypatch.setenv("ASK_OPENAI_BASE_URL", "http://127.0.0.1:11434/v1/")
    settings = Settings()
    assert settings.ask_openai_base_url == "http://127.0.0.1:11434/v1"


def test_fake_selection_ignores_url_and_key() -> None:
    settings = _settings(
        ask_model="openai",
        ask_openai_base_url="http://127.0.0.1:11434/v1",
        openai_api_key="sk-secret",
        ask_openai_model="lfm3:latest",
    )
    assert chat_endpoint_for(settings, "fake") is None
    endpoint = chat_endpoint_for(settings, "openai")
    assert endpoint is not None
    assert endpoint.label.host_class == "localhost"
    assert endpoint.headers() == {}


def _completion(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 4, "completion_tokens": 2},
        },
    )


async def test_hosted_post_uses_bearer_and_the_openai_url() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _completion("{}")

    endpoint = parse_chat_endpoint(None, "sk-test", "gpt-4o-mini")
    exporter = MemoryExporter()
    configure_for_tests(exporter)
    text = await post_chat_completion(
        endpoint,
        [{"role": "user", "content": "hi"}],
        seed=20261103,
        temperature=0.0,
        transport=httpx.MockTransport(handler),
    )
    assert text == "{}"
    assert str(seen[0].url) == "https://api.openai.com/v1/chat/completions"
    assert seen[0].headers["authorization"] == "Bearer sk-test"
    body = json.loads(seen[0].content)
    assert body["model"] == "gpt-4o-mini"
    assert body["seed"] == 20261103
    assert body["temperature"] == 0.0
    assert body["response_format"] == {"type": "json_object"}
    span = exporter.finished()[-1]
    assert span.attributes["gen_ai.provider.name"] == "openai"
    assert span.attributes["gen_ai.usage.input_tokens"] == 4
    assert "capitalcite.cost_usd" in span.attributes


async def test_hosted_span_matches_main_generation_attrs() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        _ = request
        return _completion("{}")

    endpoint = parse_chat_endpoint(None, "sk-test", "gpt-4o-mini")
    exporter = MemoryExporter()
    configure_for_tests(exporter)
    await post_chat_completion(
        endpoint,
        [{"role": "user", "content": "hi"}],
        seed=None,
        temperature=None,
        transport=httpx.MockTransport(handler),
    )
    span = exporter.finished()[-1]
    expected = generation_attrs(model="gpt-4o-mini", input_tokens=4, output_tokens=2)
    assert expected["gen_ai.provider.name"] == "openai"
    for key, value in expected.items():
        assert span.attributes[key] == value


async def test_local_post_omits_authorization_even_when_a_key_is_set() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _completion("{}")

    endpoint = parse_chat_endpoint(
        "http://127.0.0.1:11434/v1",
        "sk-hosted-secret",
        "lfm3:latest",
    )
    exporter = MemoryExporter()
    configure_for_tests(exporter)
    await post_chat_completion(
        endpoint,
        [{"role": "user", "content": "hi"}],
        seed=None,
        temperature=None,
        transport=httpx.MockTransport(handler),
    )
    assert str(seen[0].url) == "http://127.0.0.1:11434/v1/chat/completions"
    assert "authorization" not in {name.lower() for name in seen[0].headers}
    body = json.loads(seen[0].content)
    assert body["model"] == "lfm3:latest"
    assert "seed" not in body
    assert "temperature" not in body
    span = exporter.finished()[-1]
    assert span.attributes["gen_ai.provider.name"] == "local"
    assert "capitalcite.cost_usd" not in span.attributes


def _restore_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _ORIGINAL_ASYNC)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _ORIGINAL_SYNC)


async def test_closed_port_names_host_class_and_returns(monkeypatch: pytest.MonkeyPatch) -> None:
    _restore_sockets(monkeypatch)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    endpoint = parse_chat_endpoint(
        f"http://127.0.0.1:{port}/v1",
        "sk-secret",
        "lfm3:latest",
        timeout_s=0.5,
    )
    started = time.monotonic()
    with pytest.raises(ModelUnavailable) as caught:
        await post_chat_completion(
            endpoint,
            [{"role": "user", "content": "hi"}],
            seed=None,
            temperature=None,
        )
    message = str(caught.value)
    assert "connection failed" in message
    assert "host class localhost" in message
    assert "://" not in message
    assert str(port) not in message
    assert "sk-secret" not in message
    assert time.monotonic() - started < 5


class _Slow(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        _ = self.rfile.read(length)
        time.sleep(2)
        self.send_response(200)
        self.end_headers()

    def log_message(self, template: str, *args: object) -> None:
        _ = template, args


async def test_timeout_names_host_class_and_returns(monkeypatch: pytest.MonkeyPatch) -> None:
    _restore_sockets(monkeypatch)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Slow)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        endpoint = parse_chat_endpoint(
            f"http://127.0.0.1:{port}/v1",
            None,
            "lfm3:latest",
            timeout_s=0.5,
        )
        started = time.monotonic()
        with pytest.raises(ModelUnavailable) as caught:
            await post_chat_completion(
                endpoint,
                [{"role": "user", "content": "hi"}],
                seed=None,
                temperature=None,
            )
        message = str(caught.value)
        assert "timed out" in message
        assert "host class localhost" in message
        assert "://" not in message
        assert str(port) not in message
        assert time.monotonic() - started < 5
    finally:
        server.shutdown()
        thread.join(timeout=2)


async def test_embeddings_stay_on_openai_when_chat_base_is_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    class _Client:
        def __init__(self, *args: object, **kwargs: object) -> None:
            _ = args, kwargs

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *args: object) -> bool:
            _ = args
            return False

        async def post(
            self,
            url: str,
            headers: dict[str, str] | None = None,
            json: object | None = None,
        ) -> httpx.Response:
            _ = json
            seen.append(url)
            assert headers is not None
            assert headers["Authorization"] == "Bearer sk-embed"
            return httpx.Response(
                200,
                json={"data": [{"index": 0, "embedding": [0.0] * 1536}]},
            )

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    monkeypatch.setenv("ASK_OPENAI_BASE_URL", "http://127.0.0.1:11434/v1")
    embedder = OpenAIEmbedder(spec_for(EmbedderName.OPENAI_SMALL), "sk-embed")
    await embedder._embed(["tier one capital"])
    assert seen == ["https://api.openai.com/v1/embeddings"]


async def test_check_live_ignores_ask_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    from capitalcite.eval.check import _live
    from capitalcite.golden.models import Evidence, GoldenItem, ItemSource, QuestionType

    monkeypatch.setenv("OPENAI_API_KEY", "sk-live")
    monkeypatch.setenv("ASK_OPENAI_BASE_URL", "http://127.0.0.1:9/v1")
    seen: dict[str, object] = {}

    def build(name: str, *, endpoint: object, **kwargs: object) -> object:
        _ = name, kwargs
        from capitalcite.ask.endpoint import ChatEndpoint

        assert isinstance(endpoint, ChatEndpoint)
        seen["host"] = endpoint.label.host_class
        seen["auth"] = endpoint.headers().get("Authorization")
        from capitalcite.ask.models import FakeStructuredModel

        return FakeStructuredModel()

    async def retrieve(*args: object, **kwargs: object) -> list[object]:
        _ = args, kwargs
        return []

    async def ask(*args: object, **kwargs: object) -> object:
        _ = args, kwargs
        from capitalcite.ask.types import Refused

        return Refused(refusal_reason="unused")

    monkeypatch.setattr("capitalcite.eval.check.build_structured_model", build)
    monkeypatch.setattr("capitalcite.eval.check.retrieve", retrieve)
    monkeypatch.setattr("capitalcite.eval.check.ask", ask)
    item = GoldenItem(
        id="cc-ci-002",
        question="What was CET1?",
        answer="15.7%",
        type=QuestionType.EXTRACTIVE,
        answerable=True,
        source=ItemSource.HANDWRITTEN,
        evidence=[Evidence(doc_id="doc", section="Item 1", quote="15.7%")],
    )
    setup = create_autospec(RetrievalSetup, instance=True)
    await _live(True, setup, (item,))
    assert seen["host"] == "openai"
    assert seen["auth"] == "Bearer sk-live"


def test_settings_timeout_is_positive() -> None:
    settings = _settings()
    assert settings.ask_openai_timeout_s == 30.0
    endpoint = chat_endpoint_for(
        _settings(
            ask_openai_base_url="http://127.0.0.1:11434/v1",
            ask_openai_timeout_s="12.5",
        ),
        "openai",
    )
    assert endpoint is not None
    assert endpoint.timeout_s == 12.5
    with pytest.raises(ValidationError):
        _settings(ask_openai_timeout_s="0")
    with pytest.raises(ValueError, match="timeout"):
        parse_chat_endpoint(None, "sk-test", "gpt-4o-mini", timeout_s=0)
