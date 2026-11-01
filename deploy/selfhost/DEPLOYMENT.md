# Deployment hardening and verification

The maintained controller operates an existing, initialized stack. It does not
own the engine VM or initialize application data. Use the same selected engine
connection throughout preparation, migration and operation. Keep that VM running
when stopping Macro so shared authentication stays available.

## Configuration and targeted repair

When preparing from Compose files, `--env-file` is mandatory. The controller
resolves its absolute path and sets both Compose's `--env-file` and
`MACRO_ENV_FILE`. The resulting resolved model is a private snapshot; it is not
a live reference to the source env file. Prepare a new reviewed plan when
configuration changes; do not silently reuse an old snapshot.

Before starting or recreating services, the controller renders Compose and
checks known agent boot prerequisites and partial Pipedream credentials. When
`APP_SECRETS_JSON` is present it is authoritative, including an empty object;
normal environment variables do not fill its missing keys. Errors identify key
names and provenance, never values. Service-native validation remains necessary:
the preflight is deliberately not a duplicate of every Rust configuration type.

After shared-auth migration:

```sh
python3 deploy/selfhost/stacks.py doctor --state deploy/selfhost/state/shared-auth
python3 deploy/selfhost/stacks.py repair --state deploy/selfhost/state/shared-auth agent_harness_service
```

`doctor` reads configuration and container status without changing services. It
reports health, restart count, OOM state and free space on the state filesystem.
It does not establish guest free space, successful browser login or document
integrity. It omits raw logs and resolved environment dumps because these can
contain secrets.

`repair` accepts only a recorded Macro service. It validates ownership, storage
identity and configuration, waits for auth and required dependencies, then
recreates only that service with no build and no implicit dependency creation.
It cannot repair shared auth or LocalStack. No repair runs initialization,
restores an old checkpoint, removes a volume, or changes the default connection.

## Resume and failure behavior

Normal resume first checks the original LocalStack container identity/start
time. A stopped or replaced emulator blocks resume until the operator performs
checkpoint recovery and explicitly records it with `storage-restored`.
That acknowledgement does not validate checkpoint contents.

The controller waits for auth and existing LocalStack, then starts PostgreSQL,
Redis, OpenSearch and Kafka before applications, using dependency layers and
Compose health checks. Redis now has a protocol health check. Dependencies
without health checks are only known to be running; `--wait` cannot prove their
application readiness. There is no sustained restart-stability observation yet.

The first Macro start after migration applies the new public auth URL. After
that successful transition, ordinary starts use `--no-recreate`; use `repair`
for an intentional targeted recreation. Infrastructure and LocalStack always
use `--no-recreate` during resume. Macro stop leaves shared auth and LocalStack
running.

Commands announce their stage and deadline before execution, with a 240-second
default subprocess deadline and 180-second Compose health wait. Failures omit
potentially sensitive subprocess output. A state-directory advisory lock blocks
concurrent controllers using the same plan. Atomic private state writes preserve
the previous receipt if replacement fails. An interrupted migration can still
require manual inspection of both projects; never reset data to recover it.

## Linux worker dependencies

The AI editing image installs the pinned Bun workspace dependencies inside Linux,
including development dependencies used by Wrangler. Its context excludes host
`node_modules`, native build outputs, env files and the default private deployment
state directory. The container mounts only its trace state, not the repository,
so a later host install cannot shadow Linux workerd/esbuild packages.

Source changes now require rebuilding this image. Existing prepared migration
snapshots preserve old mounts and image definitions: this source change does not
retrofit an old snapshot. Review and regenerate the resolved Compose model for
the existing project before applying the new image and trace-only mount. Preserve
the current `.wrangler` directory and application volumes. Never rerun first-time
initialization to update a worker. The image currently installs the full JS
workspace for correct workspace links; it has not been minimized or benchmarked.

## Exposure and trust

The migrated mailbox joins only the auth network and publishes direct ports on
loopback. Preparation refuses a Macro proxy on that network. A stale Macro
`/mailpit` route therefore cannot reach the mailbox and may return an upstream
error until the proxy configuration is regenerated. The auth gateway only routes
to FusionAuth. Verify bare and nested mailbox paths from another device before
sharing the deployment. For normal staff enrollment use real SMTP.

The source stack's other publishing rules are preserved, not made safe by this
migration. Gateway-only LAN publishing and public-origin persistence in the
upstream generator remain separate work. Use matching certificates from a
private deployment CA or an operator-provided certificate and configure trust
in every client. Never disable certificate verification to fix HSTS errors.

## Evidence and remaining work

| Area | Current evidence or boundary |
| --- | --- |
| Configuration, targeted repair and resume ordering | Regression tests exercise authoritative JSON, env-file selection, secret omission, failure ordering and selected recreation. These are harness protections. |
| Shared auth and Open WebUI | Configuration/provisioning tools and offline checks; live migration, token validation and browser SSO still require the deployment host. |
| Linux worker image | Source isolation is implemented; an actual ARM64 build, health transition and document edit remain unverified. |
| Optional providers and MCP | Boot decoupling is implemented on the preceding commits; authenticated MCP initialize/tools/list and user isolation need live verification. |
| Storage | Emulator restart guard only. Complete S3/DynamoDB checkpoint/restore, uploaded-byte hashes, metadata and permission round-trips remain pending. |
| Engine compatibility | Existing-engine operation only. VM ownership/adoption, guest storage/network diagnostics, tiny build/load probes and guarded reset remain pending. |
| Networking | Structured resolved Compose ports are preserved; full generator-owned LAN origin/binding controls and CORS review remain pending. |
| Build resources | Earlier two-job/non-incremental defaults remain; reusable ARM64 images, registry availability and measured savings remain pending. |

Run `python3 -m unittest discover -s deploy/selfhost/tests -v`. Set
`MACROLIGHT_COMPOSE` to a Compose V2 executable to include actual model rendering.
Offline checks cannot establish a fresh deployment, backup completeness or a
successful multi-user application session.
