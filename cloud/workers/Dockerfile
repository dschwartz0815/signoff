# syntax=docker/dockerfile:1.7
# Placeholder for the cloud-worker image. Real implementation (Celery /
# Dramatiq) lands in Phase 2.

# ---- builder ----
FROM python:3.12-slim-bookworm AS builder
WORKDIR /build
RUN python -c "print('cloud-worker build stage placeholder')"

# ---- runtime ----
FROM python:3.12-slim-bookworm AS runtime
RUN groupadd --system --gid 10001 signoff \
 && useradd --system --uid 10001 --gid signoff --no-create-home signoff
WORKDIR /app
COPY --from=builder /build/ /app/
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER signoff
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "print('ok')" || exit 1
CMD ["python", "-c", "print('cloud-worker placeholder: Phase 2 not yet implemented')"]
