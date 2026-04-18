# syntax=docker/dockerfile:1.7
# Placeholder for the cloud-dashboard image. Real implementation (Next.js)
# lands in Phase 2.

# ---- builder ----
FROM node:22-slim AS builder
WORKDIR /build
RUN node -e "console.log('cloud-dashboard build stage placeholder')"

# ---- runtime ----
FROM node:22-slim AS runtime
RUN groupadd --system --gid 10001 signoff \
 && useradd --system --uid 10001 --gid signoff --no-create-home signoff
WORKDIR /app
COPY --from=builder /build/ /app/
USER signoff
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=3s CMD node -e "console.log('ok')" || exit 1
CMD ["node", "-e", "console.log('cloud-dashboard placeholder: Phase 2 not yet implemented')"]
