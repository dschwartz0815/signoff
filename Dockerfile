# syntax=docker/dockerfile:1.7
#
# Root Dockerfile — a workspace-aware base image used for CI and as a
# convenient entry point for consumers who want every Signoff Python package
# preinstalled. Individual services have their own tighter Dockerfiles under
# packages/<name>/Dockerfile.

# ---- builder ----
FROM python:3.12-slim-bookworm AS builder
WORKDIR /build
RUN pip install --no-cache-dir uv==0.11.*

COPY pyproject.toml uv.lock* ./
COPY packages/ ./packages/

# --no-editable: install workspace members as real packages, not editable
# .pth pointers into /build/ (which doesn't exist in the runtime stage).
RUN uv sync --frozen --no-dev --no-editable --all-packages \
 || uv sync --no-dev --no-editable --all-packages

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
HEALTHCHECK --interval=30s --timeout=3s \
  CMD python -c "import signoff, signoff_mcp, signoff_code" || exit 1

# Default to a smoke-check so standalone runs have observable output.
# Services (MCP, code sandbox, cloud/*) override CMD via compose or args.
CMD ["python", "-c", "import signoff, signoff_mcp, signoff_code; print('signoff workspace image ready')"]
