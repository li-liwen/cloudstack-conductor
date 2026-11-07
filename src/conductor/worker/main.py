"""Agent worker process entry point."""

from __future__ import annotations

import asyncio
import signal
from contextlib import AsyncExitStack

from conductor.agent.llm import ModelGateway
from conductor.config import Settings
from conductor.db.session import build_engine, build_session_factory
from conductor.execution.client import ExecutionClient
from conductor.logging import configure_logging
from conductor.worker.queue import build_agent_queue


async def run() -> None:
    settings = Settings()
    if settings.environment == "production":
        settings.validate_for_runtime()
    configure_logging(settings.log_level, service="conductor-worker")
    engine = build_engine(settings)
    session_factory = build_session_factory(engine)
    execution = ExecutionClient(settings)
    model = ModelGateway(settings)
    stack = AsyncExitStack()
    checkpointer = None
    dsn = settings.database.url
    if dsn.startswith("postgresql+asyncpg://"):
        dsn = "postgresql://" + dsn[len("postgresql+asyncpg://") :]
    if dsn.startswith("postgresql"):
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        checkpointer = await stack.enter_async_context(AsyncPostgresSaver.from_conn_string(dsn))
        await checkpointer.setup()
    queue = build_agent_queue(settings, session_factory, execution, model, checkpointer=checkpointer)
    await queue.open(start_worker=True, concurrency=2)
    stop = asyncio.Event()

    def _stop(*_: object) -> None:
        stop.set()

    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(signal_name, _stop)
        except NotImplementedError:  # pragma: no cover - Windows
            pass
    try:
        await stop.wait()
    finally:
        await queue.close()
        await stack.aclose()
        await execution.close()
        await engine.dispose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":  # pragma: no cover
    main()
