# syntax=docker/dockerfile:1.7
# Sandbox image for signoff-code verifiers. DockerRuntime (Phase 1)
# spawns ephemeral containers from this image to execute untrusted test
# suites, linters, and type checkers against agent-produced code.

# ---- builder ----
FROM python:3.12-slim-bookworm AS builder
WORKDIR /build
RUN pip install --no-cache-dir uv==0.11.*

COPY pyproject.toml uv.lock* ./
COPY packages/signoff-core ./packages/signoff-core
COPY packages/signoff-code ./packages/signoff-code

# --no-editable: install as a real package, not an editable .pth pointer
# into /build/ (which doesn't exist in the runtime stage).
RUN uv sync --frozen --no-dev --no-editable --package signoff-code \
 || uv sync --no-dev --no-editable --package signoff-code

# ---- runtime ----
FROM python:3.12-slim-bookworm AS runtime

# Test-runner toolchain preinstalled so verifiers don't pay install cost
# per invocation. Language-specific toolchains (node, go, rust) will be
# layered on as the pack expands.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 10001 signoff \
 && useradd --system --uid 10001 --gid signoff --home /workspace signoff \
 && mkdir -p /workspace \
 && chown signoff:signoff /workspace

WORKDIR /workspace
COPY --from=builder /build/.venv /opt/signoff/.venv
ENV PATH="/opt/signoff/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER signoff
HEALTHCHECK --interval=30s --timeout=3s \
  CMD python -c "import signoff_code; print(signoff_code.__version__)" || exit 1

# Sandbox default: no-op so the image can be warmed without side effects.
# DockerRuntime overrides CMD for each verifier invocation.
CMD ["python", "-c", "import signoff_code; print(f'signoff-code sandbox {signoff_code.__version__} ready')"]
