"""In-process tracing. No network and no Langfuse server."""

from __future__ import annotations

import subprocess
import sys
import types
from collections.abc import Mapping
from pathlib import Path

import pytest

from capitalcite.chunking.models import Chunk, ChunkerName
from capitalcite.config import Settings
from capitalcite.retrieval.types import Hit
from capitalcite.trace import (
    MemoryExporter,
    ObservationKind,
    ask_attrs,
    configure,
    configure_for_tests,
    current_trace_id,
    eval_attrs,
    gate_decision,
    generation_attrs,
    observation,
    retrieval_attrs,
)
from capitalcite.trace.attrs import sanitize
from capitalcite.trace.langfuse_exporter import LangfuseExporter
from capitalcite.trace.mask import mask_text
from capitalcite.trace.types import AttrValue, SpanRecord

_SECRET = "sk-abcdefghijklmnopqrstuvwxyz"
_EMAIL = "ada@example.com"
_CARD = "4111111111111111"
_SSN = "123-45-6789"


def test_unconfigured_observation_records_nothing() -> None:
    exporter = MemoryExporter()
    with observation("ask", ObservationKind.ASK, question="secret"):
        assert current_trace_id() is None
    assert exporter.finished() == ()
    assert current_trace_id() is None


def test_nested_spans_share_a_trace_and_mask_the_question() -> None:
    exporter = MemoryExporter()
    configure_for_tests(exporter)
    question = f"What did JPM say? mail {_EMAIL} ssn {_SSN} key {_SECRET} card {_CARD}"
    with observation("ask", ObservationKind.ASK) as span:
        root_id = current_trace_id()
        assert root_id
        span.set(**ask_attrs(path="rag", question=question))
        span.set(Authorization=f"Bearer {_SECRET}", chunk_text="tier one capital buffer")
        with observation("retrieve", ObservationKind.RETRIEVE):
            assert current_trace_id() == root_id
    assert current_trace_id() is None
    child, root = exporter.finished()
    assert root.name == "ask"
    assert root.kind is ObservationKind.ASK
    assert root.parent_id is None
    assert root.trace_id == root_id
    assert child.name == "retrieve"
    assert child.parent_id == root.span_id
    assert child.trace_id == root.trace_id
    assert root.status == "ok"
    assert root.attributes["capitalcite.path"] == "rag"
    assert root.attributes["capitalcite.sampled"] is True
    assert isinstance(root.attributes["capitalcite.latency_ms"], float)
    blob = root.model_dump_json() + child.model_dump_json()
    assert _EMAIL not in blob
    assert _SSN not in blob
    assert _SECRET not in blob
    assert _CARD not in blob
    assert "Bearer" not in blob
    assert "Authorization" not in blob
    assert "tier one capital buffer" not in blob
    assert "[REDACTED EMAIL]" in str(root.attributes["capitalcite.question"])
    assert "[REDACTED SSN]" in str(root.attributes["capitalcite.question"])


def test_sample_rate_zero_exports_nothing() -> None:
    exporter = MemoryExporter()
    configure_for_tests(exporter, sample_rate=0)
    with observation("ask", ObservationKind.ASK):
        assert current_trace_id() is None
        with observation("retrieve", ObservationKind.RETRIEVE):
            assert current_trace_id() is None
    assert exporter.finished() == ()


def test_body_exception_is_recorded_and_reraised() -> None:
    exporter = MemoryExporter()
    configure_for_tests(exporter)
    with pytest.raises(RuntimeError, match="nope"), observation("ask", ObservationKind.ASK):
        raise RuntimeError("nope")
    (record,) = exporter.finished()
    assert record.status == "error"
    assert record.name == "ask"


def test_export_and_begin_failures_do_not_escape() -> None:
    class Boom(MemoryExporter):
        def begin(
            self,
            *,
            name: str,
            kind: ObservationKind,
            span_id: str,
            attributes: Mapping[str, AttrValue],
        ) -> None:
            raise RuntimeError(f"begin failed {name} {kind} {span_id} {attributes}")

        def export(self, record: SpanRecord) -> None:
            raise RuntimeError("export failed")

    configure_for_tests(Boom())
    with observation("ask", ObservationKind.ASK):
        assert current_trace_id() is not None


