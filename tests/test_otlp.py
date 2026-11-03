from __future__ import annotations

import asyncio
import socket
import statistics
import threading
import time
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from qdrant_client import AsyncQdrantClient

from capitalcite.api.app import create_app
from capitalcite.api.routes.search import get_setup
from capitalcite.chunking.models import Chunk, ChunkerName
from capitalcite.config import Settings, get_settings
from capitalcite.retrieval import FakeEmbedder, build_index, setup_retrieval
from capitalcite.trace import (
    ObservationKind,
    ask_attrs,
    configure,
    current_trace_id,
    generation_attrs,
    observation,
    shutdown,
)
from capitalcite.trace.types import SpanRecord

_QUESTION = "What was the Standardized CET1 ratio?"
_QUOTE = "the Standardized CET1 ratio was 15.7 percent"


class _Capture(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        server = self.server
        assert isinstance(server, _Server)
        server.bodies.append((self.path, body))
        self.send_response(200)
        self.end_headers()

    def log_message(self, template: str, *args: object) -> None:
        _ = template, args
        return None


class _Slow(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        _ = self.rfile.read(length)
        time.sleep(2)
        self.send_response(200)
        self.end_headers()

    def log_message(self, template: str, *args: object) -> None:
        _ = template, args
        return None


class _Server(ThreadingHTTPServer):
    def __init__(self, handler: type[BaseHTTPRequestHandler]) -> None:
        self.bodies: list[tuple[str, bytes]] = []
        super().__init__(("127.0.0.1", 0), handler)


def test_otlp_http_exports_names_ids_and_gen_ai_attributes(tmp_path: Path) -> None:
    server = _Server(_Capture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        path = tmp_path / "spans.jsonl"
        configure(
            _settings(
                otel_exporter_otlp_endpoint=f"http://127.0.0.1:{port}",
                otel_service_name="capitalcite-test",
                capitalcite_trace_file=str(path),
            )
        )
        with observation("ask", ObservationKind.ASK, **ask_attrs(path="rag", question="cet1")):
            trace_id = current_trace_id()
            with observation(
                "generate",
                ObservationKind.GENERATE,
                **generation_attrs(model="fake"),
            ):
                pass
        assert trace_id
        shutdown()
        exported = _decode(server.bodies)
        by_name = {span["name"]: span for span in exported}
        assert set(by_name) == {"ask", "generate"}
        assert by_name["generate"]["parent"] == by_name["ask"]["span_id"]
        assert by_name["ask"]["trace_id"] == trace_id
        assert by_name["generate"]["trace_id"] == trace_id
        assert by_name["generate"]["attributes"]["gen_ai.request.model"] == "fake"
        assert by_name["generate"]["attributes"]["gen_ai.operation.name"] == "chat"
        assert by_name["generate"]["attributes"]["gen_ai.provider.name"] == "fake"
        assert by_name["ask"]["service_name"] == "capitalcite-test"
        file_records = [
            SpanRecord.model_validate_json(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        file_by_name = {record.name: record for record in file_records}
        assert file_by_name["ask"].span_id == by_name["ask"]["span_id"]
        assert file_by_name["generate"].span_id == by_name["generate"]["span_id"]
        assert file_by_name["ask"].trace_id == by_name["ask"]["trace_id"]
        assert all(posted == "/v1/traces" for posted, _body in server.bodies)
    finally:
        server.shutdown()
        server.server_close()


def test_streamed_agent_tree_reaches_otlp(tmp_path: Path) -> None:
    server = _Server(_Capture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        bac = Chunk(
            doc_id="doc-bac",
            section="Item 7",
            start=10,
            end=10 + len("CET1 capital ratio of 11.9 percent"),
            offset=10,
            source_url="https://www.sec.gov/Archives/edgar/data/70858/bac.htm",
            text="CET1 capital ratio of 11.9 percent under the Standardized approach",
            token_count=8,
            chunker=ChunkerName.SECTION,
            ticker="BAC",
            form="10-K",
            period="2024-12-31",
            accession="doc-bac",
        )
        application, _setup = _indexed_app(tmp_path, extra_chunks=(bac,))
        with TestClient(application) as http:
            configure(
                _settings(
                    otel_exporter_otlp_endpoint=f"http://127.0.0.1:{port}",
                    otel_service_name="capitalcite-test",
                )
            )
            response = http.post(
                "/ask/stream",
                json={
                    "question": (
                        "Which bank had the higher Standardized CET1 ratio at year-end 2024, "
                        "JPMorgan or Bank of America?"
                    ),
                    "path": "agent",
                    "model": "fake",
                    "k": 8,
                },
            )
            assert response.status_code == 200
            assert "event: done" in response.text
            shutdown()
        exported = _decode(server.bodies)
        by_id = {span["span_id"]: span for span in exported}
        parents = {
            name: {
                by_id[item["parent"]]["name"] if item["parent"] else None
                for item in exported
                if item["name"] == name
            }
            for name in {item["name"] for item in exported}
        }
        assert parents["ask"] == {None}
        assert parents["agent"] == {"ask"}
        assert parents["graph.plan"] == {"agent"}
        assert parents["graph.act"] == {"agent"}
        assert parents["graph.synthesize"] == {"agent"}
        assert parents["plan"] == {"graph.plan"}
        assert parents["complete"] == {"graph.synthesize"}
        assert parents["tool"] == {"graph.act"}
        assert parents["gate"] == {"graph.act"}
        assert parents["retrieve"] == {"tool"}
        if "graph.stop" in parents:
            assert parents["graph.stop"] == {"agent"}
        generate = next(item for item in exported if item["name"] == "plan")
        assert generate["attributes"]["gen_ai.operation.name"] == "chat"
        assert generate["attributes"]["gen_ai.provider.name"] == "fake"
        tool = next(item for item in exported if item["name"] == "tool")
        assert tool["attributes"]["gen_ai.operation.name"] == "execute_tool"
        assert tool["attributes"]["gen_ai.tool.name"] == "search_filings"
        assert len({item["trace_id"] for item in exported}) == 1
        assert all(posted == "/v1/traces" for posted, _body in server.bodies)
    finally:
        server.shutdown()
        server.server_close()


def test_blank_otlp_endpoint_and_grpc_protocol_are_noops() -> None:
    blank = _settings(otel_exporter_otlp_endpoint="  ")
    assert blank.otel_exporter_otlp_endpoint is None
    configure(blank)
    with observation("ask", ObservationKind.ASK):
        assert current_trace_id() is None
    configure(
        _settings(
            otel_exporter_otlp_endpoint="http://127.0.0.1:9",
            otel_exporter_otlp_protocol="grpc",
        )
    )
    with observation("ask", ObservationKind.ASK):
        assert current_trace_id() is None


def test_otlp_constructor_failure_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("collector sdk missing")

    monkeypatch.setattr("capitalcite.trace.otlp.OtlpExporter", boom)
    configure(_settings(otel_exporter_otlp_endpoint="http://127.0.0.1:4318"))
    with observation("ask", ObservationKind.ASK):
        assert current_trace_id() is None


def test_otlp_fail_open_keeps_the_answer_and_bounds_latency(tmp_path: Path) -> None:
    application, _setup = _indexed_app(tmp_path)
    body = {"question": _QUESTION, "model": "fake", "k": 4}
    slow = _Server(_Slow)
    slow_thread = threading.Thread(target=slow.serve_forever, daemon=True)
    slow_thread.start()
    try:
        with TestClient(application) as http:
            configure(_settings())
            control = _post(http, body)
            assert control["kind"] == "answered"
            control_times = _samples(http, body)
            cases = (
                ("down", f"http://127.0.0.1:{_closed_port()}", None),
                ("unreachable", "http://192.0.2.1:4318", None),
                ("slow", f"http://127.0.0.1:{slow.server_address[1]}", slow),
            )
            for label, endpoint, _server in cases:
                configure(
                    _settings(
                        otel_exporter_otlp_endpoint=endpoint,
                        otel_exporter_otlp_timeout=0.2,
                    )
                )
                got = _post(http, body)
                assert _banker(got) == _banker(control), label
                times = _samples(http, body)
                extra = statistics.median(times) - statistics.median(control_times)
                assert extra < 0.05, (label, extra, times, control_times)
    finally:
        slow.shutdown()
        slow.server_close()


def _settings(**overrides: object) -> Settings:
    payload: dict[str, object] = {
        "sec_user_agent": "capitalcite-tests tests@example.com",
        "langfuse_tracing_enabled": False,
        "chunker": ChunkerName.SECTION,
        "chunk_size": 32,
        "chunk_overlap": 4,
        "qdrant_collection": "filings",
        "embedder": "bge-small",
        "search_k": 8,
        "rrf_k": 60,
        "ask_model": "fake",
    }
    payload.update(overrides)
    return Settings.model_validate(payload)


def _indexed_app(tmp_path: Path, extra_chunks: tuple[Chunk, ...] = ()) -> tuple[FastAPI, object]:
    chunk = Chunk(
        doc_id="doc-jpm",
        section="Item 7",
        start=10,
        end=10 + len(_QUOTE),
        offset=10,
        source_url="https://www.sec.gov/Archives/edgar/data/19617/jpm.htm",
        text=_QUOTE,
        token_count=8,
        chunker=ChunkerName.SECTION,
        ticker="JPM",
        form="10-K",
        period="2024-12-31",
        accession="doc-jpm",
    )
    folder = tmp_path / "parsed" / "edgar" / "JPM" / "10-K" / "2024-12-31" / "doc-jpm"
    folder.mkdir(parents=True)
    (folder / "chunks.jsonl").write_text(chunk.model_dump_json() + "\n", encoding="utf-8")
    for extra in extra_chunks:
        extra_folder = (
            tmp_path / "parsed" / "edgar" / extra.ticker / extra.form / extra.period / extra.doc_id
        )
        extra_folder.mkdir(parents=True)
        (extra_folder / "chunks.jsonl").write_text(extra.model_dump_json() + "\n", encoding="utf-8")
    client = AsyncQdrantClient(":memory:")
    settings = _settings()
    setup = setup_retrieval(settings, client=client, embedder=FakeEmbedder())
    asyncio.run(build_index(setup, tmp_path))
    application = create_app()
    application.dependency_overrides[get_setup] = lambda: setup
    application.dependency_overrides[get_settings] = lambda: settings
    return application, setup


def _post(http: TestClient, body: Mapping[str, object]) -> dict[str, Any]:
    response = http.post("/ask", json=body)
    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload, dict)
    return payload


def _samples(http: TestClient, body: Mapping[str, object], n: int = 5) -> list[float]:
    times: list[float] = []
    for _ in range(n):
        started = time.perf_counter()
        got = _post(http, body)
        times.append(time.perf_counter() - started)
        assert got["kind"] == "answered"
    return times


def _banker(payload: Mapping[str, Any]) -> dict[str, object]:
    citations = payload.get("citations")
    rows: list[dict[str, object]] = []
    if isinstance(citations, list):
        for item in citations:
            if not isinstance(item, dict):
                continue
            rows.append(
                {
                    "doc_id": item.get("doc_id"),
                    "start": item.get("start"),
                    "end": item.get("end"),
                    "quote": item.get("quote"),
                }
            )
    return {
        "kind": payload.get("kind"),
        "answer": payload.get("answer"),
        "refusal_reason": payload.get("refusal_reason"),
        "citations": rows,
    }


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    assert isinstance(port, int)
    return port


def _decode(bodies: list[tuple[str, bytes]]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for _path, raw in bodies:
        message = ExportTraceServiceRequest()
        message.ParseFromString(raw)
        for resource_spans in message.resource_spans:
            service_name = ""
            for attr in resource_spans.resource.attributes:
                if attr.key == "service.name":
                    service_name = attr.value.string_value
            for scope_spans in resource_spans.scope_spans:
                for span in scope_spans.spans:
                    attributes: dict[str, object] = {}
                    for attr in span.attributes:
                        value = attr.value
                        if value.HasField("string_value"):
                            attributes[attr.key] = value.string_value
                        elif value.HasField("int_value"):
                            attributes[attr.key] = value.int_value
                        elif value.HasField("double_value"):
                            attributes[attr.key] = value.double_value
                        elif value.HasField("bool_value"):
                            attributes[attr.key] = value.bool_value
                    found.append(
                        {
                            "name": span.name,
                            "trace_id": span.trace_id.hex(),
                            "span_id": span.span_id.hex(),
                            "parent": span.parent_span_id.hex(),
                            "attributes": attributes,
                            "service_name": service_name,
                        }
                    )
    return found
