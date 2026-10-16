from __future__ import annotations

import pytest
from pydantic import ValidationError

from capitalcite.ask import Answered, Citation, DraftCite, ModelDraft, Refused, ground
from capitalcite.chunking.models import Chunk, ChunkerName
from capitalcite.retrieval import Hit

_JPM_URL = "https://www.sec.gov/Archives/edgar/data/19617/jpm.htm"
_BAC_URL = "https://www.sec.gov/Archives/edgar/data/70858/bac.htm"
_RATIO_TEXT = "The CET1 ratios were 15.7% under the Standardized Approach."


def test_ground_expands_a_verbatim_quote() -> None:
    hits = [_hit("doc-jpm", "tier one capital buffer", section="Item 15", start=10, end=40)]
    draft = ModelDraft(
        kind="answered",
        answer="The buffer is tier one capital.",
        citations=(DraftCite(index=1, quote="tier one capital"),),
    )
    answer = ground(draft, hits)
    assert isinstance(answer, Answered)
    assert answer.answer == "The buffer is tier one capital."
    assert answer.retrieval_score == hits[0].score
    assert answer.citations == (
        Citation(
            doc_id="doc-jpm",
            start=10,
            end=40,
            quote="tier one capital",
            quote_start=10,
            quote_end=26,
            section="Item 15",
            source_url=_JPM_URL,
        ),
    )


def test_ground_retrieval_score_is_the_minimum_cited_score() -> None:
    hits = [
        _hit("doc-jpm", "tier one capital buffer", start=10, end=40, score=0.4),
        _hit(
            "doc-bac",
            "wholesale funding markets",
            url=_BAC_URL,
            start=80,
            end=120,
            score=0.1,
        ),
    ]
    draft = ModelDraft(
        kind="answered",
        answer="Both passages.",
        citations=(
            DraftCite(index=1, quote="tier one capital"),
            DraftCite(index=2, quote="wholesale funding markets"),
        ),
    )
    answer = ground(draft, hits)
    assert isinstance(answer, Answered)
    assert answer.retrieval_score == 0.1


def test_ground_uses_the_cited_hit_not_a_neighbor() -> None:
    hits = [
        _hit("doc-jpm", "tier one capital buffer", start=10, end=40),
        _hit("doc-bac", "wholesale funding markets", url=_BAC_URL, start=80, end=120),
    ]
    draft = ModelDraft(
        kind="answered",
        answer="Funding comes from wholesale markets.",
        citations=(DraftCite(index=2, quote="wholesale funding markets"),),
    )
    answer = ground(draft, hits)
    assert isinstance(answer, Answered)
    assert answer.citations[0].doc_id == "doc-bac"
    assert answer.citations[0].start == 80
    assert answer.citations[0].end == 120
    assert answer.citations[0].quote == "wholesale funding markets"
    assert answer.citations[0].quote_start == 80
    assert answer.citations[0].quote_end == 105
    assert "wholesale funding markets" in hits[1].chunk.text


def test_ground_refuses_a_quote_that_is_not_in_the_chunk() -> None:
    hits = [_hit("doc-jpm", "tier one capital buffer")]
    draft = ModelDraft(
        kind="answered",
        answer="The ratio was 15.7%.",
        citations=(DraftCite(index=1, quote="15.7%"),),
    )
    answer = ground(draft, hits)
    assert isinstance(answer, Refused)


def test_ground_refuses_a_whitespace_quote_with_a_numeric_answer() -> None:
    hits = [_hit("doc-jpm", _RATIO_TEXT)]
    draft = ModelDraft(
        kind="answered",
        answer="JPM CET1 was 19.2%",
        citations=(DraftCite(index=1, quote=" "),),
    )
    answer = ground(draft, hits)
    assert isinstance(answer, Refused)
    assert "19.2" not in answer.refusal_reason


