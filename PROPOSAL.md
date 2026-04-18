# Signoff

**Agents make claims. Signoff makes them prove it.**

A verification layer for AI agents. Sits between an agent and its "done" claim, runs pluggable checks against the deliverable, and returns either a pass or a structured failure the agent can retry against — before any human sees the output.

Ships as three aligned surfaces from a single core: a Python library, an MCP server, and a hosted service.

---

## 1. Executive Summary

AI agents generate output quickly. The bottleneck has moved from *writing* to *verifying*. Engineers, researchers, support teams, and analysts now spend the majority of their agent-interaction time checking work the agent claimed was complete — catching fabricated citations, broken imports, hallucinated policies, and confident-but-wrong analysis.

No standard verification layer exists. Every team hand-rolls their own partial checks inside their agent framework, or skips verification entirely and absorbs the downstream cost.

Signoff is the missing layer. It defines a simple protocol — agent submits a deliverable and its embedded claims, a harness runs registered verifiers, the agent either gets signed off or gets back a structured feedback packet it can act on. The protocol is transport-agnostic: the same core engine runs as an importable library, an MCP server that any modern agent client can call, and a hosted service for teams that need scale, audit logs, and compliance.

The primitive is simple. The ecosystem it enables — verifier packs for research, support, sales, data analysis, legal, code, and every other domain where agents operate — is where the durable value lives.

---

## 2. Problem Statement

Two simultaneous shifts created the gap Signoff fills:

**The work has moved.** As GitHub's CEO put it in late 2025, the most advanced developers have moved from writing code to architecting and verifying the implementation work that is carried out by AI agents. The same shift is underway in every domain agents have entered — research, support, sales, analytics, legal, operations. The bottleneck is no longer generation; it's trust.

**Observability showed what failed, not whether to trust it.** The 2026 AI tooling market is crowded with platforms that trace agent runs after the fact (Braintrust, Langfuse, Arize, LangSmith, Galileo, Latitude, Maxim, Fiddler). These tools excel at post-hoc debugging. None of them act as a pre-commit gate. The agent still declares itself done, the bad output still ships, and observability catches up hours later.

The tactical pain these gaps produce:

- **Coding agents ship broken work with confidence.** Hallucinated imports, wrong function signatures, tests that pass for the wrong reason. Debugging time eclipses the time saved by generation.
- **Research agents fabricate citations.** Plausible URLs that 404, real URLs whose content doesn't support the claim, verbatim quotes that don't exist in the source, numbers that don't match the cited report.
- **Support agents invent policies.** Refund promises outside policy, misstated SLAs, missing required disclaimers, wrong product facts.
- **Sales agents hallucinate personalization.** "I noticed you work at Stripe" when the prospect works at Block. Fabricated shared connections. False ROI claims.
- **Data agents miscompute confidently.** Aggregations that don't match the underlying data, charts that don't match their captions, filters applied silently.

Every team building agents encounters these failure modes, rebuilds ad-hoc checks in their own framework, and still ships partial coverage. There is a clear opening for a standard verification layer owned by no single framework.

---

## 3. Product Vision

Signoff becomes the default verification layer for agent systems. In the same way Sentry became the default error capture layer and OpenTelemetry became the default trace protocol, Signoff defines *how agents prove their work* — and provides the reference implementation across every surface where agents live.

The long-term vision has three levels:

1. **A protocol** — a small, well-specified shape for deliverables, claims, verifiers, and feedback packets. Any framework, any language, any agent can speak it.
2. **A reference implementation** — the open-source Python core plus MCP adapter, high quality enough that most teams adopt it directly.
3. **A marketplace of verifier packs** — domain-specific verification logic, some built in-house, most contributed by domain experts who know their vertical. This is where compounding value lives.

---

## 4. Core Concept

Every deliverable an agent produces contains **claims** — factual, procedural, or functional assertions that can be verified. Signoff extracts or accepts those claims alongside the deliverable, runs registered **verifiers** against them, and emits a **verdict**. If the verdict fails, the feedback packet is designed to be machine-consumable so the agent can retry, not for a human to interpret.

```
┌─────────┐    Deliverable + Claims    ┌──────────┐
│  Agent  │ ─────────────────────────► │ Signoff  │
└─────────┘                             │  Harness │
     ▲                                  └────┬─────┘
     │                                       │
     │         Verdict (pass/fail)           │
     │    + structured feedback packet       │
     └───────────────────────────────────────┘

       Harness runs registered verifiers
       concurrently, within a cost/time
       budget, and returns a verdict.
```

