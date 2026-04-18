# syntax=docker/dockerfile:1.7
# Signoff MCP server image. Built from the workspace root so we can share
# the uv lock across member packages.

# ---- builder ----
FROM python:3.12-slim-bookworm AS builder
WORKDIR /build
RUN pip install --no-cache-dir uv==0.11.*

COPY pyproject.toml uv.lock* ./
COPY packages/signoff-core ./packages/signoff-core
COPY packages/signoff-mcp ./packages/signoff-mcp

# --no-editable installs workspace members as real packages. Without it
# uv writes .pth files that point to /build/..., which doesn't exist in
# the runtime stage.
RUN uv sync --frozen --no-dev --no-editable --package signoff-mcp \
 || uv sync --no-dev --no-editable --package signoff-mcp

# ---- runtime ----
FROM python:3.12-slim-bookworm AS runtime
RUN groupadd --system --gid 10001 signoff \
 && useradd --system --uid 10001 --gid signoff --no-create-home signoff

WORKDIR /app
COPY --from=builder /build/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER signoff
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=3s \
  CMD python -m signoff_mcp --health || exit 1

# Default to stdio MCP transport; override via env/args for HTTP transport.
CMD ["python", "-m", "signoff_mcp"]
