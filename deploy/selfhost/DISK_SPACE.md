# Smaller local deployments

Most local disk use is generated output, not Git history. Export the working
Linux executables before removing compiler caches: running Rust containers bind
`target/.../debug` directly. Deleting that directory first breaks later restarts.
The runtime tool never stops services, deletes data, or prunes the engine.

## Export the working executables

Run from the source repository with no build in progress, using the existing
Podman connection. This example uses an existing `office-test` instance; change
both names for another instance. An export needs enough free space for copies of
the executables plus a 4 GiB reserve. It never hardlinks mutable Cargo outputs.

```sh
python3 deploy/selfhost/runtime.py --engine podman --project macro-office-test \
  export --output "$HOME/macro-test/runtime/binaries-v1"
```

The tool derives binary names and read-only bind sources from containers carrying
the selected Compose project's labels. It copies only top-level ARM64/x86-64
Linux executables from those directories, excluding compiler intermediates and
host tools. A private manifest records service mappings, sizes and SHA-256
checksums. Incomplete exports are removed; existing exports are never overwritten.
Nix exports are refused because copying binaries alone loses their store closure.
Mount the original Nix output with the existing `--binaries-dir` support instead.

## Adopt and verify before cleanup

For an initialized **combined** developer stack, use the existing orchestrator to
remount the export. Keep the original instance, port base, engine selection and
environment override arguments. `MACRO_OVERRIDE_ENV` below must point to the
existing installer override file, not a newly generated empty file.

```sh
set -e
: "${MACRO_OVERRIDE_ENV:?Set this to the existing installer env override path}"
test -s infra/local/generated/office-test/stack.json
just stack update --instance office-test --port-base 23000 --no-doppler \
  --env-file "$MACRO_OVERRIDE_ENV" \
  --binaries-dir "$HOME/macro-test/runtime/binaries-v1"
python3 deploy/selfhost/runtime.py --engine podman --project macro-office-test \
  verify --directory "$HOME/macro-test/runtime/binaries-v1" \
  --stack-state infra/local/generated/office-test/stack.json
```

The `stack.json` guard matters: `stack update` without initialized state bootstraps
a stack. Do not substitute `stack up`, `stack down`, a fresh installer, or reset
commands. Updating binaries recreates Rust containers; it preserves their volumes.
Verify login, an existing document, upload/download and search before cleanup.
The Python check establishes hashes, the persisted runtime path, container mounts,
process state and configured health checks; it does not prove application data or
end-to-end behavior. Keep the old build directory for rollback until those checks
succeed. Roll back with the same update command and the original binaries path.

**After shared-auth migration**, do not use `just stack update`: its combined
Compose model can recreate identity services or remove independently managed
containers. Export remains read-only and works, but adoption must update the
prepared Macro Compose model and its operations state through a reviewed plan.
Automatic export adoption for split stacks is not implemented.

## Reclaim generated output

Only after successful adoption and application checks, inspect all container
mounts, including stopped containers and other projects, for references to the
old directories. Their source paths are in the export manifest. Do not delete an
old path still referenced by any container or prepared Compose model.

The largest removable build directories on an ARM64 Mac are usually:

| Path relative to the repository | Effect of removing it |
| --- | --- |
| `target/aarch64-unknown-linux-gnu` | Removes cross-compiled dependencies and old service binaries; next Rust build recompiles them. |
| `target/local-isolated` | Removes the separate local search build cache; next search build recompiles it. |
| `target/debug/deps` and `target/debug/build` | Removes host tooling dependency caches; preserve the `xtask_local` executable if using it directly. `just stack` may recompile tooling. |
| Bun's download cache inside the installer's runtime directory | Removes downloaded package copies; keep installed dependencies to avoid reinstalling them. |

Do not delete the whole source checkout: the proxy, static frontend, migrations,
analytics and other companion services can still mount files from it. Keep
`infra/local/generated`, existing env overrides, certificates, `.wrangler` trace
state, auth state and application volumes. Do not run global image/volume prune
or remove the shared VM. Guest deletions may not shrink its host disk image;
this workflow makes no VM compaction claim.

## Smaller JS images and search builds

The worker installs only its filtered workspace dependency graph, including
Wrangler's development dependencies, and removes the download cache in the same
layer. Host native dependencies still cannot shadow the Linux packages.
The Lexical image builds in a separate stage; its final image contains the server
bundle and the external `loro-crdt` package, including its WASM, rather than the
whole checkout and build dependencies. Rebuild the selected images explicitly
and verify them before retiring older images. Existing images/caches are not
deleted by updating the repository. Image size savings require host measurements.

Search keeps a separate build directory because it disables the default `pdf`
feature locally. Reusing the unified Cargo cache with a different selected graph
invalidates shared artifacts and causes repeated rebuilds. Eliminating that cache
requires changing and testing both hosted and local feature selection; this
change does not alter search capabilities. Runtime export allows the completed
search build cache to be removed when future compilation speed is less important.
