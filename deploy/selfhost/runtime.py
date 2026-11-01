#!/usr/bin/env python3
"""Export Linux service executables without touching the running deployment."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

from stacks import containers, private_json


class RuntimeExportError(ValueError):
    """An operator-actionable error containing no resolved configuration."""


def digest(path):
    with path.open('rb') as file:
        checksum = hashlib.sha256()
        for block in iter(lambda: file.read(1024 * 1024), b''):
            checksum.update(block)
        return checksum.hexdigest()


def linux_executable(path):
    with path.open('rb') as file:
        header = file.read(20)
    return (len(header) == 20 and header[:6] == b'\x7fELF\x02\x01'
            and int.from_bytes(header[18:20], 'little') in (62, 183))


def runtime_mounts(items):
    """Use inspected commands and mounts, never infer binaries from service names."""
    result = {}
    for service, item in items.items():
        command = item['Config'].get('Cmd') or []
        if not isinstance(command, list) or not command or not command[0].startswith('/app/out/'):
            continue
        binary = command[0].removeprefix('/app/out/')
        if not re.fullmatch(r'[A-Za-z0-9_-]+', binary):
            raise RuntimeExportError('unexpected runtime executable path')
        mounts = item.get('Mounts', [])
        if any(m.get('Destination') == '/nix/store' for m in mounts):
            raise RuntimeExportError('Nix binaries require their store closure; use --binaries-dir directly')
        selected = [m for m in mounts if m.get('Destination') == '/app/out']
        if len(selected) != 1 or selected[0].get('Type') != 'bind' or selected[0].get('RW', True):
            raise RuntimeExportError('expected one read-only runtime bind mount for ' + service)
        result[service] = {'binary': binary, 'source': str(Path(selected[0]['Source']).resolve(strict=True))}
    if not result:
        raise RuntimeExportError('no /app/out service mounts found in the selected project')
    return result


def export(engine, project, output, reserve_gib=4):
    output = Path(output).expanduser().absolute()
    if output.exists() or output.is_symlink():
        raise RuntimeExportError('output already exists; use a new versioned directory')
    output.parent.mkdir(parents=True, exist_ok=True)
    sources = runtime_mounts(containers(engine, project))
    # Include every Linux binary in the same top-level build directory. The
    # orchestrator validates its complete inventory even for a partial stack.
    binaries = {}
    for source in sorted({v['source'] for v in sources.values()}):
        for path in sorted(Path(source).iterdir()):
            if (not re.fullmatch(r'[A-Za-z0-9_-]+', path.name)
                    or path.is_symlink() or not path.is_file() or not os.access(path, os.X_OK)):
                continue
            if linux_executable(path):
                if path.name in binaries and path != binaries[path.name]:
                    raise RuntimeExportError('duplicate executable in different runtime directories')
                binaries[path.name] = path
    if any(v['binary'] not in binaries for v in sources.values()):
        raise RuntimeExportError('an active binary is missing or is not an ARM64/x86-64 Linux executable')
    required = sum(p.stat().st_size for p in binaries.values())
    if not 0 <= reserve_gib < float('inf') or shutil.disk_usage(output.parent).free < required + reserve_gib * 1024**3:
        raise RuntimeExportError(f'export needs {required / 1024**3:.2f} GiB plus the {reserve_gib:g} GiB reserve')
    pending = Path(tempfile.mkdtemp(prefix='.runtime-', dir=output.parent))
    try:
        manifest = {'version': 1, 'project': project, 'services': sources, 'binaries': {}}
        for name, source in binaries.items():
            before = source.stat()
            checksum = digest(source)
            target = pending / name
            shutil.copyfile(source, target)  # Never hardlink a mutable Cargo output.
            target.chmod(0o555)
            after = source.stat()
            if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns) or digest(target) != checksum:
                raise RuntimeExportError('build outputs changed during export; stop the build and retry')
            manifest['binaries'][name] = {'size': target.stat().st_size, 'sha256': checksum}
        private_json(pending / 'manifest.json', manifest)
        # A completed export is published only after all checksums match.
        if output.exists():
            raise RuntimeExportError('output appeared during export; choose another directory')
        pending.rename(output)
    finally:
        if pending.exists():
            shutil.rmtree(pending)
    return manifest


def verify(engine, project, directory, stack_state):
    directory = Path(directory).expanduser().resolve(strict=True)
    manifest = json.loads((directory / 'manifest.json').read_text())
    if manifest.get('version') != 1 or manifest.get('project') != project:
        raise RuntimeExportError('runtime manifest belongs to another project or version')
    state = json.loads(Path(stack_state).expanduser().read_text())
    if not state.get('binaries_dir') or Path(state['binaries_dir']).resolve() != directory:
        raise RuntimeExportError('stack.json has not adopted this runtime directory')
    if not manifest['binaries'] or any(v['binary'] not in manifest['binaries'] for v in manifest['services'].values()):
        raise RuntimeExportError('manifest does not cover every runtime service')
    for name, details in manifest['binaries'].items():
        if not re.fullmatch(r'[A-Za-z0-9_-]+', name):
            raise RuntimeExportError('invalid executable name in manifest')
        path = directory / name
        if path.is_symlink() or not os.access(path, os.X_OK) or digest(path) != details['sha256']:
            raise RuntimeExportError('exported executable failed verification: ' + name)
    items = containers(engine, project)
    mounts = runtime_mounts(items)
    if mounts.keys() != manifest['services'].keys():
        raise RuntimeExportError('runtime service set changed; prepare a new export')
    for service, mount in mounts.items():
        state = items[service]['State']
        # Native Podman inspect uses Healthcheck; Docker uses Health.
        statuses = [(state.get(key) or {}).get('Status') for key in ('Health', 'Healthcheck')]
        health_test = (items[service]['Config'].get('Healthcheck') or {}).get('Test', [])
        requires_health = bool(health_test) and health_test[0] != 'NONE'
        if (mount['source'] != str(directory)
                or mount['binary'] != manifest['services'][service]['binary']
                or not state.get('Running')
                or any(state.get(key) for key in ('Paused', 'Restarting', 'OOMKilled', 'Dead'))
                or any(status not in (None, '', 'healthy') for status in statuses)
                or (requires_health and 'healthy' not in statuses)):
            raise RuntimeExportError('runtime mount/start/health verification failed: ' + service)
    return len(mounts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', choices=('docker', 'podman'), default='podman')
    parser.add_argument('--project', required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    prepare = sub.add_parser('export', help='copy executables; do not remount or delete anything')
    prepare.add_argument('--output', required=True)
    prepare.add_argument('--reserve-gib', type=float, default=4)
    check = sub.add_parser('verify', help='check adopted mounts, hashes, process state and health')
    check.add_argument('--directory', required=True)
    check.add_argument('--stack-state', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'export':
            manifest = export(args.engine, args.project, args.output, args.reserve_gib)
            size = sum(v['size'] for v in manifest['binaries'].values())
            print(f'Exported {len(manifest["binaries"])} executables ({size / 1024**3:.2f} GiB). No containers changed.')
        else:
            count = verify(args.engine, args.project, args.directory, args.stack_state)
            print(f'Verified {count} services using exported executables. Check application behavior before cleanup.')
    except RuntimeExportError as error:
        print(str(error) + '. No build data deleted.', file=sys.stderr)
        return 1
    except (ValueError, RuntimeError, OSError, KeyError, TypeError):
        # Engine diagnostics and private manifests may contain credentials.
        print('Runtime operation failed; check project, paths, disk reserve, and service state. No build data deleted.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
