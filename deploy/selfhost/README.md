# Shared authentication and Open WebUI

This workflow migrates an **already initialized, running** local Macro stack.
It prepares files first; `migrate` performs the explicit container move. No step
rebuilds Rust, resets a database, restores a snapshot or deletes volumes.
It is separate from the upstream disposable developer-stack launcher.

For executable-only exports and cache cleanup boundaries, see [disk space](DISK_SPACE.md).

## What stays running

The new `macrolight-auth` project owns FusionAuth, its current PostgreSQL data,
Mailpit and an independent Caddy HTTPS endpoint. The existing Macro project
keeps its application services and uses the shared network to reach FusionAuth.
All existing network and volume names are retained as external resources.

The Podman VM must remain running. Do not use a VM-stop command when switching
applications. `unless-stopped` policies restart auth with the container engine;
they cannot keep it available while the Mac sleeps or the VM is off.

Mailpit remains a local test mail sink and its UI binds only to loopback.
It joins only the auth network; the old Macro gateway cannot reach it. Preparation
refuses a Macro proxy sharing that network. The auth gateway does not expose it.
For staff access, configure working SMTP
in FusionAuth instead of exposing the login-code inbox.

**Account-provisioning limitation:** the existing tenant has synchronous global
`user.create` and `user.email.verified` webhooks calling Macro. This migration
does not disable that signup policy or rewrite the account model. Create,
register and verify the first SSO users while Macro is running. Existing enrolled
users can then test login with Macro stopped. New-user creation or email
verification may still require Macro until those legacy hooks are refactored.
Do not silently change them to non-blocking delivery: Macro depends on them to
register users and initialize application records.

## 1. Prepare the migration

Use the same engine connection as the existing Macro stack. `--engine docker`
means the existing Docker-compatible CLI pointed at Podman, not a requirement
to install or run Docker Desktop. `--engine podman` uses `podman compose`.
The Compose provider must support `config --format json`, `--wait` and the
merge features already required by the Macro launcher.

Choose a stable HTTPS auth origin, on a different port from Macro. The browser,
the host and the Open WebUI container must resolve/reach the same origin.
Do not use container-only names such as `http://fusionauth:9011` as the public
OIDC URL. A LAN IP plus a dedicated HTTPS port also works if present in the cert.

Create a server certificate for that hostname/IP using the **existing** trusted
CA; do not rotate the CA during this move. With mkcert already configured:

```sh
mkdir -p deploy/selfhost/state/tls
mkcert -cert-file deploy/selfhost/state/tls/server.pem \
  -key-file deploy/selfhost/state/tls/server-key.pem auth.example.test
```

Replace the sample host and source paths below. The source project name is the
existing container label, not necessarily the directory name. The current
evaluation wrapper stores its checkout at `~/macro-test/source/macro` and the
generated env/override under `infra/local/generated/<instance>`.

```sh
python3 deploy/selfhost/stacks.py prepare \
  --engine docker \
  --source-project EXISTING_COMPOSE_PROJECT \
  --project-directory /absolute/path/to/current/macro-checkout \
  --compose-file /absolute/path/to/current/macro-checkout/docker/docker-compose.yml \
  --compose-file /absolute/path/to/generated/docker-compose.override.yml \
  --env-file /absolute/path/to/generated/local.generated.env \
  --auth-origin https://auth.example.test:23010 \
  --cert deploy/selfhost/state/tls/server.pem \
  --key deploy/selfhost/state/tls/server-key.pem \
  --output deploy/selfhost/state/shared-auth
```

Alternatively, provide `--source-config /private/path/resolved-compose.json`
exported by the current Compose command's `config --format json`. Do not
hand-author that source: its names, paths, environment and dollar escaping must
be fully resolved by Compose. A preparation failure changes no containers;
use a new output directory for a replacement plan.

The output contains credentials: keep it private and outside public commits.
The generated manifests deliberately preserve the current images and data
mounts. The new FusionAuth service drops the old kickstart mount so a restart
cannot reapply a disposable bootstrap over the shared identity provider.

## 2. Move auth, then use the new lifecycle commands

```sh
python3 deploy/selfhost/stacks.py migrate --state deploy/selfhost/state/shared-auth
python3 deploy/selfhost/stacks.py macro --state deploy/selfhost/state/shared-auth start
python3 deploy/selfhost/stacks.py auth --state deploy/selfhost/state/shared-auth status
```

Migration verifies the recorded source containers, stops FusionAuth, exports
the still-running auth database, then stops/removes only those three containers
without removing their volumes. It starts auth under the new project and waits
for readiness. If startup fails, it attempts to restore the previous auth
project. The backup is `auth-before-migration.sql`, mode `0600`.

If automatic rollback reports incomplete, inspect both project states before
retrying; never start another PostgreSQL container against the same volume.
The tool intentionally does not offer a data-restore or volume-delete command.

Normal switching:

```sh
python3 deploy/selfhost/stacks.py macro --state deploy/selfhost/state/shared-auth stop
python3 deploy/selfhost/stacks.py auth --state deploy/selfhost/state/shared-auth status
python3 deploy/selfhost/stacks.py macro --state deploy/selfhost/state/shared-auth start
```

