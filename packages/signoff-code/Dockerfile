# syntax=docker/dockerfile:1.7
# ``signoff-code`` sandbox image.
#
# Built and published as ``ghcr.io/dschwartz0815/signoff/code-sandbox:<version>`` /
# ``:latest`` by ``.github/workflows/publish-sandbox-images.yml``,
# signed with cosign at publish time, scanned with trivy.
#
# What lives here: the tools this pack's verifiers invoke via
# ``ctx.exec`` — pytest, mypy, ruff, coverage, plus git + patch for
# diff application. NOT the signoff-code Python package itself —
# verifier logic runs in the harness process, not inside the
# sandbox (see CLAUDE.md §8.1). Keeping the image tool-only keeps
# it small and reproducible.
#
# Regenerating when tools update:
#   1. Bump the pinned versions in the ``pip install`` line below.
#   2. Rebuild locally with
#      ``docker build -t signoff/code-sandbox:dev packages/signoff-code``.
#   3. Run ``pytest -m docker packages/signoff-code`` to smoke the image.
#   4. Update ``tests/integration/test_live_docker.py`` if a
#      version bump changed the output format we parse.

FROM python:3.12-slim-bookworm AS runtime

# git + patch: workspace materialisation runs ``patch -p1`` inside
# the container for Workspace.apply_diff and `git` for future
# base-revision seeding. ca-certificates: HTTPS fetches from the
# verifier's ctx.exec calls (pip, etc.) if a future verifier runs them.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      git \
      patch \
      ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Verifier toolchain, pinned so the image is reproducible.
# ``--no-compile`` keeps the layer small; pytest / mypy / ruff
# bytecode regenerates on first run inside the ephemeral container.
RUN pip install --no-cache-dir --no-compile \
      'pytest==8.3.*' \
      'mypy==1.13.*' \
      'ruff==0.7.*' \
      'coverage==7.6.*'

# Non-root user matching CLAUDE.md §9.4 and the DockerRuntime policy.
RUN groupadd --system --gid 10001 signoff \
 && useradd --system --uid 10001 --gid signoff --no-create-home signoff \
 && mkdir -p /workspace \
 && chown signoff:signoff /workspace

WORKDIR /workspace
USER signoff:signoff

# Holder process — DockerRuntime keeps the container alive and runs
# commands via ``docker exec``. Verifier invocations override this
# implicitly (they exec inside the already-running container); the
# CMD only matters for ``docker run`` against this image.
CMD ["sleep", "infinity"]
