import re
from collections.abc import Sequence
from dataclasses import dataclass

from medical_archive.documents.extraction import ExtractedPage

_TOKEN_PATTERN = re.compile(r"\w+|[^\w\s]", re.UNICODE)
_LOCAL_SUBTOKEN_CHARACTERS = 4


@dataclass(frozen=True)
class TextChunk:
    page_number: int
    text: str


def _token_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for match in _TOKEN_PATTERN.finditer(text):
        token = match.group()
        if token.isalnum() or token == "_":
            for offset in range(0, len(token), _LOCAL_SUBTOKEN_CHARACTERS):
                start = match.start() + offset
                spans.append((start, min(start + _LOCAL_SUBTOKEN_CHARACTERS, match.end())))
        else:
            spans.append((match.start(), match.end()))
    return spans


def count_local_tokens(text: str) -> int:
    """Count deterministic local subword estimates without sending text to a tokenizer."""
    return len(_token_spans(text))


def chunk_pages(
    pages: Sequence[ExtractedPage],
    *,
    max_tokens: int = 500,
    overlap_tokens: int = 80,
) -> list[TextChunk]:
    if max_tokens < 1:
        raise ValueError("max_tokens must be positive.")
    if overlap_tokens < 0 or overlap_tokens >= max_tokens:
        raise ValueError("overlap_tokens must be between zero and max_tokens - 1.")

    chunks: list[TextChunk] = []
    for page in pages:
        spans = _token_spans(page.text)
        if not spans:
            continue
        start_token = 0
        while start_token < len(spans):
            end_token = min(start_token + max_tokens, len(spans))
            start_offset = spans[start_token][0]
            end_offset = spans[end_token - 1][1]
            chunk_text = page.text[start_offset:end_offset].strip()
            if chunk_text:
                chunks.append(TextChunk(page_number=page.page_number, text=chunk_text))
            if end_token == len(spans):
                break
            start_token = end_token - overlap_tokens
    return chunks