`macro stop` stops recorded application services but leaves LocalStack running:
the upstream local emulator has volatile object/key/queue state. The evaluation
wrapper exported checkpoints before stopping it; this controller must not skip
that recovery contract. `macro start` refuses a stopped/replaced/restarted
LocalStack and never implicitly recreates it. After a VM shutdown, recover the
evaluation wrapper's object/cloud checkpoint and verify the stored documents
and keys before acknowledging the new running container:

```sh
python3 deploy/selfhost/stacks.py storage-restored --state deploy/selfhost/state/shared-auth
```

That command records an operator-completed recovery; it does not restore or
validate any data. A persistent storage replacement remains separate work.

`macro start` ensures auth is up
before starting the application. Start commands refuse recreated source auth
containers or an unexpected running writer on the shared auth database volume.
Use `auth ... stop` only for deliberate auth
maintenance. Never run the old combined `stack up/down`, `reset_local`,
`destroy_local`, snapshot restore or combined Compose `up` against this migrated
instance. Those paths still describe a disposable combined developer stack.
The evaluation wrapper's label-based Macro stop no longer matches moved auth
containers, but its combined start/reinstall path must not recreate them.

## 3. Provision Open WebUI

First retain a working local Open WebUI administrator, its data volume and its
existing `WEBUI_SECRET_KEY`. Do not replace the session key during SSO setup.
Place a FusionAuth API key with application/key-management permissions in a
local `0600` file. Find the existing tenant ID in FusionAuth's admin UI.

```sh
python3 deploy/selfhost/openwebui.py provision \
  --engine docker \
  --auth-origin https://auth.example.test:23010 \
  --tenant TENANT_UUID \
  --api-key-file /private/path/fusionauth-api-key \
  --webui-url https://apps.example.test:8444 \
  --service EXISTING_WEBUI_COMPOSE_SERVICE \
  --webui-container EXISTING_WEBUI_CONTAINER \
  --ca-file /private/path/existing-root-ca.crt \
  --output deploy/selfhost/state/openwebui
```

Omit `--ca-file` and `--webui-container` when using a publicly trusted CA.
For a private CA the helper copies the container's current trust roots, appends
the CA, and mounts the combined bundle; it does not disable TLS verification or
replace public roots needed by external AI providers.

The helper creates a dedicated OIDC client and RS256 signing key. It does not
change the tenant issuer, Macro client, Macro JWT keys or existing users. Its
private receipt makes retries reuse the same identifiers/secret. Existing
application settings that differ are refused rather than overwritten.

In FusionAuth, register each approved existing user for **Open WebUI**, and
ensure their email is verified before the offline-Macro test. Public self-service
registration is disabled on this application. New Open WebUI OAuth users get
the `user` role. The local login form stays enabled; email-based account merging
and automatic provider redirects stay off. An existing local account with the
same email needs an intentional linking decision; enabling global merging is
not part of this workflow.

Apply the private overlay to the **existing WebUI project**, preserving its
original Compose files, project name and env-file flags:

```sh
docker compose -p EXISTING_WEBUI_PROJECT \
  -f /absolute/path/to/existing-apps-compose.yml \
  -f /absolute/path/to/macrolight/deploy/selfhost/state/openwebui/openwebui.compose.json \
  up -d --no-deps --no-build EXISTING_WEBUI_COMPOSE_SERVICE
```

The exact callback is `<public WebUI origin>/oauth/oidc/callback`. The discovery
URL is `<auth origin>/<tenant UUID>/.well-known/openid-configuration`.
OAuth env values are authoritative with `ENABLE_OAUTH_PERSISTENT_CONFIG=false`
on current Open WebUI. Older images may require updating their saved OAuth
settings in the admin UI; check the deployed image's documentation first.

## 4. Verify before considering SSO operational

```sh
python3 deploy/selfhost/openwebui.py check \
  --auth-origin https://auth.example.test:23010 \
  --tenant TENANT_UUID --ca-file /private/path/existing-root-ca.crt
```

Also fetch that discovery URL **from inside the running WebUI container**, using
its normal certificate verification, to prove its DNS, routing and CA setup.
The helper checks advertised origins and capabilities; it does not execute a
browser authorization-code flow.

Test with a non-admin, enrolled, verified user in a private browser window:

1. Complete Open WebUI sign-in through FusionAuth; confirm the returned user
   has the intended role and cannot access administration.
2. Stop Macro with the split controller. Confirm auth's database, FusionAuth,
   Mailpit/SMTP and gateway remain running, then repeat sign-in in a fresh session.
3. Confirm an unregistered user is refused, and local administrator recovery
   still works. Do not disable the fallback login until this passes.
4. Restart Macro and confirm normal Macro login and MCP authorization still work.

Record this distinction: generated configuration, successful discovery,
successful token verification and successful end-to-end login are separate
checks. No live login is claimed by the repository's offline tests.

## Other apps

See [deployment hardening](DEPLOYMENT.md) for `doctor`, guarded targeted `repair`,
configuration snapshot updates, dependency-aware resume, Linux worker images and
the remaining acceptance checks.

See [APP_SUPPORT.md](APP_SUPPORT.md) for source-backed capability checks. Each
application needs its own client and redirect URI. A proxy login gate is access
protection; it does not automatically create application users or roles.

References: [FusionAuth application API](https://fusionauth.io/docs/apis/applications/create-an-application),
[discovery](https://fusionauth.io/docs/apis/oauth/openid-configuration),
[Open WebUI SSO](https://docs.openwebui.com/features/authentication-access/auth/sso/).