Four primitives:

- **Deliverable** — what the agent submitted (code diff, report, email draft, SQL + results, etc.).
- **Claim** — an asserted fact, citation, computation, or policy statement embedded in the deliverable.
- **Verifier** — a pluggable function that checks one kind of claim (or the whole deliverable), returns pass/fail with evidence.
- **Pack** — a versioned, pip-installable bundle of verifiers tuned for a domain.

---

## 5. Target Users

**Primary — solo developers and small teams building agents.** They feel the verification pain most acutely and have authority to adopt tools without procurement. Reach them through OSS distribution: GitHub, HackerNews, MCP ecosystem, developer communities. Priced at free (library) and $29/developer/month (hosted team tier).

**Secondary — engineering teams at mid-market SaaS companies deploying agents in production.** They need audit trails, team dashboards, cost attribution, and SSO. Reach them through inbound from OSS adoption plus targeted content. Priced at $499–$2,000/month per team.

**Tertiary — regulated industries (healthcare, financial services, legal, insurance).** They need tamper-evident audit logs, compliance exports, HIPAA/SOC 2 postures, and domain-specific verifier packs. Longer sales cycle, higher ACV ($20K–$100K/year). Reach them only after the OSS core is established and the first two verifier packs have proven the model.

---

## 6. Use Cases

### 6.1 Coding Agents

**Users:** Engineers using Claude Code, Cursor, Cline, Aider, Devin, or custom coding agents.
**Pain:** Agents declare work complete when tests fail, types don't check, or the change doesn't match the stated intent.
**Pack:** `signoff-code`
**Verifiers:** test execution, type checking, linter compliance, smoke test execution, semantic diff validation, adversarial LLM review.
**Value:** Cuts verification time to near-zero. The agent's "done" message can be trusted.

### 6.2 Research Agents

**Users:** Teams building deep-research tools, analyst copilots, investor research bots, consulting-style deliverable generators.
**Pain:** Fabricated citations, numbers that don't exist in the source, verbatim quotes that are paraphrases, missing uncertainty.
**Pack:** `signoff-research`
**Verifiers:** citation existence, citation entailment, quote verbatim matching, quantitative grounding, source credibility scoring, source diversity, recency appropriateness, uncertainty calibration.
**Value:** Publishable research output without line-by-line human verification.

### 6.3 Customer Support Agents

**Users:** Companies deploying support agents for Tier-1 resolution or human augmentation.
**Pain:** Agents promise refunds outside policy, misstate SLAs, omit required disclaimers, drift from brand voice.
**Pack:** `signoff-support`
**Verifiers:** policy compliance against rulebook, forbidden phrase detection, required disclaimer enforcement, intent alignment, tone compliance, no-hallucinated-product-facts check.
**Value:** Safe auto-response and safer human-in-the-loop suggestion, with an audit trail for every interaction.

### 6.4 Sales & Outbound Agents

**Users:** Teams running AI-personalized outbound at scale.
**Pain:** Fabricated personalization ("I saw you at Block" → prospect works at Square), hallucinated mutual connections, non-compliant claims.
**Pack:** `signoff-sales`
**Verifiers:** personalization grounding (every "you" claim traces to a real CRM fact), no fabricated mutual connections, compliance safety (no unverified ROI claims, no regulated-industry guarantees), deliverability checks.
**Value:** Personalization at scale without embarrassing misses.

### 6.5 Data Analysis Agents

**Users:** Users of Hex, Julius, Claude Data Analysis, or custom BI agents.
**Pain:** Confident aggregations that don't match the data, charts that don't match their captions, silent filter application.
**Pack:** `signoff-data`
**Verifiers:** SQL re-execution with diff, aggregation re-computation in pandas, chart-vs-data value matching, filter documentation check, null-handling disclosure.
**Value:** Analyst-grade output without analyst-grade human review.

### 6.6 Contract & Legal Review Agents

**Users:** In-house legal teams, contract ops, deal desks using LLMs for contract review.
**Pain:** Missed risk categories, wrong section references, jurisdiction-unaware advice.
**Pack:** `signoff-legal`
**Verifiers:** taxonomy coverage (all risk categories in the checklist were addressed), citation-to-clause (every "Section 3.2 says X" references the correct section), jurisdiction awareness, missing-party detection.
**Value:** Defensible use of AI in a risk-averse function.

### 6.7 Additional Packs (Later Phases)