def test_gate_decision_records_allowlisted_fields() -> None:
    exporter = MemoryExporter()
    configure_for_tests(exporter)
    with gate_decision("search_filings", "deny", f"blocked {_EMAIL}"):
        assert current_trace_id() is not None
    (record,) = exporter.finished()
    assert record.name == "gate"
    assert record.kind is ObservationKind.GATE
    assert record.attributes["capitalcite.tool"] == "search_filings"
    assert record.attributes["capitalcite.gate.decision"] == "deny"
    assert _EMAIL not in record.model_dump_json()
    assert "[REDACTED EMAIL]" in str(record.attributes["capitalcite.gate.reason"])


def test_file_exporter_appends_jsonl_without_langfuse(tmp_path: Path) -> None:
    path = tmp_path / "spans.jsonl"
    settings = Settings.model_validate(
        {
            "sec_user_agent": "capitalcite-tests tests@example.com",
            "langfuse_tracing_enabled": False,
            "langfuse_public_key": "pk-lf-test",
            "langfuse_secret_key": _SECRET,
            "capitalcite_trace_file": str(path),
        }
    )
    configure(settings)
    with observation("ask", ObservationKind.ASK, **ask_attrs(path="rag", question="cet1")):
        pass
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = SpanRecord.model_validate_json(lines[0])
    assert record.name == "ask"
    assert record.attributes["capitalcite.path"] == "rag"
    assert _SECRET not in lines[0]


def test_missing_keys_configure_is_a_noop() -> None:
    settings = Settings.model_validate(
        {
            "sec_user_agent": "capitalcite-tests tests@example.com",
            "langfuse_tracing_enabled": True,
            "langfuse_public_key": "  ",
            "langfuse_secret_key": "",
        }
    )
    configure(settings)
    with observation("ask", ObservationKind.ASK):
        assert current_trace_id() is None


def test_langfuse_constructor_failure_stays_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("langfuse down")

    monkeypatch.setattr("capitalcite.trace.langfuse_exporter.LangfuseExporter", boom)
    settings = Settings.model_validate(
        {
            "sec_user_agent": "capitalcite-tests tests@example.com",
            "langfuse_tracing_enabled": True,
            "langfuse_public_key": "pk-lf-test",
            "langfuse_secret_key": "sk-lf-testsecret",
            "langfuse_host": "http://127.0.0.1:9",
        }
    )
    configure(settings)
    with observation("ask", ObservationKind.ASK):
        assert current_trace_id() is None


def test_langfuse_adapter_maps_ask_and_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    started: dict[str, object] = {}

    class Obs:
        def update(self, **kwargs: object) -> None:
            started["update"] = kwargs

    class Manager:
        def __enter__(self) -> Obs:
            return Obs()

        def __exit__(self, *_args: object) -> None:
            started["exited"] = True

    starts: list[dict[str, object]] = []

    class Client:
        def start_as_current_observation(self, **kwargs: object) -> Manager:
            starts.append(kwargs)
            return Manager()

        def flush(self) -> None:
            started["flush"] = True

        def shutdown(self) -> None:
            started["shutdown"] = True

    class FakeLangfuse:
        def __init__(self, **kwargs: object) -> None:
            started["init"] = kwargs
            self._client = Client()

        def start_as_current_observation(self, **kwargs: object) -> Manager:
            return self._client.start_as_current_observation(**kwargs)

        def flush(self) -> None:
            self._client.flush()

        def shutdown(self) -> None:
            self._client.shutdown()

    module = types.ModuleType("langfuse")
    module.Langfuse = FakeLangfuse  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langfuse", module)
    exporter = LangfuseExporter(
        public_key="pk-lf-test",
        secret_key="sk-lf-testsecret",
        host="http://127.0.0.1:9",
    )
    init = started["init"]
    assert isinstance(init, dict)
    assert init["sample_rate"] == 1.0
    assert init["secret_key"] == "sk-lf-testsecret"
    span_id = "a" * 16
    question = ask_attrs(path="rag", question=f"hello {_EMAIL}")["capitalcite.question"]
    exporter.begin(
        name="ask",
        kind=ObservationKind.ASK,
        span_id=span_id,
        attributes={"capitalcite.question": question, "capitalcite.path": "rag"},
    )
    exporter.export(
        SpanRecord(
            name="ask",
            kind=ObservationKind.ASK,
            trace_id="b" * 32,
            span_id=span_id,
            attributes={
                "capitalcite.question": question,
                "capitalcite.path": "rag",
                "capitalcite.outcome": "answered",
            },
            status="ok",
            duration_ms=1.0,
        )
    )
    update = started["update"]
    assert isinstance(update, dict)
    assert update["input"] == {"question": question}
    assert update["output"] == {"outcome": "answered"}
    assert _EMAIL not in str(update)
    assert "sk-lf-testsecret" not in str(update)
    assert started["exited"] is True
    assert starts[0]["as_type"] == "chain"
    exporter.shutdown()
    assert started["flush"] is True
    assert started["shutdown"] is True


