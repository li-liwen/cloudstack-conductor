"""Local model gateway client.

Only the configured model alias is requested. There is no fallback to an
external model. The model never decides authorization, prices, or approval.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

from conductor.config import Settings
from conductor.errors import CapabilityUnsupported, RateLimitExceeded

logger = logging.getLogger(__name__)


class ModelGateway:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._semaphore = asyncio.Semaphore(settings.llm.max_concurrency)
        self._queued = 0
        self._lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        return bool(self.settings.llm.base_url)

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        if not self.configured:
            raise CapabilityUnsupported("the model gateway is not configured")
        async with self._lock:
            if self._queued >= self.settings.llm.max_queue:
                raise RateLimitExceeded("the model queue is full; retry shortly")
            self._queued += 1
        try:
            try:
                await asyncio.wait_for(self._semaphore.acquire(), timeout=10.0)
            except TimeoutError as exc:
                raise RateLimitExceeded("the model is busy; retry shortly") from exc
            try:
                return await self._request(
                    messages,
                    temperature=(self.settings.llm.temperature if temperature is None else temperature),
                    max_tokens=max_tokens or self.settings.llm.max_output_tokens,
                    response_format=response_format,
                )
            finally:
                self._semaphore.release()
        finally:
            async with self._lock:
                self._queued -= 1

    async def _request(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        max_tokens: int,
        response_format: dict[str, Any] | None,
    ) -> str:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        key = self.settings.llm_api_key()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        payload: dict[str, Any] = {
            "model": self.settings.llm.model_alias,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if response_format:
            payload["response_format"] = response_format
        base_url = self.settings.llm.base_url
        if not base_url:
            raise CapabilityUnsupported("the model gateway is not configured")
        try:
            async with httpx.AsyncClient(timeout=self.settings.llm.request_timeout_seconds) as client:
                response = await client.post(
                    base_url.rstrip("/") + "/chat/completions",
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("model gateway failed: %s", type(exc).__name__)
            raise CapabilityUnsupported("the approved local model is unavailable") from exc
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise CapabilityUnsupported("the model gateway returned an unexpected response") from exc
        if not isinstance(content, str):
            raise CapabilityUnsupported("the model gateway returned no text")
        return content

    async def json_chat(self, messages: list[dict[str, str]]) -> dict[str, Any] | None:
        try:
            text = await self.chat(messages, response_format={"type": "json_object"})
        except CapabilityUnsupported:
            return None
        return extract_json(text)


def extract_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text
        if text.startswith("json"):
            text = text[4:]
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            value = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None
