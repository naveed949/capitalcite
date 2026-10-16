from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import get_args

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from qdrant_client import AsyncQdrantClient

from capitalcite.api.app import create_app
from capitalcite.api.routes.ask import AskResponse
from capitalcite.api.routes.search import get_setup
from capitalcite.ask import (
    Answered,
    DraftCite,
    FakeStructuredModel,
    ModelDraft,
    ModelNotConfigured,
    ModelUnavailable,
    OpenAIStructuredModel,
    Refused,
    ask,
    build_structured_model,
    retrieval_refusal,
)
from capitalcite.ask.types import AskModelName
from capitalcite.chunking.models import Chunk, ChunkerName
from capitalcite.config import Settings, get_settings
from capitalcite.retrieval import (
    FakeEmbedder,
    Hit,
    RetrievalSetup,
    build_index,
    collection_name,
    config_hash,
    setup_retrieval,
)

_JPM_URL = "https://www.sec.gov/Archives/edgar/data/19617/jpm.htm"
_CHAT_URL = "https://api.openai.com/v1/chat/completions"


async def test_ask_empty_hits_does_not_call_the_model() -> None:
    model = FakeStructuredModel()
    answer = await ask([], "What was the CET1 ratio?", model)
    assert isinstance(answer, Refused)
    assert answer.refusal_reason == "no passages were retrieved"
    assert model.calls == 0


async def test_ask_low_hit_score_does_not_call_the_model() -> None:
    model = FakeStructuredModel()
    hit = _hit(score=0.01)
    answer = await ask([hit], "What buffer did JPM describe?", model, min_score=0.5)
    assert isinstance(answer, Refused)
    assert answer.refusal_reason == "top hit score is below the retrieval floor"
    assert model.calls == 0


async def test_ask_ticker_mismatch_does_not_call_the_model() -> None:
    model = FakeStructuredModel()
    answer = await ask([_hit()], "What CET1 ratio did Wells Fargo report?", model)
    assert isinstance(answer, Refused)
    assert "entity" in answer.refusal_reason
    assert model.calls == 0


async def test_ask_year_mismatch_does_not_call_the_model() -> None:
    model = FakeStructuredModel()
    answer = await ask(
        [_hit(ticker="WFC", period="2025-12-31")],
        "What CET1 ratio did Wells Fargo disclose for year-end 2026?",
        model,
    )
    assert isinstance(answer, Refused)
    assert "entity" in answer.refusal_reason
    assert model.calls == 0


async def test_ask_matching_bank_and_year_calls_the_model() -> None:
    model = FakeStructuredModel()
    hit = _hit(period="2024-12-31")
    answer = await ask([hit], "What buffer did JPMorgan describe at year-end 2024?", model)
    assert isinstance(answer, Answered)
    assert model.calls == 1


async def test_ask_default_fake_quotes_the_first_hit() -> None:
    model = FakeStructuredModel()
    hit = _hit()
    answer = await ask([hit], "What buffer did JPM describe?", model)
    assert isinstance(answer, Answered)
    assert model.calls == 1
    assert answer.citations[0].quote == hit.chunk.text
    assert answer.citations[0].quote in hit.chunk.text
    assert answer.citations[0].doc_id == hit.chunk.doc_id
    assert answer.citations[0].start == hit.chunk.start
    assert answer.citations[0].end == hit.chunk.end


async def test_ask_refuses_a_hallucinated_quote() -> None:
    script = ModelDraft(
        kind="answered",
        answer="The ratio was 15.7%.",
        citations=(DraftCite(index=1, quote="15.7%"),),
    )
    model = FakeStructuredModel(script)
    answer = await ask([_hit()], "What was CET1?", model)
    assert isinstance(answer, Refused)
    assert model.calls == 1