def test_importing_trace_does_not_import_langfuse() -> None:
    code = (
        "import sys\n"
        "from capitalcite.config import Settings\n"
        "from capitalcite.trace import ObservationKind, configure, observation\n"
        "settings = Settings.model_validate({\n"
        "    'sec_user_agent': 'capitalcite-tests tests@example.com',\n"
        "    'langfuse_tracing_enabled': False,\n"
        "    'langfuse_public_key': 'pk-lf-test',\n"
        "    'langfuse_secret_key': 'sk-lf-testsecret',\n"
        "})\n"
        "configure(settings)\n"
        "with observation('ask', ObservationKind.ASK):\n"
        "    pass\n"
        "raise SystemExit('langfuse' in sys.modules)\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_ssn_is_masked() -> None:
    assert _SSN not in mask_text(f"ssn {_SSN}")
    assert mask_text(f"ssn {_SSN}") == "ssn [REDACTED SSN]"


def test_question_is_masked_and_truncated() -> None:
    question = ("a" * 180) + f" {_EMAIL} " + ("b" * 80)
    masked = mask_text(question, max_len=200)
    assert _EMAIL not in masked
    assert len(masked) == 200
    attrs = ask_attrs(path="rag", question=question)
    assert len(str(attrs["capitalcite.question"])) == 200
    assert sanitize({"Authorization": f"Bearer {_SECRET}", "capitalcite.ok": True}) == {
        "capitalcite.ok": True
    }
    assert sanitize({"capitalcite.path": "sideways"}) == {}
    assert sanitize({"capitalcite.hit_count": True}) == {}
    assert sanitize({"capitalcite.hit.8.doc_id": "doc"}) == {}


def test_retrieval_attrs_cap_hits_and_omit_chunk_text() -> None:
    hits = [
        _hit(doc_id=f"doc-{index}", score=float(index), text=f"chunk body {index}")
        for index in range(10)
    ]
    attrs = retrieval_attrs(hits, mode="hybrid", collection="filings", k=10)
    assert attrs["capitalcite.hit_count"] == 10
    assert attrs["capitalcite.hit.7.doc_id"] == "doc-7"
    assert "capitalcite.hit.8.doc_id" not in attrs
    assert "chunk body" not in str(attrs)
    capped = retrieval_attrs(hits, mode="bm25", collection="filings", k=3)
    assert "capitalcite.hit.2.score" in capped
    assert "capitalcite.hit.3.score" not in capped


def test_eval_attrs_keep_git_sha_digit_runs() -> None:
    sha = "abc" + ("1" * 16) + "def"
    attrs = eval_attrs(git_sha=sha, suite="golden", small=True)
    assert attrs["capitalcite.git_sha"] == sha
    assert "[REDACTED DIGITS]" not in str(attrs["capitalcite.git_sha"])


def test_generation_cost_requires_a_known_rate_and_both_counts() -> None:
    priced = generation_attrs(model="gpt-4o-mini", input_tokens=1_000_000, output_tokens=1_000_000)
    assert priced["capitalcite.cost_usd"] == pytest.approx(0.75)
    assert "capitalcite.cost_usd" not in generation_attrs(model="fake")
    assert "capitalcite.cost_usd" not in generation_attrs(model="gpt-4o-mini", input_tokens=10)
    assert "capitalcite.cost_usd" not in generation_attrs(model="gpt-4o-mini", output_tokens=10)


def _hit(*, doc_id: str, score: float, text: str) -> Hit:
    return Hit(
        score=score,
        chunk=Chunk(
            doc_id=doc_id,
            section="Item 15",
            start=0,
            end=len(text),
            offset=0,
            source_url="https://www.sec.gov/Archives/edgar/data/19617/jpm.htm",
            text=text,
            token_count=4,
            chunker=ChunkerName.SECTION,
            ticker="JPM",
            form="10-K",
            period="2023-12-31",
            accession=doc_id,
        ),
    )
