"""MCP service entry point."""

from __future__ import annotations

import os

import uvicorn

from conductor.config import Settings
from conductor.db.session import build_engine, build_session_factory
from conductor.execution.client import ExecutionClient
from conductor.logging import configure_logging
from conductor.mcp.server import create_mcp


def build_asgi_app():
    settings = Settings()
    settings.validate_for_runtime()
    configure_logging(settings.log_level, service="conductor-mcp")
    engine = build_engine(settings)
    session_factory = build_session_factory(engine)
    execution = ExecutionClient(settings)
    mcp = create_mcp(settings, session_factory, execution)
    app = mcp.http_app(path="/mcp", transport="streamable-http", stateless_http=False)
    app.state.engine = engine
    app.state.execution_client = execution
    return app


def run() -> None:
    app = build_asgi_app()
    uvicorn.run(
        app,
        host=os.environ.get("CONDUCTOR_BIND_HOST", "0.0.0.0"),
        port=int(os.environ.get("CONDUCTOR_BIND_PORT", "8083")),
        log_level=os.environ.get("CONDUCTOR_LOG_LEVEL", "info").lower(),
        access_log=False,
        proxy_headers=True,
    )


if __name__ == "__main__":  # pragma: no cover
    run()
