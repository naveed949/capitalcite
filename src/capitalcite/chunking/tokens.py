"""Token estimates used for size, overlap, and logged stats.

The counter is character length divided by four, rounded up. Issue #4 picks
the embedding model. Until then a model-specific BPE would lock the numbers
to the wrong vocabulary.
"""

from __future__ import annotations

CHARS_PER_TOKEN = 4


def count_tokens(text: str) -> int:
    """Approximate token count. Empty text is 0."""
    if not text:
        return 0
    return max(1, (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN)


def token_chars(token_count: int) -> int:
    """Convert a token budget to a character budget."""
    if token_count < 1:
        raise ValueError("token budget must be at least 1")
    return token_count * CHARS_PER_TOKEN