async def test_ask_refuses_when_any_citation_is_ungrounded() -> None:
    script = ModelDraft(
        kind="answered",
        answer="Both passages.",
        citations=(
            DraftCite(index=1, quote="tier one capital"),
            DraftCite(index=2, quote="not in the second passage"),
        ),
    )
    hits = [_hit(), _hit(doc_id="doc-bac", text="wholesale funding markets")]
    answer = await ask(hits, "Compare the two.", FakeStructuredModel(script))
    assert isinstance(answer, Refused)


async def test_ask_refuses_an_invalid_scripted_draft() -> None:
    script = ModelDraft.model_construct(
        kind="answered",
        answer="The ratio was 15.7%.",
        refusal_reason="",
        citations=(),
    )
    answer = await ask([_hit()], "What was CET1?", FakeStructuredModel(script))
    assert isinstance(answer, Refused)


def test_fake_model_does_not_read_a_key() -> None:
    model = build_structured_model("fake", api_key=None, openai_model="gpt-4o-mini")
    assert isinstance(model, FakeStructuredModel)
    with pytest.raises(ModelNotConfigured, match="OPENAI_API_KEY"):
        build_structured_model("openai", api_key="  ", openai_model="gpt-4o-mini")


def test_ask_model_names_match_settings() -> None:
    allowed = set(get_args(Settings.model_fields["ask_model"].annotation))
    assert allowed == set(get_args(AskModelName))


def test_openai_repr_hides_the_key() -> None:
    model = OpenAIStructuredModel("test-key", "gpt-4o-mini")
    assert "test-key" not in repr(model)


async def test_openai_retries_invalid_json_once() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if len(seen) == 1:
            content = json.dumps({"kind": "answered"})
        else:
            content = json.dumps(
                {
                    "kind": "answered",
                    "answer": "tier one capital buffer",
                    "citations": [{"index": 1, "quote": "tier one capital"}],
                }
            )
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    model = OpenAIStructuredModel("test-key", "gpt-4o-mini", transport=httpx.MockTransport(handler))
    draft = await model.complete("What buffer is described?", [_hit()])
    assert draft.kind == "answered"
    assert draft.citations[0].quote == "tier one capital"
    assert len(seen) == 2
    second = json.loads(seen[1].content)
    blob = json.dumps(second["messages"])
    assert "non-empty answer" in blob
    assert "tier one capital buffer" in blob
    assert "<passage n=" in blob
    assert "untrusted" in blob
    assert "JSON" in blob
    assert "doc_id=" not in blob
    assert second["response_format"] == {"type": "json_object"}
    assert second["model"] == "gpt-4o-mini"
    assert seen[1].headers["authorization"] == "Bearer test-key"
    assert str(seen[1].url) == _CHAT_URL


