# syntax=docker/dockerfile:1.7
# Generic default sandbox image for signoff-runtime-docker.
#
# Minimal — just a long-running holder process in a non-root user's
# shell. Specialised packs (signoff-code, etc.) ship their own
# sandbox images preloaded with the tools they need; this image is
# the zero-config default for verifiers that only run shell commands.
#
# Built and published as ghcr.io/signoff/generic-sandbox:<version> /
# :latest by .github/workflows/publish-sandbox-images.yml, signed
# with cosign at publish time, scanned with trivy.
FROM python:3.12-slim-bookworm
RUN groupadd --system --gid 10001 signoff \
 && useradd --system --uid 10001 --gid signoff --no-create-home signoff \
 && mkdir -p /workspace \
 && chown signoff:signoff /workspace
WORKDIR /workspace
USER signoff:signoff
# The runtime uses `sleep infinity` as a holder so it can stream
# `docker exec` invocations; override via create_container cmd if a
# future runtime wants something else.
CMD ["sleep", "infinity"]