- **signoff-ops** — claims processing, insurance adjudication, ticket triage.
- **signoff-finance** — reconciliation, categorization, expense review, GAAP compliance.
- **signoff-medical** — clinical note review, coding assistance, drug interaction verification.
- **signoff-compliance** — SOC 2 evidence gathering, policy check, control attestation.

---

## 7. Product Surfaces

Signoff ships three surfaces from the same core engine. The surfaces are tiered by capability, and the tiering motivates the upgrade path.

### 7.1 Library

Python (primary) and TypeScript (secondary) packages. `pip install signoff signoff-research`. Import, configure, call. Runs entirely in-process, no network required. Best for teams building custom agents with LangGraph, CrewAI, raw SDK, or proprietary frameworks.

### 7.2 MCP Server

`signoff serve --mcp` exposes the harness over the Model Context Protocol. Any MCP client — Claude Code, Cursor, Windsurf, Cline, Zed, Continue, custom agents — adds Signoff to its server list and gains a `request_signoff` tool. Zero integration work.

This is the primary distribution channel. An agent's system prompt instructs it to call `request_signoff` before declaring task completion; the sidecar inserts itself into the control flow transparently.

### 7.3 Hosted Service

A managed cloud service at signoff.dev (working name). Teams offload verification workloads when they need:

- Parallel verifier execution across a worker pool
- Sandboxed, pre-warmed containers for running untrusted code
- Tamper-evident audit history with compliance export
- Team dashboards and cross-agent reporting
- SSO, RBAC, usage quotas
- Flaky verifier detection across historical runs
- Cost observability attributed to user, session, and agent

Same verifier logic as the library. Different infrastructure.

| Capability | Library | MCP | Hosted |
|------------|---------|-----|--------|
| Built-in verifiers | ✓ | ✓ | ✓ |
| Structured feedback to agent | ✓ | ✓ | ✓ |
| Parallel execution | local CPU | local CPU | worker pool |
| Sandboxed code execution | needs local Docker | needs local Docker | managed |
| Audit log | local SQLite | local SQLite | Postgres, tamper-evident |
| Team dashboards | — | — | ✓ |
| SSO, RBAC | — | — | ✓ |
| Flaky verifier detection | — | — | ✓ |
| Cost observability | per-process | per-process | org-wide |
| Compliance exports | — | — | ✓ |

---

## 8. Architecture Overview

```
signoff-core/             Core engine. No transport knowledge.
  harness.py                Orchestration, budgets, concurrency.
  verifier.py               Plugin protocol, decorator, result types.
  deliverable.py, claim.py  Data models.
  feedback.py               Structured packet format.
  config.py                 YAML config loader.
  registry.py               Entry-point plugin discovery.

signoff-mcp/              MCP server adapter (~300 LOC).
  server.py                 Wraps harness as MCP tools.

signoff-sdk-ts/           TypeScript client for the hosted API.

signoff-packs/            First-party verifier packs (separate PyPI packages).
  signoff-code/
  signoff-research/
  signoff-support/
  signoff-sales/
  signoff-data/

signoff-cloud/            Hosted service (private repo).
  api/                      FastAPI application.
  workers/                  Distributed verifier execution.
  audit/                    Tamper-evident log, exports.
  dashboard/                Team UI.
  billing/                  Stripe integration.
```

`signoff-core` has no knowledge of MCP, HTTP, the cloud, or any particular agent framework. It exposes a `Harness` with a single `verify(deliverable, claims) -> Verdict` method. Every other surface is a thin adapter.

This separation is the strategic payoff: new surfaces (Temporal activity, Kafka consumer, Lambda handler, CLI) are 200–400 LOC each, and we can ship them as use cases emerge without touching the engine.

---

## 9. Business Model

### 9.1 Revenue Streams

1. **Hosted team plan** — $29/developer/month. Targets small teams that have outgrown local verification.
2. **Hosted business plan** — $499–$2,000/month per team. Includes team dashboards, SSO, flaky-verifier detection, cost attribution.
3. **Hosted enterprise plan** — $20K–$100K/year. Adds tamper-evident audit, compliance exports, SOC 2 Type II, HIPAA BAA, dedicated support.
4. **Verifier pack marketplace (Phase 3)** — 70/30 revenue share with pack authors. Enables domain experts to publish (e.g., a legal-tech firm publishes `signoff-msa` for master service agreements, a medical-informatics team publishes `signoff-icd10`). Becomes the real moat once there are 30+ packs.

### 9.2 Free Tier Philosophy

