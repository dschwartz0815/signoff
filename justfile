# Signoff cross-toolchain command runner.
# Run `just --list` to see every recipe.

set shell := ["bash", "-cu"]
set dotenv-load := true

default:
    @just --list

# ---------- setup ----------

# Install Python and TS workspace dependencies.
setup: setup-py setup-ts

setup-py:
    uv sync --all-packages --all-groups

setup-ts:
    pnpm install --frozen-lockfile || pnpm install

# Pull / build Docker images required for local dev.
setup-docker:
    docker compose build

# ---------- tests ----------

# Run every test suite across Python and TypeScript.
test: test-py test-ts

test-py:
    uv run pytest

test-py-core:
    uv run pytest packages/signoff-core

test-py-mcp:
    uv run pytest packages/signoff-mcp

test-py-code:
    uv run pytest packages/signoff-code

test-ts:
    pnpm -r test

test-ts-sdk:
    pnpm --filter @signoff/sdk test

# ---------- types ----------

typecheck: typecheck-py typecheck-ts

typecheck-py:
    uv run mypy

typecheck-ts:
    pnpm -r typecheck

# ---------- lint / format ----------

lint: lint-py lint-ts

lint-py:
    uv run ruff check .

lint-ts:
    pnpm exec biome check .

fmt: fmt-py fmt-ts

fmt-py:
    uv run ruff format .
    uv run ruff check --fix .

fmt-ts:
    pnpm exec biome format --write .

# Check-only variant: fails if formatting would change anything.
# Wired into CI so drift is caught on PR.
fmt-check: fmt-check-py fmt-check-ts

fmt-check-py:
    uv run ruff format --check .

fmt-check-ts:
    pnpm exec biome format .

# ---------- docker ----------

# Build every Dockerfile tracked by docker-compose (OSS stack).
# `--profile build-only` pulls in the sandbox image used by DockerRuntime,
# which is not started as a long-running service.
build-images:
    docker compose --profile build-only build

# Build every Dockerfile including cloud services.
build-images-all:
    docker compose --profile build-only -f docker-compose.yml -f docker-compose.cloud.yml build

# ---------- dev stacks ----------

# Run the OSS stack locally (MCP server + verifier sandbox image).
dev:
    docker compose up --build

# Run the full cloud stack for local dev.
cloud-dev:
    docker compose -f docker-compose.yml -f docker-compose.cloud.yml up --build

# Run the MCP server directly from Python source (no Docker).
mcp:
    uv run python -m signoff_mcp

# Run the MCP server via its published Docker image.
mcp-docker:
    docker compose up signoff-mcp --build

# ---------- CI ----------

# Re-export JSON schemas from the Pydantic models. Commit the result.
schemas:
    uv run python scripts/export_schemas.py

# Fail if committed schemas have drifted from signoff.models.
schemas-check:
    uv run python scripts/export_schemas.py --check

# Full simulation of CI: setup, lint, fmt-check, typecheck, schema drift, test, image builds.
ci: setup lint fmt-check typecheck schemas-check test build-images