async def test_openai_refuses_after_two_invalid_payloads() -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": "{"}}]})

    model = OpenAIStructuredModel("test-key", "gpt-4o-mini", transport=httpx.MockTransport(handler))
    draft = await model.complete("What buffer is described?", [_hit()])
    assert calls["n"] == 2
    assert isinstance(draft, ModelDraft)
    assert draft.kind == "refused"
    assert draft.citations == ()


async def test_openai_http_error_does_not_retry() -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(502, json={"error": "down"})

    model = OpenAIStructuredModel("test-key", "gpt-4o-mini", transport=httpx.MockTransport(handler))
    with pytest.raises(ModelUnavailable, match="HTTP 502"):
        await model.complete("What buffer is described?", [_hit()])
    assert calls["n"] == 1


async def test_grounding_failure_does_not_retry_openai() -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        content = json.dumps(
            {
                "kind": "answered",
                "answer": "The ratio was 15.7%.",
                "citations": [{"index": 1, "quote": "15.7%"}],
            }
        )
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    model = OpenAIStructuredModel("test-key", "gpt-4o-mini", transport=httpx.MockTransport(handler))
    answer = await ask([_hit()], "What was CET1?", model)
    assert isinstance(answer, Refused)
    assert calls["n"] == 1


async def test_openai_without_transport_is_blocked() -> None:
    model = OpenAIStructuredModel("test-key", "gpt-4o-mini")
    with pytest.raises(RuntimeError, match="live network is forbidden"):
        await model.complete("What buffer is described?", [_hit()])


def test_post_ask_answered_quotes_the_indexed_chunk(tmp_path: Path) -> None:
    chunk = _chunk()
    application, setup = _indexed_app(tmp_path, [chunk])
    with TestClient(application) as http:
        response = http.post(
            "/ask",
            json={"question": "tier one capital", "ticker": "JPM", "model": "fake"},
        )
    assert response.status_code == 200
    body = AskResponse.model_validate(response.json())
    assert body.kind == "answered"
    assert body.answer == chunk.text
    assert body.refusal_reason is None
    assert body.point_count == 1
    assert body.config_hash == config_hash(setup.config)
    assert body.collection_name == collection_name(setup.config)
    assert len(body.citations) == 1
    cite = body.citations[0]
    assert cite.doc_id == chunk.doc_id
    assert cite.start == chunk.start
    assert cite.end == chunk.end
    assert cite.section == chunk.section
    assert cite.source_url == chunk.source_url
    assert cite.quote in chunk.text
    assert cite.quote_start == chunk.start
    assert cite.quote_end == chunk.start + len(cite.quote)
    assert body.retrieval_score > 0


def test_post_ask_scripted_refusal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    application, setup = _indexed_app(tmp_path, [_chunk()])
    _script(
        monkeypatch,
        ModelDraft(kind="refused", refusal_reason="not in the filing"),
    )
    with TestClient(application) as http:
        response = http.post("/ask", json={"question": "What was CET1?", "model": "fake"})
    assert response.status_code == 200
    body = AskResponse.model_validate(response.json())
    assert body.kind == "refused"
    assert body.answer is None
    assert body.citations == []
    assert body.refusal_reason == "not in the filing"
    assert body.retrieval_score == 0.0
    assert body.point_count == 1
    assert body.config_hash == config_hash(setup.config)


def test_post_ask_hallucinated_quote_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application, _setup = _indexed_app(tmp_path, [_chunk()])
    _script(
        monkeypatch,
        ModelDraft(
            kind="answered",
            answer="The ratio was 15.7%.",
            citations=(DraftCite(index=1, quote="15.7%"),),
        ),
    )
    with TestClient(application) as http:
        response = http.post("/ask", json={"question": "What was CET1?", "model": "fake"})
    assert response.status_code == 200
    body = AskResponse.model_validate(response.json())
    assert body.kind == "refused"
    assert body.answer is None
    assert body.citations == []
    assert body.refusal_reason
    assert "15.7%" not in body.refusal_reason


def test_post_ask_openai_without_key_is_503(tmp_path: Path) -> None:
    application, _setup = _indexed_app(tmp_path, [_chunk()])
    application.dependency_overrides[get_settings] = lambda: _settings(ask_model="openai")
    with TestClient(application) as http:
        response = http.post("/ask", json={"question": "tier one capital", "model": "openai"})
    assert response.status_code == 503
    assert "OPENAI_API_KEY" in response.json()["detail"]


def test_post_ask_empty_index_refuses_without_calling_openai(tmp_path: Path) -> None:
    application, setup = _indexed_app(tmp_path, [])
    application.dependency_overrides[get_settings] = lambda: _settings(ask_model="openai")
    with TestClient(application) as http:
        response = http.post("/ask", json={"question": "cet1", "model": "openai"})
    assert response.status_code == 200
    body = AskResponse.model_validate(response.json())
    assert body.kind == "refused"
    assert body.citations == []
    assert body.answer is None
    assert body.point_count == 0
    assert body.config_hash == config_hash(setup.config)


def test_post_ask_rejects_empty_question_and_bad_period(tmp_path: Path) -> None:
    application, _setup = _indexed_app(tmp_path, [_chunk()])
    with TestClient(application) as http:
        empty = http.post("/ask", json={"question": ""})
        blank = http.post("/ask", json={"question": "   ", "model": "fake"})
        period = http.post("/ask", json={"question": "cet1", "period": "FY2024", "model": "fake"})
    assert empty.status_code == 422
    assert blank.status_code == 422
    assert period.status_code == 422
    assert period.json()["detail"] == "period must be YYYY-MM-DD or a 4-digit year"


def test_post_ask_503_when_qdrant_is_down() -> None:
    client = AsyncQdrantClient(url="http://127.0.0.1:1", timeout=1, check_compatibility=False)
    setup = setup_retrieval(_settings(), client=client, embedder=FakeEmbedder())
    application = create_app()
    application.dependency_overrides[get_setup] = lambda: setup
    with TestClient(application) as http:
        response = http.post("/ask", json={"question": "cet1", "model": "fake"})
    assert response.status_code == 503


def test_ask_domain_does_not_import_frameworks() -> None:
    banned = ("fastapi", "qdrant_client", "fastembed", "pydantic_ai", "pydantic-ai")
    root = Path("src/capitalcite/ask")
    for path in sorted(root.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for needle in banned:
            assert needle not in text, f"{path} mentions {needle}"


def test_project_does_not_depend_on_pydantic_ai() -> None:
    text = Path("pyproject.toml").read_text(encoding="utf-8")
    assert "pydantic-ai" not in text
    assert "pydantic_ai" not in text


def _script(monkeypatch: pytest.MonkeyPatch, script: ModelDraft) -> None:
    monkeypatch.setattr(
        "capitalcite.api.routes.ask.build_structured_model",
        lambda *_args, **_kwargs: FakeStructuredModel(script),
    )


def _indexed_app(tmp_path: Path, chunks: list[Chunk]) -> tuple[FastAPI, RetrievalSetup]:
    _write_chunks(tmp_path, chunks)
    client = AsyncQdrantClient(":memory:")
    setup = setup_retrieval(_settings(), client=client, embedder=FakeEmbedder())
    asyncio.run(build_index(setup, tmp_path))
    application = create_app()
    application.dependency_overrides[get_setup] = lambda: setup
    application.dependency_overrides[get_settings] = lambda: _settings()
    return application, setup


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "sec_user_agent": "capitalcite-tests tests@example.com",
        "chunker": ChunkerName.SECTION,
        "chunk_size": 32,
        "chunk_overlap": 4,
        "qdrant_collection": "filings",
        "embedder": "bge-small",
        "search_k": 8,
        "rrf_k": 60,
        "openai_api_key": None,
        "ask_model": "fake",
        "ask_openai_model": "gpt-4o-mini",
        "ask_min_hit_score": 0.0,
    }
    values.update(overrides)
    return Settings.model_validate(values)


def _chunk() -> Chunk:
    return Chunk(
        doc_id="doc-jpm",
        section="Item 15",
        start=10,
        end=40,
        offset=10,
        source_url=_JPM_URL,
        text="tier one capital buffer",
        token_count=4,
        chunker=ChunkerName.SECTION,
        ticker="JPM",
        form="10-K",
        period="2023-12-31",
        accession="doc-jpm",
    )


def _hit(
    *,
    doc_id: str = "doc-jpm",
    text: str = "tier one capital buffer",
    ticker: str = "JPM",
    period: str = "2023-12-31",
    score: float = 1.0,
) -> Hit:
    return Hit(
        score=score,
        chunk=_chunk().model_copy(
            update={"doc_id": doc_id, "text": text, "ticker": ticker, "period": period}
        ),
    )


def test_retrieval_refusal_allows_matching_hits() -> None:
    assert retrieval_refusal([_hit()], "What buffer did JPM describe?", min_score=0.0) is None


def _write_chunks(root: Path, chunks: list[Chunk]) -> None:
    if not chunks:
        return
    folder = root / "parsed" / "edgar" / "JPM" / "10-K" / "2023-12-31" / "acc"
    folder.mkdir(parents=True)
    lines = [chunk.model_dump_json() for chunk in chunks]
    (folder / "chunks.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
