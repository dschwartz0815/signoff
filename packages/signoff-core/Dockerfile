# syntax=docker/dockerfile:1.7
# Image for CI verification of signoff-core in isolation — a minimal
# runtime with only the core engine installed.

# ---- builder ----
FROM python:3.12-slim-bookworm AS builder
WORKDIR /build
RUN pip install --no-cache-dir uv==0.11.*

COPY pyproject.toml uv.lock* ./
COPY packages/signoff-core ./packages/signoff-core

# --no-editable: install as a real package, not an editable .pth pointer
# into /build/ (which doesn't exist in the runtime stage).
RUN uv sync --frozen --no-dev --no-editable --package signoff-core \
 || uv sync --no-dev --no-editable --package signoff-core

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
  CMD python -c "import signoff; print(signoff.__version__)" || exit 1

# Smoke-check the installation when the container runs standalone.
CMD ["python", "-c", "import signoff; print(f'signoff-core {signoff.__version__}')"]
