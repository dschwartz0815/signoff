# syntax=docker/dockerfile:1.7
# Placeholder for the hosted API image. Real implementation (FastAPI) lands
# in Phase 2. Multi-stage shape matches §9.4 so the convention is enforced
# consistently from day one.

# ---- builder ----
FROM python:3.12-slim-bookworm AS builder
WORKDIR /build
# Phase-2 build steps will land here (uv sync --package cloud-api, etc.).
RUN python -c "print('cloud-api build stage placeholder')"

# ---- runtime ----
FROM python:3.12-slim-bookworm AS runtime
RUN groupadd --system --gid 10001 signoff \
 && useradd --system --uid 10001 --gid signoff --no-create-home signoff
WORKDIR /app
COPY --from=builder /build/ /app/
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER signoff
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "print('ok')" || exit 1
CMD ["python", "-c", "print('cloud-api placeholder: Phase 2 not yet implemented')"]
