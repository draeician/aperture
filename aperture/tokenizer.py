"""Tokenizer protocol, stdlib whitespace default, and optional tiktoken adapter."""

from __future__ import annotations

from typing import Protocol

from aperture.errors import PolicyError

try:
    import tiktoken
except ImportError:  # tiktoken is an optional extra; core paths never require it
    tiktoken = None


class Tokenizer(Protocol):
    """Token counting and splitting contract used throughout Aperture.

    Implementations must be deterministic: the same input text always
    yields the same count() and the same split() result.

    split() returns the ordered token-strings for the text. Truncation
    math (added in a later step) slices this sequence by position
    (head/tail) and uses count() on the elided slice to report an exact
    elision count; split() need not preserve original whitespace when
    its pieces are rejoined.
    """

    def count(self, text: str) -> int:
        ...

    def split(self, text: str) -> list[str]:
        ...


class WhitespaceTokenizer:
    """Stdlib, deterministic default tokenizer. Tokens are whitespace-separated runs."""

    def split(self, text: str) -> list[str]:
        return text.split()

    def count(self, text: str) -> int:
        return len(self.split(text))


class TiktokenTokenizer:
    """Adapter over the optional tiktoken dependency, keyed by encoding name."""

    def __init__(self, encoding: str) -> None:
        if tiktoken is None:
            raise PolicyError(
                "tokenizer_id requests tiktoken but the 'tiktoken' package is not "
                "installed; install the optional 'tokens' extra (pip install "
                "aperture[tokens]) or use tokenizer_id='whitespace'"
            )
        self._encoding = tiktoken.get_encoding(encoding)

    def split(self, text: str) -> list[str]:
        return [self._encoding.decode([token]) for token in self._encoding.encode(text)]

    def count(self, text: str) -> int:
        return len(self._encoding.encode(text))


def get_tokenizer(tokenizer_id: str) -> Tokenizer:
    """Resolve a policy tokenizer_id into a Tokenizer instance.

    "whitespace" resolves to WhitespaceTokenizer. "tiktoken:<encoding>"
    resolves to TiktokenTokenizer(encoding), raising PolicyError if
    tiktoken is not installed. Any other shape raises PolicyError.
    """

    if tokenizer_id == "whitespace":
        return WhitespaceTokenizer()

    if tokenizer_id.startswith("tiktoken:"):
        encoding = tokenizer_id[len("tiktoken:") :]
        return TiktokenTokenizer(encoding)

    raise PolicyError(f"unrecognized tokenizer_id: {tokenizer_id!r}")
