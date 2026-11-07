"""Hybrid retrieval with reciprocal-rank fusion and citations."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from conductor.config import Settings
from conductor.db.knowledge import KnowledgeRepository, RetrievalRow
from conductor.knowledge.chunking import lexical_query
from conductor.knowledge.embeddings import EmbeddingClient
from conductor.models.common import utcnow
from conductor.models.knowledge import SearchHit, SearchResponse
from conductor.security.crypto import Vault  # noqa: F401  (typing convenience)

RRF_K = 60


class KnowledgeSearch:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        embeddings: EmbeddingClient | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.repo = KnowledgeRepository(session)
        self.embeddings = embeddings or EmbeddingClient(settings)

    async def search(self, query: str, *, limit: int | None = None) -> SearchResponse:
        limit = limit or self.settings.knowledge.top_k
        query = query.strip()
        if not query:
            return SearchResponse(query=query, hits=[], performed_at=utcnow(), retrieval="empty")
        lexical_rows = await self.repo.search_lexical(lexical_query(query), limit=limit * 3)
        vector_rows: list[RetrievalRow] = []
        retrieval = "lexical"
        try:
            embeddings = await self.embeddings.embed([query])
            vector_rows = await self.repo.search_vector(embeddings[0], limit=limit * 3)
            if vector_rows:
                retrieval = "hybrid-rrf"
        except Exception:
            # Retrieval must degrade to lexical rather than fail the chat.
            vector_rows = []
            retrieval = "lexical"
        fused: dict[str, float] = {}
        by_id: dict[str, RetrievalRow] = {}
        for rank, row in enumerate(lexical_rows, start=1):
            key = row.chunk.chunk_id
            fused[key] = fused.get(key, 0.0) + 1.0 / (RRF_K + rank)
            by_id[key] = row
        for rank, row in enumerate(vector_rows, start=1):
            key = row.chunk.chunk_id
            fused[key] = fused.get(key, 0.0) + 1.0 / (RRF_K + rank)
            by_id.setdefault(key, row)
        ordered = sorted(fused.items(), key=lambda item: item[1], reverse=True)[:limit]
        hits: list[SearchHit] = []
        for chunk_id, score in ordered:
            row = by_id[chunk_id]
            hits.append(
                SearchHit(
                    chunk_id=row.chunk.chunk_id,
                    document_id=row.document.document_id,
                    title=row.document.title,
                    section=row.chunk.section or row.document.section,
                    source_url=row.document.source_url,
                    version=row.document.version,
                    snippet=_snippet(row.chunk.content, query),
                    score=round(score, 6),
                    lexical_score=next(
                        (item.score for item in lexical_rows if item.chunk.chunk_id == chunk_id),
                        0.0,
                    ),
                    vector_score=next(
                        (item.score for item in vector_rows if item.chunk.chunk_id == chunk_id),
                        0.0,
                    ),
                    chunk_ordinal=row.chunk.ordinal,
                )
            )
        return SearchResponse(
            query=query,
            hits=hits,
            performed_at=datetime.now(tz=utcnow().tzinfo),
            retrieval=retrieval,
        )

    async def document(self, document_id: str):
        return await self.repo.get_document(document_id)

    async def list_documents(self):
        return await self.repo.list_documents()


def _snippet(content: str, query: str, *, length: int = 360) -> str:
    compact = " ".join(content.split())
    if len(compact) <= length:
        return compact
    lowered = compact.lower()
    positions = [lowered.find(token) for token in query.lower().split() if token]
    positions = [pos for pos in positions if pos >= 0]
    start = max(0, (min(positions) if positions else 0) - length // 3)
    return compact[start : start + length] + "…"
