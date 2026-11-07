"""Local BGE-M3 embedding worker.

Runs on the private application network. Never exposed publicly. The model is
loaded lazily so health checks can report readiness without blocking startup.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

from conductor.config import Settings, read_secret_file
from conductor.logging import configure_logging

logger = logging.getLogger(__name__)


class EmbedRequest(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=64)
    model: str


class EmbedResponse(BaseModel):
    embeddings: list[list[float]]
    model: str
    dimension: int


class EmbeddingModel:
    """Thread-safe lazy loader for BGE-M3."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._model: Any = None
        self._lock = threading.Lock()
        self.failed = False

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def load(self) -> Any:
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is not None:
                return self._model
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:  # pragma: no cover - optional heavy extra
                self.failed = True
                raise RuntimeError("sentence-transformers is not installed; install the embeddings extra") from exc
            try:
                self._model = SentenceTransformer(self.name, trust_remote_code=False)
            except Exception:
                self.failed = True
                logger.exception("embedding model failed to load")
                raise
            return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        model = self.load()
        vectors = model.encode(
            texts,
            normalize_embeddings=True,
            batch_size=8,
            show_progress_bar=False,
        )
        return [[float(value) for value in vector] for vector in vectors]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    configure_logging(settings.log_level, service="conductor-embeddings")
    model = EmbeddingModel(settings.knowledge.embedding_model)
    app = FastAPI(title="CloudStack Conductor Embeddings", docs_url=None, openapi_url=None)
    app.state.model = model
    app.state.settings = settings
    app.state.lock = asyncio.Lock()

    def _authorize(token: str | None) -> None:
        file_path = settings.knowledge.embedding_api_key_file
        if not file_path:
            return
        expected = read_secret_file(file_path, name="the embedding service token")
        if not token or token.removeprefix("Bearer ").strip() != expected:
            raise HTTPException(status_code=401, detail="unauthorized")

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {
            "status": "ok",
            "model": settings.knowledge.embedding_model,
            "loaded": model.loaded,
        }

    @app.get("/readyz")
    async def readyz() -> dict[str, Any]:
        if model.failed:
            return {"status": "failed"}
        return {"status": "loading" if not model.loaded else "ready"}

    @app.post("/embed", response_model=EmbedResponse)
    async def embed(
        body: EmbedRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> EmbedResponse:
        _authorize(authorization)
        if body.model != settings.knowledge.embedding_model:
            raise HTTPException(status_code=400, detail="unsupported model")
        async with request.app.state.lock:
            try:
                vectors = await asyncio.to_thread(model.embed, body.texts)
            except RuntimeError as exc:
                raise HTTPException(status_code=503, detail="embedding model unavailable") from exc
        dimension = len(vectors[0]) if vectors else settings.knowledge.embedding_dimension
        return EmbedResponse(embeddings=vectors, model=settings.knowledge.embedding_model, dimension=dimension)

    return app
