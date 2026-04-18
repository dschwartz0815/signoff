# cloud/

Hosted service source. See [`CLAUDE.md`](../CLAUDE.md) §6 for the **cloud directory and split policy**.

**Phase 0–2:** lives in this repo; scaffolding only, no code. The API, workers, dashboard, billing, audit, and infra directories exist so the boundary between OSS and cloud is visible from day one.

**Phase 2+ (after first paying customer / first non-founder cloud contributor / genuine secrets):** split out to a private `signoff/cloud` repo via `git filter-repo --path cloud/`. At that point `cloud/*` consumes the OSS packages as published artifacts.

## Discipline

- `cloud/*` MAY import `packages/signoff-*` through their public API only.
- `packages/*` MUST NOT import anything from `cloud/`. Ever.
- No secrets in this directory. `.env.example` files only.
