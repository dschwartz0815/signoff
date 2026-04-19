# signoff-runtime-docker

Docker-backed sandbox Runtime for Signoff verifiers. Implements the
`signoff.runtime.Runtime` protocol per `CLAUDE.md` §8.2.

Drop-in replacement for `LocalRuntime` when verifiers need to execute
untrusted content (running tests / linters on AI-generated code is the
motivating case — see `signoff-code`, which lands next). Each verifier
invocation gets a fresh ephemeral container; the verifier's own Python
logic still runs in the harness process, but every `ctx.exec` command
routes through `docker exec` into the container.

```python
from signoff_runtime_docker import DockerRuntime, DockerRuntimeConfig

runtime = DockerRuntime(DockerRuntimeConfig(verify_signatures=False))
# Pass `runtime` to `Harness(..., runtimes=[LocalRuntime(), runtime])`
# or let `Harness.from_config_path` auto-include it when
# signoff-runtime-docker is installed.
```

Safe by default: `network=none`, read-only workspace, read-only root
fs, non-root UID (10001), strict capability drop, PID / memory / CPU
limits, cosign signature verification on pulled images. See
[`docs/runtimes.md`](../../docs/runtimes.md) and
[`docs/deployment.md`](../../docs/deployment.md).

Install: `pip install signoff-runtime-docker` (pulls the `docker`
Python SDK as a required dep). Requires a reachable Docker daemon at
the usual socket / `DOCKER_HOST`.

`cosign` is optional but strongly recommended. When
`verify_signatures=True` (the default), the runtime invokes `cosign
verify` on every pulled image; missing `cosign` fails fast at
`prepare()` time with a clear error. Set `verify_signatures=False`
(logs a WARNING) if you're running against locally-built images in a
trusted environment.
