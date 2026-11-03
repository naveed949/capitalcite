"""Pinned tokenizer counts of the prompts the adapters would send."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from functools import lru_cache
from typing import Any

from capitalcite.agent.models import _plan_system_prompt, _plan_user_prompt
from capitalcite.agent.types import TraceStep
from capitalcite.ask.models import _system_prompt, _user_prompt
from capitalcite.retrieval.types import Hit

TOKENIZER_NAME = "o200k_base"


@lru_cache(maxsize=1)
def encoding() -> Any:
    """Load o200k_base. First use may download the encoding over the network."""
    try:
        import tiktoken
    except ImportError as exc:
        raise RuntimeError(
            "tiktoken is not installed. Install the eval group with "
            "`uv sync --group eval`. First use may download o200k_base."
        ) from exc
    return tiktoken.get_encoding(TOKENIZER_NAME)


def count_tokens(text: str) -> int:
    return len(encoding().encode(text))


def prompt_templates() -> str:
    return _system_prompt() + "\n" + _plan_system_prompt()


def prompt_hash() -> str:
    return hashlib.sha256(prompt_templates().encode("utf-8")).hexdigest()


def rag_prompt_tokens(question: str, hits: Sequence[Hit]) -> int:
    return count_tokens(_system_prompt()) + count_tokens(_user_prompt(question, hits))


def agent_prompt_tokens(
    question: str,
    traces: Sequence[TraceStep],
    hits: Sequence[Hit],
    *,
    synthesized: bool,
) -> int:
    total = count_tokens(_plan_system_prompt()) + count_tokens(
        _plan_user_prompt(question, traces, hits)
    )
    if synthesized:
        total += rag_prompt_tokens(question, hits)
    return total
