# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:0.9.17 AS uv

FROM python:3.12-slim AS builder
COPY --from=uv /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim AS runtime
LABEL org.opencontainers.image.title="pytestgen" \
      org.opencontainers.image.description="LLM-assisted pytest test generator with concolic-lite path search" \
      org.opencontainers.image.source="https://github.com/ark2016/mipt-9-pytestGen"
RUN useradd --create-home --uid 1000 app
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTESTGEN_CACHE_DIR=/work/.pytestgen/cache
WORKDIR /work
USER app
ENTRYPOINT ["pytestgen"]
CMD ["--help"]
