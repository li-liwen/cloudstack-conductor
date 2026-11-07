from __future__ import annotations

import math

import pytest

from conductor.knowledge.chunking import (
    chunk_text,
    content_hash,
    document_id,
    estimate_tokens,
    lexical_text,
    tokenize_lexical,
)
from conductor.knowledge.embeddings import mock_embedding
from conductor.knowledge.ingest import parse_markdown


def test_tokenization_handles_chinese_bigrams() -> None:
    tokens = tokenize_lexical("云主机快照")
    assert "云" in tokens
    assert "云主" in tokens
    assert "快照" in tokens
    assert lexical_text("云主机快照") == " ".join(tokens)


def test_chunking_respects_size_and_keeps_content() -> None:
    text = "\n\n".join(f"Paragraph {index} with some useful content." for index in range(60))
    chunks = chunk_text(text, max_tokens=120, overlap_tokens=20)
    assert len(chunks) > 2
    assert all(chunk.token_count <= 160 for chunk in chunks)
    assert "Paragraph 0" in chunks[0].content
    assert "Paragraph 59" in chunks[-1].content


def test_chunking_keeps_headings_as_section() -> None:
    text = "# Networking\n\n" + "\n\n".join(f"Rule {index}" for index in range(20))
    chunks = chunk_text(text, max_tokens=100, overlap_tokens=10)
    assert chunks[0].section == "Networking"


def test_token_estimate_is_positive() -> None:
    assert estimate_tokens("hello world") >= 1
    assert estimate_tokens("") == 1


def test_mock_embedding_is_deterministic_and_normalised() -> None:
    first = mock_embedding("cloud instance price", 64)
    second = mock_embedding("cloud instance price", 64)
    assert first == second
    assert len(first) == 64
    assert math.isclose(math.sqrt(sum(value * value for value in first)), 1.0, rel_tol=1e-6)


def test_document_identity_is_stable() -> None:
    assert document_id("https://example.com/docs", "1.0", "abc") == document_id(
        "https://example.com/docs", "1.0", "abc"
    )
    assert document_id("https://example.com/docs", "1.0", "abc") != document_id(
        "https://example.com/docs", "1.1", "abc"
    )
    assert content_hash("x") != content_hash("y")


def test_markdown_front_matter(tmp_path) -> None:
    path = tmp_path / "doc.md"
    path.write_text(
        "---\ntitle: Example\nversion: '2.0'\n"
        "source_url: https://example.com/docs/example\nlanguage: en\n---\n\n# Example\n\nBody.\n",
        encoding="utf-8",
    )
    document = parse_markdown(path)
    assert document.title == "Example"
    assert document.version == "2.0"
    assert document.source_url == "https://example.com/docs/example"
    assert document.text.startswith("# Example")


def test_markdown_requires_version(tmp_path) -> None:
    path = tmp_path / "doc.md"
    path.write_text("# Untitled\n\nBody.\n", encoding="utf-8")
    with pytest.raises(ValueError):
        parse_markdown(path)
    document = parse_markdown(path, default_version="1.0")
    assert document.version == "1.0"
    assert document.title == "Untitled"