def test_ground_refuses_when_answer_figure_is_not_in_the_quote() -> None:
    hits = [_hit("doc-jpm", _RATIO_TEXT)]
    draft = ModelDraft(
        kind="answered",
        answer="JPM's CET1 was 12.3%",
        citations=(DraftCite(index=1, quote="CET1 ratios were 15.7%"),),
    )
    answer = ground(draft, hits)
    assert isinstance(answer, Refused)
    assert answer.refusal_reason == "answer contains a figure not present in the cited quotes"


def test_ground_accepts_when_answer_figure_is_in_the_quote() -> None:
    hits = [_hit("doc-jpm", _RATIO_TEXT, start=10, end=80)]
    quote = "CET1 ratios were 15.7%"
    draft = ModelDraft(
        kind="answered",
        answer="JPM's CET1 was 15.7%",
        citations=(DraftCite(index=1, quote=quote),),
    )
    answer = ground(draft, hits)
    assert isinstance(answer, Answered)
    assert answer.citations[0].quote == quote
    assert answer.citations[0].quote_start == 10 + _RATIO_TEXT.find(quote)
    assert answer.citations[0].quote_end == answer.citations[0].quote_start + len(quote)


def test_ground_exempts_years_and_figures_already_in_the_question() -> None:
    hits = [_hit("doc-jpm", _RATIO_TEXT)]
    draft = ModelDraft(
        kind="answered",
        answer="At year-end 2024 the Standardized CET1 ratio was 15.7%.",
        citations=(DraftCite(index=1, quote="CET1 ratios were 15.7%"),),
    )
    answer = ground(draft, hits, "What was JPMorgan CET1 at year-end 2024?")
    assert isinstance(answer, Answered)


def test_ground_refuses_an_index_outside_the_hits() -> None:
    hits = [_hit("doc-jpm", "tier one capital buffer")]
    draft = ModelDraft(
        kind="answered",
        answer="Something about capital buffers.",
        citations=(DraftCite(index=3, quote="tier one capital"),),
    )
    assert isinstance(ground(draft, hits), Refused)


def test_draft_cite_rejects_a_zero_index() -> None:
    with pytest.raises(ValidationError):
        DraftCite(index=0, quote="tier one")


def test_ground_refuses_a_constructed_zero_index() -> None:
    hits = [_hit("doc-jpm", "tier one capital buffer")]
    draft = ModelDraft.model_construct(
        kind="answered",
        answer="Something about capital buffers.",
        refusal_reason="",
        citations=(DraftCite.model_construct(index=0, quote="tier one capital"),),
    )
    answer = ground(draft, hits)
    assert isinstance(answer, Refused)
    assert "tier one" not in answer.refusal_reason


def test_ground_refuses_a_blank_quote() -> None:
    hits = [_hit("doc-jpm", "tier one capital buffer")]
    draft = ModelDraft.model_construct(
        kind="answered",
        answer="Something about capital buffers.",
        refusal_reason="",
        citations=(DraftCite.model_construct(index=1, quote=""),),
    )
    answer = ground(draft, hits)
    assert isinstance(answer, Refused)


def test_ground_passes_a_refusal_through() -> None:
    draft = ModelDraft(
        kind="refused",
        refusal_reason="The filing does not state that ratio.",
    )
    answer = ground(draft, [_hit("doc-jpm", "tier one capital buffer")])
    assert isinstance(answer, Refused)
    assert answer.refusal_reason == "The filing does not state that ratio."
    assert answer.retrieval_score == 0.0


def test_ground_refuses_an_answered_draft_with_no_citations() -> None:
    draft = ModelDraft.model_construct(
        kind="answered",
        answer="The ratio was 15.7%.",
        refusal_reason="",
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
    score: float = 1.0,
) -> Hit:
    span_end = max(end, start + len(text))
    return Hit(
        score=score,
        chunk=Chunk(
            doc_id=doc_id,
            section=section,
            start=start,
            end=span_end,
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
