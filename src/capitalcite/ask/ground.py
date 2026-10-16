"""Expand a model draft into a citation, or refuse."""

from __future__ import annotations

from collections.abc import Sequence

from capitalcite.ask.types import Answer, Answered, Citation, ModelDraft, Refused, draft_problem
from capitalcite.retrieval.types import Hit


def ground(draft: ModelDraft, hits: Sequence[Hit]) -> Answer:
    """Copy doc_id, start, end, section, and source_url from the cited hit.

    Passage numbers are 1-based. The quote must be a verbatim substring of that
    hit's chunk text. An illegal draft or a quote that is not in the chunk
    becomes Refused. This does not call the model again. Confidence is the
    minimum retrieval score of the cited hits.
    """
    problem = draft_problem(draft)
    if problem is not None:
        return Refused(refusal_reason=problem)
    if draft.kind == "refused":
        return Refused(refusal_reason=draft.refusal_reason)
    citations: list[Citation] = []
    scores: list[float] = []
    for cite in draft.citations:
        if cite.index < 1 or cite.index > len(hits):
            return Refused(refusal_reason="citation index is outside the retrieved hits")
        hit = hits[cite.index - 1]
        chunk = hit.chunk
        if cite.quote == "" or cite.quote not in chunk.text:
            return Refused(
                refusal_reason="citation quote is not a verbatim substring of the cited passage"
            )
        citations.append(
            Citation(
                doc_id=chunk.doc_id,
                start=chunk.start,
                end=chunk.end,
                quote=cite.quote,
                section=chunk.section,
                source_url=chunk.source_url,
            )
        )
        scores.append(hit.score)
    return Answered(answer=draft.answer, citations=tuple(citations), confidence=min(scores))
