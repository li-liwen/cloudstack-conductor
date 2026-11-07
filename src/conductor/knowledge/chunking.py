"""Text chunking and Chinese-aware lexical tokenisation.

Token counts are estimates for chunk sizing only; billing never uses them.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

_CJK = re.compile(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]")
_ASCII_WORD = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.:/-]{1,}")
_SENTENCE = re.compile(r"(?<=[。！？.!?])\s+")


def estimate_tokens(text: str) -> int:
    cjk = len(_CJK.findall(text))
    ascii_words = len(_ASCII_WORD.findall(text))
    other = max(0, len(text) - cjk) / 4
    return max(1, int(cjk / 1.6 + ascii_words * 1.3 + other * 0.25))


def tokenize_lexical(text: str) -> list[str]:
    """Produce PostgreSQL ``simple`` FTS tokens.

    Chinese has no spaces in typical prose, so CJK runs become adjacent bigrams
    plus single characters. This provides deterministic lexical matching
    without an external segmenter.
    """

    tokens: list[str] = []
    for word in _ASCII_WORD.findall(text):
        tokens.append(word.lower())
    for run in re.findall(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]+", text):
        characters = list(run)
        tokens.extend(characters)
        tokens.extend("".join(characters[i : i + 2]) for i in range(len(characters) - 1))
    # Deduplicate while preserving order.
    seen: set[str] = set()
    result: list[str] = []
    for token in tokens:
        if token not in seen:
            seen.add(token)
            result.append(token)
    return result


def lexical_text(text: str) -> str:
    return " ".join(tokenize_lexical(text))


def lexical_query(text: str) -> str:
    return " ".join(tokenize_lexical(text))


@dataclass
class TextChunk:
    ordinal: int
    content: str
    token_count: int
    section: str | None = None


def chunk_text(text: str, *, max_tokens: int = 750, overlap_tokens: int = 100) -> list[TextChunk]:
    if max_tokens < 100:
        raise ValueError("max_tokens is too small")
    paragraphs = [part.strip() for part in re.split(r"\n{2,}", text) if part.strip()]
    chunks: list[TextChunk] = []
    current: list[str] = []
    current_tokens = 0
    section: str | None = None

    def flush() -> None:
        nonlocal current, current_tokens
        if not current:
            return
        content = "\n\n".join(current).strip()
        if content:
            chunks.append(
                TextChunk(
                    ordinal=len(chunks),
                    content=content,
                    token_count=estimate_tokens(content),
                    section=section,
                )
            )
        current = []
        current_tokens = 0

    for paragraph in paragraphs:
        if paragraph.startswith("#"):
            heading = paragraph.lstrip("#").strip()
            if heading:
                section = heading[:300]
        tokens = estimate_tokens(paragraph)
        if tokens > max_tokens:
            flush()
            sentences = _SENTENCE.split(paragraph)
            buffer: list[str] = []
            buffer_tokens = 0
            for sentence in sentences:
                sentence_tokens = estimate_tokens(sentence)
                if buffer and buffer_tokens + sentence_tokens > max_tokens:
                    content = " ".join(buffer).strip()
                    chunks.append(
                        TextChunk(
                            ordinal=len(chunks),
                            content=content,
                            token_count=estimate_tokens(content),
                            section=section,
                        )
                    )
                    carry: list[str] = []
                    carry_tokens = 0
                    for item in reversed(buffer):
                        if carry_tokens + estimate_tokens(item) > overlap_tokens:
                            break
                        carry.insert(0, item)
                        carry_tokens += estimate_tokens(item)
                    buffer = carry
                    buffer_tokens = carry_tokens
                buffer.append(sentence)
                buffer_tokens += sentence_tokens
            if buffer:
                content = " ".join(buffer).strip()
                chunks.append(
                    TextChunk(
                        ordinal=len(chunks),
                        content=content,
                        token_count=estimate_tokens(content),
                        section=section,
                    )
                )
            continue
        if current and current_tokens + tokens > max_tokens:
            flush()
        current.append(paragraph)
        current_tokens += tokens
    flush()
    return chunks


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def document_id(source_url: str, version: str, digest: str) -> str:
    return "doc_" + hashlib.sha256(f"{source_url}|{version}|{digest}".encode()).hexdigest()[:24]
