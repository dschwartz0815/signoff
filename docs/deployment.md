# Deployment & Self-Hosting

**Status:** Phase 0 + 1 coverage below. Full multi-tenant self-hosting guide lands alongside the Phase 2 hosted alpha.

Docker image and compose conventions: [`CLAUDE.md`](../CLAUDE.md) §9 — minimal multi-stage builds, non-root users, `cosign`-signed releases, `trivy` CI gate. Local dev stack: `just dev` (OSS) or `just cloud-dev` (with Postgres + Redis + cloud services).

---

## Running with `DockerRuntime`

`signoff-runtime-docker` spawns an ephemeral container per verifier
invocation. When the harness itself runs in a container (the published
`ghcr.io/signoff/mcp` image does), it needs access to the host's Docker
daemon to spawn sibling containers. There are three reasonable
patterns; they trade off security against operational simplicity.

### 1. Socket mount (simplest, weakest isolation)

```yaml
# docker-compose.yml excerpt
services:
  signoff-mcp:
    image: ghcr.io/signoff/mcp:latest
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - ./signoff.yaml:/app/signoff.yaml:ro
    environment:
      SIGNOFF_DOCKER_VERIFY_SIGNATURES: "true"
```

**Security tradeoff:** access to `/var/run/docker.sock` is effectively
root on the host — a compromised harness (or a compromised verifier
pack the harness loads) can launch privileged containers, mount the
host filesystem, and escape. `DockerRuntime`'s cap-drop +
read-only-rootfs + non-root-user hardening applies only to the
*spawned* sandbox containers, not to this MCP container's access to
the daemon.

Suitable when: trusted operator, trusted verifier packs, single-host
deployment.

Unsuitable when: running untrusted third-party verifier packs, or
running in a multi-tenant environment where "host root" is too much
blast radius.

### 2. Docker-in-Docker (dind) sidecar

Run a dedicated `docker:dind` container alongside the harness, have
the harness talk to *its* daemon via TCP inside the pod. Isolates the
blast radius: even if the harness is compromised, the daemon it
controls has no view of the host.

```yaml
services:
  dind:
    image: docker:27-dind
    privileged: true               # dind needs this; quarantine it in a pod/VM.
    environment:
      DOCKER_TLS_CERTDIR: "/certs"
    volumes:
      - dind-certs:/certs/client
      - dind-storage:/var/lib/docker
  signoff-mcp:
    image: ghcr.io/signoff/mcp:latest
    environment:
      DOCKER_HOST: tcp://dind:2376
      DOCKER_TLS_VERIFY: "1"
      DOCKER_CERT_PATH: /certs/client
      SIGNOFF_DOCKER_DOCKER_HOST: tcp://dind:2376
    volumes:
      - dind-certs:/certs/client:ro
      - ./signoff.yaml:/app/signoff.yaml:ro
```

**Security tradeoff:** the dind container is `privileged` and
effectively root-equivalent inside its own namespace. Isolation from
the host depends on whatever boundary contains the pod (Kubernetes
`PodSpec.securityContext`, gVisor, Firecracker, dedicated VM).
Ops-cost is higher (the dind daemon is slow to warm; verifiers pay
the first-container tax on every cold pod).

Suitable when: running in Kubernetes with a strong node-isolation
story, or in a VM-per-pod setup.

### 3. Separate worker nodes (recommended at scale)

The harness becomes a thin scheduler that submits verifier jobs to a
pool of worker nodes, each of which runs its own `DockerRuntime`. The
MCP container has no Docker access at all. The surface the attacker
can reach is whatever RPC your scheduler exposes to workers.

In Phase 2 this is what the hosted service uses. For now, the
"docker-in-docker" pattern is the nearest approximation; the worker
abstraction lands with the Kubernetes-Job runtime.

---

## Picking one for local dev

`just dev` + the default `examples/minimal.yaml` keep everything local
(`LocalRuntime`, `FakeHttpClient`, `FakeJudge`). Zero daemon sockets
mounted anywhere; zero untrusted input. This is the recommended entry
point.

When you want to exercise `DockerRuntime` end-to-end on a single
machine, start with Pattern 1 (socket mount) and be aware of the
tradeoff. A future `just dev-sandboxed` recipe will add the dind
pattern with a warning about the `privileged` container.

---

## See also

- [`docs/runtimes.md`](./runtimes.md) — Runtime protocol, `LocalRuntime`, `DockerRuntime` safe-by-default posture, exec routing.
- [`docs/configuration.md`](./configuration.md) — SIGNOFF_\* env-var namespaces including `SIGNOFF_DOCKER_*`.
- [`packages/signoff-runtime-docker/README.md`](../packages/signoff-runtime-docker/README.md) — package quickstart.
