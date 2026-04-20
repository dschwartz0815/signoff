# Security

Signoff runs untrusted code. Its threat model is serious and so
is our handling of vulnerability reports.

---

## Reporting a vulnerability

**Please do not report vulnerabilities via public GitHub issues or
pull requests.**

Instead, use GitHub's private-reporting feature:
[**Report a vulnerability**](https://github.com/signoff/signoff/security/advisories/new).
That surface routes straight to the maintainers without public
disclosure.

Alternatively, email `security@signoff.dev`. Include:

- The affected package(s) and version(s).
- A clear description of the issue.
- Reproduction steps. Proof-of-concept code is welcome.
- Any mitigating factors (e.g. "only reachable when
  `SIGNOFF_DOCKER_VERIFY_SIGNATURES=false`").

We aim to acknowledge reports within **2 business days** and to
provide a remediation timeline within **7 business days**. Critical
issues take precedence over everything else in the queue.

---

## What we consider in-scope

Everything that could let an attacker:

- Escape the `DockerRuntime` sandbox to the host.
- Exfiltrate data from a running harness (environment variables,
  API keys, cached judge responses).
- Cause a verifier to return a passing verdict on a deliverable
  that should fail (integrity of the audit trail).
- Poison a pack's signed image (supply-chain attack).
- Route a verifier's `ctx.exec` to a host path outside the
  workspace.
- Trigger prompt-injection via `semantic_diff` in a way that
  produces an unintended label.
- Cause a crash / hang under policy-compliant input
  (denial-of-service of a tenant in a multi-tenant deployment).

---

## What we consider out-of-scope

- Issues only reproducible with `verify_signatures=False` on
  untrusted images. That combination is explicitly documented
  as unsafe; report the *doc gap* instead.
- Issues that require an attacker to already have access to the
  Docker socket the harness talks to. Docker-socket access is
  root-equivalent on the host; see
  [`docs/deployment.md`](./docs/deployment.md) for the threat
  model around nested-container setups.
- Theoretical side-channel attacks on the LLM providers
  themselves. Those are the providers' responsibility.

---

## Our stance on sandboxing

`DockerRuntime` is the thing that enables most of the product.
Specifics:

- Containers drop all Linux capabilities (`cap_drop=[ALL]`), run
  with `no-new-privileges`, and mount a read-only rootfs with a
  tmpfs at `/tmp`.
- The non-root signoff user (UID 10001) owns the container
  process.
- The workspace is bind-mounted read-only by default; packs that
  need writes set `workspace_mount_mode: rw` in config.
- Networking defaults to `none`. Verifier `ctx.http` and
  `ctx.judge` calls happen on the host, not in the sandbox, so
  the sandbox container itself doesn't need outbound network.
- Images are signed with `cosign` in the publish workflow and
  verified before first use. Missing `cosign` on PATH with
  `verify_signatures=True` is a fail-fast, not a silent skip.
- Trivy scans block publishes on CRITICAL fixed vulnerabilities.

None of these by itself is a security perimeter; combined, they
make a compromised verifier substantially harder to weaponise.
For multi-tenant deployments we recommend running the harness on
dedicated worker nodes or inside a Firecracker/Kata-isolated pod;
[`docs/deployment.md`](./docs/deployment.md) § "Running with
DockerRuntime" enumerates the tradeoffs per pattern.

---

## Disclosure policy

We follow coordinated disclosure:

1. You report privately. We acknowledge + triage.
2. We develop and test a fix, typically on a private branch.
3. We publish the fix, a CVE (if warranted), and an advisory at
   the same time.
4. Credit goes to the reporter in the advisory, unless you ask
   otherwise.

We don't have a bug bounty program at present. When we do,
this doc will be the place it's announced.

---

## Supported versions

Phase 1 ships with a single supported minor version per package
(`0.0.x`). Security fixes ship as patch releases on the latest
minor. As the project matures we'll adopt a longer-term support
policy; this doc will track it.

---

## Acknowledgments

If you've reported a vulnerability that resulted in a fix, we'd
like to credit you here. Open an advisory at the link above and
we'll add your name (or handle) on release.
