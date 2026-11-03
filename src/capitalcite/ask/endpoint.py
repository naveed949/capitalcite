from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal, Protocol
from urllib.parse import SplitResult, urlsplit, urlunsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from capitalcite.ask.types import AskModelName, ModelNotConfigured, ModelUnavailable
from capitalcite.trace import ObservationKind, chat_span_name, generation_attrs, observation

HostClass = Literal["localhost", "openai", "other"]
ProviderName = Literal["local", "hosted", "compatible"]

_LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})
_HOSTED_BASE = "https://api.openai.com/v1"


class RunLabel(BaseModel):
    """Safe to put on a span or in a results file. No URL, no secret, no port."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: ProviderName
    host_class: HostClass
    model_id: str = Field(min_length=1)
    billed: bool

    @model_validator(mode="after")
    def policy_matches_class(self) -> RunLabel:
        if self.host_class == "localhost" and self.provider == "local" and not self.billed:
            return self
        if self.host_class == "openai" and self.provider == "hosted" and self.billed:
            return self
        if self.host_class == "other" and self.provider == "compatible" and not self.billed:
            return self
        raise ValueError("run label does not match host class")


@dataclass(frozen=True, slots=True, repr=False)
class ChatEndpoint:
    _completions_url: str
    _api_key: str | None
    label: RunLabel
    timeout_s: float = 30.0

    def headers(self) -> dict[str, str]:
        if self._api_key:
            return {"Authorization": f"Bearer {self._api_key}"}
        return {}

    def __repr__(self) -> str:
        auth = "yes" if self._api_key else "no"
        label = self.label
        return (
            f"ChatEndpoint(provider={label.provider}, host_class={label.host_class}, "
            f"model_id={label.model_id!r}, auth={auth})"
        )


class ChatConfig(Protocol):
    ask_openai_base_url: str | None
    openai_api_key: str | None
    ask_openai_model: str
    ask_openai_timeout_s: float


def normalize_chat_base_url(value: str | None) -> str:
    if value is None:
        return _HOSTED_BASE
    raw = value.strip()
    if not raw:
        return _HOSTED_BASE
    parts = urlsplit(raw)
    scheme = parts.scheme.casefold()
    if scheme not in {"http", "https"}:
        raise ValueError("chat base URL must be http or https")
    if parts.username is not None or parts.password is not None:
        raise ValueError("chat base URL rejects userinfo")
    if parts.query:
        raise ValueError("chat base URL rejects a query")
    if parts.fragment:
        raise ValueError("chat base URL rejects a fragment")
    host = _hostname(parts)
    if not host:
        raise ValueError("chat base URL needs a host")
    path = _path(parts.path)
    if host == "api.openai.com":
        if scheme != "https":
            raise ValueError("api.openai.com requires https")
        if parts.port not in {None, 443}:
            raise ValueError("api.openai.com does not take a port")
        if path not in {"", "/v1"}:
            raise ValueError("chat base URL path must be /v1")
        path = "/v1"
    elif not path.endswith("/v1"):
        raise ValueError("chat base URL path must end in /v1")
    port = parts.port
    if (scheme == "https" and port == 443) or (scheme == "http" and port == 80):
        port = None
    return urlunsplit((scheme, _netloc(host, port), path, "", ""))


def host_class_for(base_url: str | None) -> HostClass:
    _provider, host_class, _billed = _classify(_parts(base_url))
    return host_class


def parse_chat_endpoint(
    base_url: str | None,
    api_key: str | None,
    model_id: str,
    *,
    timeout_s: float = 30.0,
) -> ChatEndpoint:
    """Config boundary for a live openai model. None base_url is hosted OpenAI."""
    if timeout_s <= 0:
        raise ValueError("chat timeout must be greater than 0")
    parts = _parts(base_url)
    provider, host_class, billed = _classify(parts)
    key = (api_key or "").strip() or None
    if billed:
        if key is None:
            raise ModelNotConfigured("ASK_MODEL=openai requires OPENAI_API_KEY")
        stored_key: str | None = key
    else:
        stored_key = None
    model = model_id.strip()
    if not model:
        raise ValueError("chat model id is empty")
    if "://" in model:
        raise ValueError("chat model id must not contain a URL")
    normalized = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    label = RunLabel(
        provider=provider,
        host_class=host_class,
        model_id=model,
        billed=billed,
    )
    return ChatEndpoint(
        _completions_url=f"{normalized}/chat/completions",
        _api_key=stored_key,
        label=label,
        timeout_s=timeout_s,
    )


def chat_endpoint_for(settings: ChatConfig, selected: AskModelName) -> ChatEndpoint | None:
    if selected == "fake":
        return None
    return parse_chat_endpoint(
        settings.ask_openai_base_url,
        settings.openai_api_key,
        settings.ask_openai_model,
        timeout_s=settings.ask_openai_timeout_s,
    )


async def post_chat_completion(
    endpoint: ChatEndpoint,
    messages: list[dict[str, str]],
    *,
    seed: int | None,
    temperature: float | None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    label = endpoint.label
    with observation(
        chat_span_name(label.model_id),
        ObservationKind.GENERATE,
        **generation_attrs(model=label.model_id, provider=_span_provider(label)),
    ) as span:
        try:
            async with httpx.AsyncClient(
                timeout=endpoint.timeout_s,
                transport=transport,
            ) as client:
                response = await client.post(
                    endpoint._completions_url,
                    headers=endpoint.headers(),
                    json=_chat_body(
                        label.model_id,
                        messages,
                        seed=seed,
                        temperature=temperature,
                    ),
                )
        except httpx.TimeoutException as exc:
            raise _unavailable(endpoint, "chat endpoint unreachable (timed out)") from exc
        except httpx.ConnectError as exc:
            raise _unavailable(endpoint, "chat endpoint unreachable (connection failed)") from exc
        except httpx.HTTPError as exc:
            raise _unavailable(endpoint, "chat completion request failed") from exc
        if response.status_code != 200:
            raise _unavailable(endpoint, f"chat completion HTTP {response.status_code}")
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise _unavailable(endpoint, "chat completion response was not JSON") from exc
        content = _message_content(endpoint, payload)
        input_tokens, output_tokens = _usage_tokens(payload)
        span.set(
            **generation_attrs(
                model=label.model_id,
                provider=_span_provider(label),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        )
        return content


def _unavailable(endpoint: ChatEndpoint, detail: str) -> ModelUnavailable:
    label = endpoint.label
    return ModelUnavailable(f"{label.provider} {detail} (host class {label.host_class})")


def _parts(base_url: str | None) -> SplitResult:
    return urlsplit(normalize_chat_base_url(base_url))


def _classify(parts: SplitResult) -> tuple[ProviderName, HostClass, bool]:
    host = _hostname(parts)
    if host == "api.openai.com":
        if parts.scheme.casefold() != "https":
            raise ValueError("api.openai.com requires https")
        return "hosted", "openai", True
    if host in _LOOPBACK:
        return "local", "localhost", False
    return "compatible", "other", False


def _span_provider(label: RunLabel) -> str:
    if label.host_class == "openai":
        return "openai"
    if label.host_class == "localhost":
        return "local"
    return "openai-compatible"


def _hostname(parts: SplitResult) -> str:
    host = parts.hostname or ""
    host = host.casefold()
    if host.endswith("."):
        host = host[:-1]
    return host


def _path(path: str) -> str:
    if path.endswith("/chat/completions"):
        raise ValueError("chat base URL must not end in /chat/completions")
    trimmed = path
    while trimmed.endswith("/") and trimmed != "/":
        trimmed = trimmed[:-1]
    if trimmed == "/":
        return ""
    return trimmed


def _netloc(host: str, port: int | None) -> str:
    token = f"[{host}]" if ":" in host else host
    if port is None:
        return token
    return f"{token}:{port}"


def _chat_body(
    model: str,
    messages: list[dict[str, str]],
    *,
    seed: int | None,
    temperature: float | None,
) -> dict[str, object]:
    body: dict[str, object] = {
        "model": model,
        "response_format": {"type": "json_object"},
        "messages": messages,
    }
    if seed is not None:
        body["seed"] = seed
    if temperature is not None:
        body["temperature"] = temperature
    return body


def _token_count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _usage_tokens(payload: object) -> tuple[int | None, int | None]:
    if not isinstance(payload, dict):
        return None, None
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None, None
    return _token_count(usage.get("prompt_tokens")), _token_count(usage.get("completion_tokens"))


def _message_content(endpoint: ChatEndpoint, payload: object) -> str:
    if not isinstance(payload, dict):
        raise _unavailable(endpoint, "chat completion response was not an object")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise _unavailable(endpoint, "chat completion response missing choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise _unavailable(endpoint, "chat completion choice was not an object")
    message = first.get("message")
    if not isinstance(message, dict):
        raise _unavailable(endpoint, "chat completion choice missing message")
    content = message.get("content")
    if not isinstance(content, str):
        raise ValueError("chat completion content was not a string")
    return content
