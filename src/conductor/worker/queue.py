"""Agent task queue."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from procrastinate import App, PsycopgConnector
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conductor.agent.llm import ModelGateway
from conductor.agent.runner import AgentRunner
from conductor.config import Settings
from conductor.execution.client import ExecutionClient

logger = logging.getLogger(__name__)


async def _ensure_queue_schema(app: App) -> None:
    """Apply the Procrastinate schema once, tolerating a concurrent starter.

    Procrastinate's schema SQL is not idempotent. The queue schema is shared by
    the API, execution service, and worker, so startup must not fail merely
    because another service created it first.
    """

    try:
        await app.schema_manager.apply_schema_async()
    except Exception as exc:
        text = str(exc).lower() + " " + str(getattr(exc, "__cause__", "")).lower()
        if "already exists" not in text:
            raise


def _psycopg_dsn(database_url: str) -> str:
    if database_url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + database_url[len("postgresql+asyncpg://") :]
    if database_url.startswith("postgresql+psycopg://"):
        return "postgresql://" + database_url[len("postgresql+psycopg://") :]
    if database_url.startswith("postgresql://"):
        return database_url
    raise ValueError("Procrastinate requires a PostgreSQL database URL")


@dataclass
class AgentQueue:
    app: App
    task: Any
    _opened: bool = False
    _worker: asyncio.Task | None = None

    async def open(self, *, start_worker: bool = False, concurrency: int = 2) -> None:
        await self.app.open_async()
        await _ensure_queue_schema(self.app)
        self._opened = True
        if start_worker:
            self._worker = asyncio.create_task(
                self.app.run_worker_async(
                    queues=["agent"],
                    concurrency=concurrency,
                    wait=True,
                    install_signal_handlers=False,
                    listen_notify=True,
                ),
                name="conductor-agent-worker",
            )

    async def enqueue(self, run_id: str) -> None:
        if not self._opened:
            raise RuntimeError("the agent queue is not open")
        await self.task.defer_async(run_id=run_id)

    async def close(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except (asyncio.CancelledError, Exception):
                pass
            self._worker = None
        if self._opened:
            await self.app.close_async()
            self._opened = False


def build_agent_queue(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    execution: ExecutionClient,
    model: ModelGateway,
    *,
    checkpointer=None,
) -> AgentQueue:
    app = App(connector=PsycopgConnector(conninfo=_psycopg_dsn(settings.database.url)))
    runner = AgentRunner(settings, session_factory, execution, model, checkpointer=checkpointer)

    @app.task(name="conductor.run_agent", queue="agent", retry=1)
    async def run_agent(run_id: str) -> None:  # pragma: no cover - integration path
        await runner.run(run_id)

    return AgentQueue(app=app, task=run_agent)
