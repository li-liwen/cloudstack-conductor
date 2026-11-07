"""Version-pinned documentation ingestion.

Sources are public CloudStack documentation and approved task instructions.
Each document records version, source URL, section, content hash, and ingestion
time. Private pricing or runbooks must not be ingested into the public index.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from conductor.db.base import new_id, utcnow
from conductor.db.knowledge import KnowledgeRepository
from conductor.knowledge.chunking import (
    chunk_text,
    content_hash,
    document_id,
    lexical_text,
)
from conductor.knowledge.embeddings import EmbeddingClient
from conductor.models.knowledge import (
    IngestReport,
    KnowledgeChunk,
    KnowledgeDocument,
)

logger = logging.getLogger(__name__)


@dataclass
class SourceDocument:
    path: Path
    title: str
    version: str
    source_url: str
    language: str = "en"
    license_note: str | None = None
    section: str | None = None
    text: str = ""


def parse_markdown(path: Path, *, default_version: str | None = None) -> SourceDocument:
    raw = path.read_text(encoding="utf-8")
    front: dict[str, str] = {}
    body = raw
    if raw.startswith("---\n"):
        end = raw.find("\n---\n", 4)
        if end > 0:
            header = raw[4:end]
            body = raw[end + 5 :]
            for line in header.splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    front[key.strip()] = value.strip().strip('"').strip("'")
    title = front.get("title") or _first_heading(body) or path.stem
    version = front.get("version") or default_version
    if not version:
        raise ValueError(f"missing documentation version for {path.name}")
    source_url = front.get("source_url") or f"https://example.com/docs/{path.stem}"
    if not source_url.startswith(("https://", "http://")):
        raise ValueError("documentation source URLs must be HTTP(S)")
    return SourceDocument(
        path=path,
        title=title[:500],
        version=version[:64],
        source_url=source_url,
        language=front.get("language", "en"),
        license_note=front.get("license") or front.get("license_note"),
        section=front.get("section"),
        text=body.strip(),
    )


def _first_heading(text: str) -> str | None:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return None


class KnowledgeIngestor:
    def __init__(self, session: AsyncSession, embeddings: EmbeddingClient) -> None:
        self.session = session
        self.embeddings = embeddings
        self.repo = KnowledgeRepository(session)

    async def ingest_document(
        self,
        source: SourceDocument,
        *,
        chunk_tokens: int = 750,
        overlap_tokens: int = 100,
    ) -> tuple[int, bool]:
        digest = content_hash(source.text)
        doc_id = document_id(source.source_url, source.version, digest)
        document = KnowledgeDocument(
            document_id=doc_id,
            title=source.title,
            version=source.version,
            source_url=source.source_url,
            section=source.section,
            content_hash=digest,
            language=source.language,
            license_note=source.license_note,
            ingested_at=utcnow(),
        )
        _, created = await self.repo.add_document(document)
        if not created:
            return 0, False
        chunks = chunk_text(source.text, max_tokens=chunk_tokens, overlap_tokens=overlap_tokens)
        prepared: list[tuple[KnowledgeChunk, list[float] | None, str]] = []
        embeddings: list[list[float]] | None = None
        try:
            embeddings = await self.embeddings.embed([chunk.content for chunk in chunks])
        except Exception:
            logger.warning("embedding unavailable; storing lexical-only chunks")
            embeddings = None
        for index, chunk in enumerate(chunks):
            prepared.append(
                (
                    KnowledgeChunk(
                        chunk_id=f"{doc_id}_{index:03d}",
                        document_id=doc_id,
                        ordinal=index,
                        content=chunk.content,
                        token_count=chunk.token_count,
                        section=chunk.section or source.section,
                    ),
                    embeddings[index] if embeddings else None,
                    lexical_text(chunk.content),
                )
            )
        await self.repo.replace_chunks(doc_id, prepared)
        return len(prepared), True

    async def ingest_directory(
        self,
        directory: str | Path,
        *,
        default_version: str | None = None,
        chunk_tokens: int = 750,
        overlap_tokens: int = 100,
    ) -> IngestReport:
        root = Path(directory)
        if not root.is_dir():
            raise ValueError("the documentation directory does not exist")
        started = utcnow()
        documents = 0
        chunks = 0
        skipped = 0
        for path in sorted(root.rglob("*.md")):
            try:
                source = parse_markdown(path, default_version=default_version)
                added, created = await self.ingest_document(
                    source,
                    chunk_tokens=chunk_tokens,
                    overlap_tokens=overlap_tokens,
                )
                documents += 1
                chunks += added
                if not created:
                    skipped += 1
            except ValueError as exc:
                logger.warning("skipping documentation file: %s", exc)
                skipped += 1
        await self.session.commit()
        return IngestReport(
            run_id=new_id("ing"),
            documents=documents,
            chunks=chunks,
            skipped=skipped,
            started_at=started,
            finished_at=utcnow(),
            source=str(root),
            version=default_version or "mixed",
        )
