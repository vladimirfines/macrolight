# Self-host operations

## Scope

Owns the persistent auth-stack migration, Open WebUI OIDC setup and runtime export.
These tools operate existing local Compose projects; they do not deploy upstream cloud stacks.

## Start here

- `README.md`: migration, rollback, OIDC enrollment and verification.
- `stacks.py`: partition a resolved Compose model; preserve external resources.
- `openwebui.py`: provision one dedicated OIDC application and private overlay.
- `operations.py`: bounded execution, generated env selection and config preflight.
- `runtime.py` and `DISK_SPACE.md`: executable export and post-adoption verification.
- `DEPLOYMENT.md`: repair, resume, worker isolation and verification boundaries.

## Checks

- `python3 -m unittest discover -s deploy/selfhost/tests -v`
- Render generated Compose with Compose V2 `config --format json`.
- `python3 tooling/pii/scan.py --changed`

## Gotchas

- Generated models contain credentials: write only to private, ignored state.
- Never remove volumes, restore snapshots, or start a second database writer.
- Keep the auth gateway and mail delivery independent of Macro.
- Preserve the existing tenant issuer and Macro signing keys during migration.
- Provision asymmetric signing keys only for the new OIDC application.
- Never treat discovery checks as proof of a completed browser login.
- Keep the mailbox off every Macro gateway network and private state out of image contexts.
- Validate effective JSON configuration before mutation; never print resolved secrets.
- Preserve the LocalStack restart guard; a running container is not recovered storage.
- Export never adopts or deletes build data; verify live mounts and application behavior before cleanup.
- Never use the combined-stack launcher to adopt binaries after shared-auth migration.