The OSS library and MCP server are genuinely complete. Every verifier in the first-party packs works identically in the library and the hosted service. The hosted service earns its fee on infrastructure (parallelism, audit, history, dashboards, compliance) — not on feature gatekeeping. This is the PostHog/Sentry/Temporal model, and it's the only model that sustains community trust at the scale needed to become infrastructure.

---

## 10. Phased Roadmap

### Phase 0 — Foundation (Weeks 1–4)

- `signoff-core` v0.1: data models, harness, verifier protocol, plugin discovery, YAML config, feedback packet format.
- `signoff-mcp` v0.1: MCP server wrapper exposing the harness.
- CI, test coverage, type checking, documentation scaffolding.
- No verifiers yet — skeleton only.

### Phase 1 — Coding Wedge (Weeks 5–8)

- `signoff-code` pack: test runner, type checker, linter, smoke test, semantic diff, adversarial review.
- Dogfood on internal projects.
- Launch OSS: HackerNews, MCP server registries, developer communities.
- Target: 500 GitHub stars, 50 active weekly users.

### Phase 2 — Research Pack & Hosted Alpha (Weeks 9–14)

- `signoff-research` pack: eight verifiers covering citations, quotes, numbers, credibility, diversity, recency, uncertainty.
- Hosted alpha: FastAPI app, Postgres audit log, Stripe billing, free tier with 1,000 verifications/month.
- Target: 1,500 stars, 200 active users, 10 paying teams.

### Phase 3 — Support & Sales Packs, Hosted Business Plan (Months 4–6)

- `signoff-support` and `signoff-sales` packs.
- Hosted business tier: team dashboards, SSO (Google, Okta), flaky detection, cost observability.
- Content marketing per vertical.
- Target: 5,000 stars, 1,000 active users, 75 paying teams, $40K MRR.

### Phase 4 — Data Pack, Compliance Posture, Marketplace Preview (Months 7–12)

- `signoff-data` and `signoff-legal` packs.
- SOC 2 Type I audit initiation.
- Marketplace preview: third-party packs with revenue share.
- TypeScript SDK for the hosted API.
- Target: 10,000 stars, 3,000 active users, 250 paying teams, $150K MRR.

### Phase 5 — Enterprise Posture (Year 2)

- SOC 2 Type II, HIPAA BAA.
- Tamper-evident audit log with cryptographic chain.
- On-prem / VPC deployment option.
- First marketplace packs from domain-expert third parties.
- Enterprise pilots in healthcare, finance, legal.

---

## 11. Risks & Mitigations

**Risk: Agent frameworks build verification in-house and obsolete the need.**
Mitigation: The protocol and verifier packs are the moat, not the harness. A framework's built-in verification will always be weaker than domain-tuned packs maintained by specialists. We ship as a library that embeds inside any framework — we compete with *in-house scripts*, not with frameworks.

**Risk: Observability platforms extend into pre-commit verification.**
Mitigation: They're structurally post-hoc; their entire product shape is trace inspection. Extending into pre-commit gating requires a different core primitive and a different integration pattern. We should move fast on the MCP distribution before they notice.

**Risk: The LLM judge inside verifiers hallucinates, undermining trust in the whole system.**
Mitigation: Layered verification — cheap deterministic checks (URL exists, number appears in source, regex match) run before LLM judges; LLM judges are structured (label + verbatim evidence); every verifier result records its evidence so failures are auditable. The judge is never the sole source of truth.

**Risk: Verifier packs are hard to maintain across domain evolution.**
Mitigation: Version packs on PyPI, use semver, write comprehensive pack-level test suites with representative claims and ground truth. Pack maintenance becomes the business, not a cost center.

**Risk: Low barrier to entry invites copycats.**
Mitigation: The core primitive is easy. The verifier library, the pack ecosystem, the community, and the hosted infrastructure are hard and cumulative. First-mover with the right open-source posture wins the protocol position.

---

## 12. What Success Looks Like

**12 months:** Signoff is the default "how do I make my agent's output trustworthy?" answer on HackerNews, Reddit, and developer Twitter. 10K+ stars, mid-hundreds of paying teams, 6+ first-party packs, early third-party pack contributions, $150K+ MRR.

**36 months:** Signoff is a line item in AI platform architecture diagrams the same way Sentry is in web architecture diagrams. Every major agent framework has Signoff integration docs. The verifier pack marketplace has 50+ domain-specific packs. Enterprise customers in regulated industries treat the audit log as core compliance evidence.

**The protocol wins either way.** Even in the bear case where Signoff-the-company plateaus, the verification protocol and verifier pack format become how the industry talks about agent trust. That's a win worth building toward.
