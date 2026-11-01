# Macrolight

A self-hosting fork of [Macro](https://github.com/macro-inc/macro), focused on
simpler operations, fewer mandatory vendor integrations, and lower build
resource usage. The frontend is SolidJS; the backend is Rust.

This fork is under active development. Changes below describe committed source
and setup tools, not a claim that an existing installation has been upgraded.

## Changes from upstream

| Area | Implemented change |
| --- | --- |
| Build memory | Host service builds default to at most two concurrent jobs; the legacy container build defaults to two. Explicit job overrides remain available. |
| Build disk use | Development incremental artifacts are disabled by default; shared dependency caches remain reusable. |
| MCP sign-in | Uses FusionAuth's hosted login without requiring a Google/Gmail provider lookup or Google secrets. |
| Optional connectors | Pipedream can be absent across agent egress, chat and the app catalog; partial credentials fail clearly. |
| Local defaults | Fake Pipedream credentials and webhook stubs are no longer generated. |
| Auth lifecycle | A migration tool separates FusionAuth, its existing database and mail delivery into an independent Compose project with its own HTTPS gateway. |
| Open WebUI | Dedicated OIDC client provisioning, app-specific RSA signing keys, a private Compose overlay and discovery validation. |
| Operations | Existing auth volumes are reused; migration exports a database backup and attempts rollback on startup failure. Macro stop does not stop the separated auth project. |
| Deployment checks | Explicit generated-env injection, authoritative JSON preflight, bounded commands, ownership checks, atomic state, diagnostics and targeted repair. |
| Resume | Infrastructure health waits precede application startup; normal starts preserve existing containers after the auth URL transition. |
| Linux worker | Dependencies install inside the image; trace-only mounts prevent host native packages from replacing Linux executables. Runtime validation is still pending. |

The auth split preserves the existing tenant, users, Macro client, signing keys
and issuer. Synchronous legacy tenant webhooks still affect user creation/email
verification while Macro is stopped: pre-enroll and verify the initial SSO users.
The split does not change those account-provisioning policies.

## Start here

- [Separate auth and connect Open WebUI](deploy/selfhost/README.md).
- [SSO support in other self-hosted apps](deploy/selfhost/APP_SUPPORT.md).
- [Deployment hardening and remaining verification](deploy/selfhost/DEPLOYMENT.md).
- [Build and local development](docs/development/running-locally.md).
- [Code contribution rules](CONTRIBUTING.md).

Use the split-stack commands after migration. The upstream development launcher
still creates a combined disposable stack and must not manage shared auth data.
Keep the Podman VM running: a separate Compose project cannot survive its VM
being stopped. Restart policies restart auth when the engine restarts; stopping
Macro alone leaves auth running. The switch controller also leaves the current
volatile LocalStack container running rather than discard its storage state.

## Still planned

- Remove calls, CRM and GitHub/cloud-agent features from the dependency graph.
- Exclude unused file-format WebAssembly engines from builds.
- Reusable runtime images and resource benchmarks.
- Generic branding hooks and themes.
- Decouple legacy user-provisioning webhooks from Macro availability.

Those features have not been deleted or benchmarked by the changes above.

## License and attribution

Macrolight derives from Macro by Macro, Inc. Preserve upstream copyright and
license notices. The code remains [GNU AGPLv3](LICENSE.txt). Operators of a
modified network application must offer the corresponding source to its users.
