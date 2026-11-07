"""Knowledge index and retrieval contracts."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from conductor.models.common import ensure_utc


class KnowledgeDocument(BaseModel):
    document_id: str
    title: str
    version: str
    source_url: str
    section: str | None = None
    content_hash: str
    language: str = "en"
    license_note: str | None = None
    ingested_at: datetime
    visibility: str = "public"

    @field_validator("ingested_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class KnowledgeChunk(BaseModel):
    chunk_id: str
    document_id: str
    ordinal: int
    content: str
    token_count: int
    section: str | None = None


class SearchHit(BaseModel):
    chunk_id: str
    document_id: str
    title: str
    section: str | None = None
    source_url: str
    version: str
    snippet: str
    score: float
    lexical_score: float = 0.0
    vector_score: float = 0.0
    chunk_ordinal: int = 0


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=8, ge=1, le=20)
    language: str | None = None


class SearchResponse(BaseModel):
    query: str
    hits: list[SearchHit] = Field(default_factory=list)
    performed_at: datetime
    retrieval: str = "hybrid-rrf"

    @field_validator("performed_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class IngestReport(BaseModel):
    run_id: str
    documents: int
    chunks: int
    skipped: int
    started_at: datetime
    finished_at: datetime
    source: str
    version: str

    @field_validator("started_at", "finished_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)
