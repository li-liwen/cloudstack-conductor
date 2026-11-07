"""Knowledge document/chunk persistence and hybrid retrieval primitives."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from conductor.db.models import KnowledgeChunkRow, KnowledgeDocumentRow
from conductor.models.common import ensure_utc
from conductor.models.knowledge import KnowledgeChunk, KnowledgeDocument


@dataclass
class RetrievalRow:
    chunk: KnowledgeChunk
    document: KnowledgeDocument
    score: float


def _document(row: KnowledgeDocumentRow) -> KnowledgeDocument:
    return KnowledgeDocument(
        document_id=row.document_id,
        title=row.title,
        version=row.version,
        source_url=row.source_url,
        section=row.section,
        content_hash=row.content_hash,
        language=row.language,
        license_note=row.license_note,
        ingested_at=ensure_utc(row.ingested_at),
        visibility=row.visibility,
    )


def _chunk(row: KnowledgeChunkRow) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=row.chunk_id,
        document_id=row.document_id,
        ordinal=row.ordinal,
        content=row.content,
        token_count=row.token_count,
        section=row.section,
    )


class KnowledgeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @property
    def dialect(self) -> str:
        return self.session.get_bind().dialect.name

    async def add_document(self, document: KnowledgeDocument) -> tuple[KnowledgeDocument, bool]:
        existing = await self.session.execute(
            select(KnowledgeDocumentRow).where(
                KnowledgeDocumentRow.source_url == document.source_url,
                KnowledgeDocumentRow.version == document.version,
                KnowledgeDocumentRow.content_hash == document.content_hash,
            )
        )
        row = existing.scalar_one_or_none()
        if row is not None:
            return _document(row), False
        row = KnowledgeDocumentRow(
            document_id=document.document_id,
            title=document.title,
            version=document.version,
            source_url=document.source_url,
            section=document.section,
            content_hash=document.content_hash,
            language=document.language,
            license_note=document.license_note,
            visibility=document.visibility,
            ingested_at=document.ingested_at,
        )
        self.session.add(row)
        await self.session.flush()
        return _document(row), True

    async def replace_chunks(
        self, document_id: str, chunks: list[tuple[KnowledgeChunk, list[float] | None, str]]
    ) -> int:
        await self.session.execute(delete(KnowledgeChunkRow).where(KnowledgeChunkRow.document_id == document_id))
        for chunk, embedding, lexical_text in chunks:
            self.session.add(
                KnowledgeChunkRow(
                    chunk_id=chunk.chunk_id,
                    document_id=document_id,
                    ordinal=chunk.ordinal,
                    content=chunk.content,
                    token_count=chunk.token_count,
                    section=chunk.section,
                    lexical_text=lexical_text,
                    embedding=embedding,
                )
            )
        await self.session.flush()
        return len(chunks)

    async def search_vector(self, query_embedding: list[float], *, limit: int = 20) -> list[RetrievalRow]:
        if self.dialect != "postgresql":
            return []
        distance = KnowledgeChunkRow.embedding.cosine_distance(query_embedding)
        result = await self.session.execute(
            select(KnowledgeChunkRow, KnowledgeDocumentRow, distance.label("distance"))
            .join(
                KnowledgeDocumentRow,
                KnowledgeDocumentRow.document_id == KnowledgeChunkRow.document_id,
            )
            .where(
                KnowledgeChunkRow.embedding.is_not(None),
                KnowledgeDocumentRow.visibility == "public",
            )
            .order_by(distance)
            .limit(limit)
        )
        rows: list[RetrievalRow] = []
        for chunk_row, document_row, dist in result.all():
            score = max(0.0, 1.0 - float(dist))
            rows.append(RetrievalRow(_chunk(chunk_row), _document(document_row), score))
        return rows

    async def search_lexical(self, query: str, *, limit: int = 20) -> list[RetrievalRow]:
        if self.dialect == "postgresql":
            vector = func.to_tsvector("simple", KnowledgeChunkRow.lexical_text)
            query_expr = func.plainto_tsquery("simple", query)
            rank = func.ts_rank(vector, query_expr)
            result = await self.session.execute(
                select(KnowledgeChunkRow, KnowledgeDocumentRow, rank.label("rank"))
                .join(
                    KnowledgeDocumentRow,
                    KnowledgeDocumentRow.document_id == KnowledgeChunkRow.document_id,
                )
                .where(
                    vector.op("@@")(query_expr),
                    KnowledgeDocumentRow.visibility == "public",
                )
                .order_by(rank.desc())
                .limit(limit)
            )
            return [
                RetrievalRow(_chunk(chunk_row), _document(document_row), float(score))
                for chunk_row, document_row, score in result.all()
            ]
        term = query.split()[0] if query.split() else query
        result = await self.session.execute(
            select(KnowledgeChunkRow, KnowledgeDocumentRow)
            .join(
                KnowledgeDocumentRow,
                KnowledgeDocumentRow.document_id == KnowledgeChunkRow.document_id,
            )
            .where(
                KnowledgeChunkRow.lexical_text.ilike(f"%{term}%"),
                KnowledgeDocumentRow.visibility == "public",
            )
            .limit(limit)
        )
        return [
            RetrievalRow(_chunk(chunk_row), _document(document_row), 0.0) for chunk_row, document_row in result.all()
        ]

    async def chunks_for_document(self, document_id: str) -> list[KnowledgeChunk]:
        result = await self.session.execute(
            select(KnowledgeChunkRow)
            .where(KnowledgeChunkRow.document_id == document_id)
            .order_by(KnowledgeChunkRow.ordinal)
        )
        return [_chunk(row) for row in result.scalars().all()]

    async def get_document(self, document_id: str) -> KnowledgeDocument | None:
        row = await self.session.get(KnowledgeDocumentRow, document_id)
        return _document(row) if row else None

    async def list_documents(self, *, limit: int = 200) -> list[KnowledgeDocument]:
        result = await self.session.execute(
            select(KnowledgeDocumentRow).order_by(KnowledgeDocumentRow.title).limit(limit)
        )
        return [_document(row) for row in result.scalars().all()]

    async def counts(self) -> tuple[int, int]:
        documents = int(await self.session.scalar(select(func.count(KnowledgeDocumentRow.document_id))) or 0)
        chunks = int(await self.session.scalar(select(func.count(KnowledgeChunkRow.chunk_id))) or 0)
        return documents, chunks

    async def delete_document(self, document_id: str) -> None:
        await self.session.execute(delete(KnowledgeDocumentRow).where(KnowledgeDocumentRow.document_id == document_id))
        await self.session.flush()

    async def chunk_text(self, chunk_id: str) -> tuple[KnowledgeChunk, KnowledgeDocument] | None:
        result = await self.session.execute(
            select(KnowledgeChunkRow, KnowledgeDocumentRow)
            .join(
                KnowledgeDocumentRow,
                KnowledgeDocumentRow.document_id == KnowledgeChunkRow.document_id,
            )
            .where(KnowledgeChunkRow.chunk_id == chunk_id)
        )
        row = result.first()
        if row is None:
            return None
        return _chunk(row[0]), _document(row[1])

    async def verify_pgvector(self) -> bool:
        if self.dialect != "postgresql":
            return False
        try:
            await self.session.execute(text("SELECT '[1,2,3]'::vector"))
            return True
        except Exception:
            return False
