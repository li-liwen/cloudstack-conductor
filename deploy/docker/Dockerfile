# Multi-stage build (no remote Dockerfile frontend; base images are pinned in the release process).
# Multi-stage build. The build context is the repository root; .dockerignore
# excludes private files, tests, docs, caches, and local state.

FROM node:22-alpine AS portal
WORKDIR /portal
COPY apps/portal/package.json apps/portal/package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci --no-audit --no-fund; else npm install --no-audit --no-fund; fi
COPY apps/portal/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_NO_CACHE=1
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /uvx /bin/

RUN groupadd --gid 10001 conductor \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin conductor \
    && mkdir -p /app /data/mcp \
    && chown -R conductor:conductor /app /data

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
COPY deploy ./deploy
COPY demo ./demo
RUN uv sync --frozen --no-dev \
    && chown -R conductor:conductor /app

COPY --from=portal --chown=conductor:conductor /portal/dist ./portal

ENV PATH="/app/.venv/bin:$PATH" \
    CONDUCTOR_PORTAL_STATIC_DIR=/app/portal

USER conductor
EXPOSE 8000 8001 8083 8085
CMD ["conductor-api"]
