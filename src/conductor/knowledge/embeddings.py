"""Embedding client for the local BGE-M3 worker.

Production uses the HTTP embedding service. A deterministic mock belongs only to
tests and the synthetic demo; production configuration rejects it.
"""

from __future__ import annotations

import hashlib
import math

import httpx

from conductor.config import Settings
from conductor.errors import CapabilityUnsupported, ConfigurationError

MOCK_MODEL = "mock-deterministic-v1"


class EmbeddingClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.model = settings.knowledge.embedding_model
        self.dimension = settings.knowledge.embedding_dimension
        self._url = settings.knowledge.embedding_base_url

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if self.settings.knowledge.mock_embeddings:
            return [mock_embedding(text, self.dimension) for text in texts]
        if not self._url:
            raise CapabilityUnsupported("the embedding service is not configured; lexical retrieval only")
        headers = {"Accept": "application/json"}
        key = self.settings.embedding_api_key()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    self._url.rstrip("/") + "/embed",
                    json={"texts": texts, "model": self.model},
                    headers=headers,
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise CapabilityUnsupported("the embedding service is unavailable; lexical retrieval only") from exc
        embeddings = payload.get("embeddings")
        model = payload.get("model")
        if model and model != self.model:
            raise ConfigurationError("the embedding service returned a different model")
        if not isinstance(embeddings, list) or len(embeddings) != len(texts):
            raise CapabilityUnsupported("the embedding service returned an invalid response")
        for vector in embeddings:
            if not isinstance(vector, list) or len(vector) != self.dimension:
                raise ConfigurationError("the embedding dimension does not match the index; rebuild the index")
        return [[float(value) for value in vector] for vector in embeddings]


def mock_embedding(text: str, dimension: int) -> list[float]:
    """Deterministic hashed embedding for tests and the public demo only."""

    vector = [0.0] * dimension
    tokens = text.lower().split()
    if not tokens:
        tokens = [text[:64]]
    for token in tokens:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "big") % dimension
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]
