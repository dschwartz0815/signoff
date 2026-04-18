# Deployment & Self-Hosting

**Status:** Phase 0 stub. Full self-hosting guide lands alongside the Phase 2 hosted alpha.

Docker image and compose conventions: [`CLAUDE.md`](../CLAUDE.md) §9 — minimal multi-stage builds, non-root users, `cosign`-signed releases, `trivy` CI gate. Local dev stack: `just dev` (OSS) or `just cloud-dev` (with Postgres + Redis + cloud services).
