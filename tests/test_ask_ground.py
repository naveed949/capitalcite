from __future__ import annotations

from capitalcite.ask import Answered, Citation, DraftCite, ModelDraft, Refused, ground
from capitalcite.chunking.models import Chunk, ChunkerName
from capitalcite.retrieval import Hit

_JPM_URL = "https://www.sec.gov/Archives/edgar/data/19617/jpm.htm"
_BAC_URL = "https://www.sec.gov/Archives/edgar/data/70858/bac.htm"


def test_ground_expands_a_verbatim_quote() -> None:
    hits = [_hit("doc-jpm", "tier one capital buffer", section="Item 15", start=10, end=40)]
    draft = ModelDraft(
        kind="answered",
        answer="The buffer is tier one capital.",
        confidence=0.8,
        citations=(DraftCite(index=0, quote="tier one capital"),),
    )
    answer = ground(draft, hits)
    assert isinstance(answer, Answered)
    assert answer.answer == "The buffer is tier one capital."
    assert answer.confidence == 0.8
    assert answer.citations == (
        Citation(
            doc_id="doc-jpm",
            start=10,
            end=40,
            quote="tier one capital",
            section="Item 15",
            source_url=_JPM_URL,
        ),
    )


def test_ground_uses_the_cited_hit_not_a_neighbor() -> None:
    hits = [
        _hit("doc-jpm", "tier one capital buffer", start=10, end=40),
        _hit("doc-bac", "wholesale funding markets", url=_BAC_URL, start=80, end=120),
    ]
    draft = ModelDraft(
        kind="answered",
        answer="Funding comes from wholesale markets.",
        citations=(DraftCite(index=1, quote="wholesale funding"),),
    )
    answer = ground(draft, hits)
    assert isinstance(answer, Answered)
    assert answer.citations[0].doc_id == "doc-bac"
    assert answer.citations[0].start == 80
    assert answer.citations[0].end == 120
    assert answer.citations[0].quote == "wholesale funding"
    assert "wholesale funding" in hits[1].chunk.text


def test_ground_refuses_a_quote_that_is_not_in_the_chunk() -> None:
    hits = [_hit("doc-jpm", "tier one capital buffer")]
    draft = ModelDraft(
        kind="answered",
        answer="The ratio was 15.7%.",
        citations=(DraftCite(index=0, quote="15.7%"),),
    )
    answer = ground(draft, hits)
    assert isinstance(answer, Refused)


def test_ground_refuses_an_index_outside_the_hits() -> None:
    hits = [_hit("doc-jpm", "tier one capital buffer")]
    draft = ModelDraft(
        kind="answered",
        answer="Something.",
        citations=(DraftCite(index=3, quote="tier one"),),
    )
    assert isinstance(ground(draft, hits), Refused)
    negative = ModelDraft(
        kind="answered",
        answer="Something.",
        citations=(DraftCite(index=-1, quote="tier one"),),
    )
    assert isinstance(ground(negative, hits), Refused)


def test_ground_passes_a_refusal_through() -> None:
    draft = ModelDraft(
        kind="refused",
        refusal_reason="The filing does not state that ratio.",
        confidence=0.2,
    )
    answer = ground(draft, [_hit("doc-jpm", "tier one capital buffer")])
    assert isinstance(answer, Refused)
    assert answer.refusal_reason == "The filing does not state that ratio."
    assert answer.confidence == 0.2


def test_ground_refuses_an_answered_draft_with_no_citations() -> None:
    draft = ModelDraft.model_construct(
        kind="answered",
        answer="The ratio was 15.7%.",
        refusal_reason="",
        confidence=0.9,
        citations=(),
    )
    answer = ground(draft, [_hit("doc-jpm", "tier one capital buffer")])
    assert isinstance(answer, Refused)


def _hit(
    doc_id: str,
    text: str,
    *,
    section: str = "Item 7",
    start: int = 10,
    end: int = 40,
    url: str = _JPM_URL,
) -> Hit:
    return Hit(
        score=1.0,
        chunk=Chunk(
            doc_id=doc_id,
            section=section,
            start=start,
            end=end,
            offset=start,
            source_url=url,
            text=text,
            token_count=4,
            chunker=ChunkerName.SECTION,
            ticker="JPM",
            form="10-K",
            period="2024-12-31",
            accession=doc_id,
        ),
    )
