"""Portal API entry point."""

from __future__ import annotations

import os

import uvicorn


def run() -> None:
    uvicorn.run(
        "conductor.api.app:create_app",
        factory=True,
        host=os.environ.get("CONDUCTOR_BIND_HOST", "0.0.0.0"),
        port=int(os.environ.get("CONDUCTOR_BIND_PORT", "8000")),
        log_level=os.environ.get("CONDUCTOR_LOG_LEVEL", "info").lower(),
        proxy_headers=True,
        forwarded_allow_ips=os.environ.get("CONDUCTOR_FORWARDED_ALLOW_IPS", ""),
        access_log=False,
        workers=1,
    )


if __name__ == "__main__":  # pragma: no cover
    run()
