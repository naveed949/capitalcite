"""StructuredModel adapters. Fake quotes a hit. OpenAI speaks JSON over httpx."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Protocol

import httpx
from pydantic import ValidationError

from capitalcite.ask.types import (
    AskModelName,
    DraftCite,
    ModelDraft,
    ModelNotConfigured,
    ModelUnavailable,
)
from capitalcite.retrieval.types import Hit

_CHAT_URL = "https://api.openai.com/v1/chat/completions"


class StructuredModel(Protocol):
    """Question and hits in, a ModelDraft out. No Qdrant types and no HTTP types."""

    async def complete(self, question: str, hits: Sequence[Hit]) -> ModelDraft:
        """Return one draft. Schema failure is the adapter's problem, not the caller's."""
        ...


class FakeStructuredModel:
    """Quotes hits[0] unless a scripted draft is installed. No network."""

    def __init__(self, script: ModelDraft | None = None) -> None:
        self._script = script
        self.calls = 0

    async def complete(self, question: str, hits: Sequence[Hit]) -> ModelDraft:
        self.calls += 1
        if self._script is not None:
            return self._script
        if not hits:
            return ModelDraft(kind="refused", refusal_reason="no passages were retrieved")
        quote = hits[0].chunk.text
        return ModelDraft(
            kind="answered",
            answer=quote,
            confidence=1.0,
            citations=(DraftCite(index=0, quote=quote),),
        )


class OpenAIStructuredModel:
    """Chat completions with json_object. One validation retry, then a refusal draft."""

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._transport = transport

    def __repr__(self) -> str:
        return f"OpenAIStructuredModel(model={self._model!r})"

    async def complete(self, question: str, hits: Sequence[Hit]) -> ModelDraft:
        messages: list[dict[str, str]] = [
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": _user_prompt(question, hits)},
        ]
        failure: str | None = None
        for _ in range(2):
            if failure is not None:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "The previous JSON failed validation: "
                            f"{failure}. Return one JSON object that matches the schema."
                        ),
                    }
                )
            content: str | None = None
            try:
                content = await self._post(messages)
                return _draft_from_content(content)
            except ValueError as exc:
                failure = str(exc)
                if content is not None:
                    messages.append({"role": "assistant", "content": content})
        return ModelDraft(
            kind="refused",
            refusal_reason="the model did not return a valid answer",
        )

    async def _post(self, messages: list[dict[str, str]]) -> str:
        try:
            async with httpx.AsyncClient(timeout=30.0, transport=self._transport) as client:
                response = await client.post(
                    _CHAT_URL,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json={
                        "model": self._model,
                        "response_format": {"type": "json_object"},
                        "messages": messages,
                    },
                )
        except httpx.HTTPError as exc:
            raise ModelUnavailable("openai chat completion request failed") from exc
        if response.status_code != 200:
            raise ModelUnavailable(f"openai chat completion HTTP {response.status_code}")
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise ModelUnavailable("openai chat completion response was not JSON") from exc
        return _message_content(payload)


def build_structured_model(
    name: AskModelName,
    *,
    api_key: str | None,
    openai_model: str,
) -> StructuredModel:
    """fake never reads the key. openai without a key raises ModelNotConfigured."""
    if name == "fake":
        return FakeStructuredModel()
    key = (api_key or "").strip()
    if not key:
        raise ModelNotConfigured("ASK_MODEL=openai requires OPENAI_API_KEY")
    return OpenAIStructuredModel(key, openai_model)


def _system_prompt() -> str:
    # response_format json_object requires the messages to contain the word JSON.
    schema = json.dumps(ModelDraft.model_json_schema())
    return (
        "You answer questions about U.S. bank filings using only the numbered passages. "
        "Return one JSON object. "
        "kind is answered or refused. "
        "When kind is answered, answer is non-empty and citations contains at least one "
        "object with index and quote. index is the passage number. "
        "quote must be a verbatim substring of that passage. "
        "When the passages do not support an answer, set kind to refused, "
        "set refusal_reason, and set citations to an empty array. "
        "confidence is a number from 0 to 1. "
        f"JSON schema: {schema}"
    )


def _user_prompt(question: str, hits: Sequence[Hit]) -> str:
    blocks: list[str] = []
    for index, hit in enumerate(hits):
        chunk = hit.chunk
        blocks.append(f"[{index}] doc_id={chunk.doc_id} section={chunk.section}\n{chunk.text}")
    return f"Question: {question}\n\nPassages:\n" + "\n\n".join(blocks)


def _message_content(payload: object) -> str:
    if not isinstance(payload, dict):
        raise ModelUnavailable("openai chat completion response was not an object")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ModelUnavailable("openai chat completion response missing choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise ModelUnavailable("openai chat completion choice was not an object")
    message = first.get("message")
    if not isinstance(message, dict):
        raise ModelUnavailable("openai chat completion choice missing message")
    content = message.get("content")
    if not isinstance(content, str):
        raise ValueError("openai chat completion content was not a string")
    return content


def _draft_from_content(content: str) -> ModelDraft:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("model content was not JSON") from exc
    try:
        return ModelDraft.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc
