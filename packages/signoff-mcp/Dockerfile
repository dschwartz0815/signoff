# syntax=docker/dockerfile:1.7
# Signoff MCP server image. Built from the workspace root so we can share
# the uv lock across member packages.
#
# Run with a mounted config:
#   docker run -v $(pwd)/signoff.yaml:/app/signoff.yaml -p 8765:8765 \
#     signoff-mcp:dev
#
# The default CMD exposes the HTTP+SSE transport on :8765; use --transport
# stdio for embedding in a parent MCP client process.

# ---- builder ----
# WORKDIR is /app in both stages so the venv's generated console-script
# shebangs (e.g. /app/.venv/bin/python) resolve correctly at runtime.
FROM python:3.12-slim-bookworm AS builder
WORKDIR /app
RUN pip install --no-cache-dir uv==0.11.*

COPY pyproject.toml uv.lock* ./
COPY packages/signoff-core ./packages/signoff-core
COPY packages/signoff-mcp ./packages/signoff-mcp
# signoff-http + signoff-judge + signoff-runtime-docker + signoff-code
# bundled so Harness.from_config_path can route to the real
# HttpxClient / AnthropicJudge / OpenAIJudge / DockerRuntime and
# discover signoff-code's verifiers via entry points without an extra
# install step at deployment time. The runtime-docker package needs
# the Docker socket to spawn sibling containers at runtime — see
# docs/deployment.md "Running with DockerRuntime" for the security
# tradeoffs.
COPY packages/signoff-http ./packages/signoff-http
COPY packages/signoff-judge ./packages/signoff-judge
COPY packages/signoff-runtime-docker ./packages/signoff-runtime-docker
COPY packages/signoff-code ./packages/signoff-code

# --no-editable installs workspace members as real packages. Without it
# uv writes .pth files that point to /app/..., which survives the copy.
RUN uv sync --frozen --no-dev --no-editable \
      --package signoff-mcp --package signoff-http --package signoff-judge \
      --package signoff-runtime-docker --package signoff-code \
 || uv sync --no-dev --no-editable \
      --package signoff-mcp --package signoff-http --package signoff-judge \
      --package signoff-runtime-docker --package signoff-code

# ---- runtime ----
FROM python:3.12-slim-bookworm AS runtime
RUN groupadd --system --gid 10001 signoff \
 && useradd --system --uid 10001 --gid signoff --no-create-home signoff

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER signoff
EXPOSE 8765
# HEALTHCHECK probes /health on the running HTTP server. Matches the
# default CMD below; override HEALTHCHECK if you change the port.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD signoff-mcp --health --transport http --port 8765 || exit 1

# Containers default to HTTP on :8765 so other services can reach them.
# For embedded stdio use, override with --transport stdio.
CMD ["signoff-mcp", "--transport", "http", "--host", "0.0.0.0", "--port", "8765", \
     "--config", "/app/signoff.yaml"]
