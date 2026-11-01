import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import runtime


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'target' / 'debug'
        self.source.mkdir(parents=True)
        self.binary = self.source / 'service'
        self.binary.write_bytes(b'\x7fELF\x02\x01' + b'\x00' * 12 + b'\xb7\x00' + b'payload')
        self.binary.chmod(0o755)
        self.output = self.root / 'runtime'
        self.items = {'test-service': {
            'Config': {'Cmd': ['/app/out/service']},
            'Mounts': [{'Type': 'bind', 'Source': str(self.source), 'Destination': '/app/out', 'RW': False}],
            'State': {'Running': True, 'Health': {'Status': 'healthy'}},
        }}
        self.inspection = patch.object(runtime, 'containers', return_value=self.items)
        self.inspection.start()
        self.addCleanup(self.inspection.stop)

    def test_export_is_independent_of_build_tree_and_verified_after_remount(self):
        (self.source / 'deps').mkdir()
        (self.source / 'deps' / 'intermediate').write_bytes(b'compiler cache')
        runtime.export('podman', 'trial', self.output, 0)
        self.assertEqual({p.name for p in self.output.iterdir()}, {'service', 'manifest.json'})
        self.assertNotEqual(self.binary.stat().st_ino, (self.output / 'service').stat().st_ino)
        state = self.root / 'stack.json'
        state.write_text(json.dumps({'binaries_dir': str(self.output)}))
        with self.assertRaisesRegex(ValueError, 'mount/start/health'):
            runtime.verify('podman', 'trial', self.output, state)
        self.items['test-service']['Mounts'][0]['Source'] = str(self.output)
        self.binary.unlink()
        self.assertEqual(runtime.verify('podman', 'trial', self.output, state), 1)
        (self.output / 'service').chmod(0o755)
        (self.output / 'service').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'verification'):
            runtime.verify('podman', 'trial', self.output, state)

    def test_changed_output_is_not_published(self):
        original_copy = runtime.shutil.copyfile

        def changing_copy(source, destination):
            result = original_copy(source, destination)
            source.write_bytes(source.read_bytes() + b'build changed')
            return result

        with patch.object(runtime.shutil, 'copyfile', side_effect=changing_copy):
            with self.assertRaisesRegex(ValueError, 'changed during export'):
                runtime.export('podman', 'trial', self.output, 0)
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.root.glob('.runtime-*')), [])

    def test_disk_reserve_existing_output_nix_and_missing_binary_fail_without_publication(self):
        with self.assertRaisesRegex(ValueError, 'reserve'):
            runtime.export('podman', 'trial', self.output, 10**8)
        self.output.mkdir()
        with self.assertRaisesRegex(ValueError, 'already exists'):
            runtime.export('podman', 'trial', self.output, 0)
        self.output.rmdir()
        self.items['test-service']['Mounts'].append({'Destination': '/nix/store'})
        with self.assertRaisesRegex(ValueError, 'store closure'):
            runtime.export('podman', 'trial', self.output, 0)
        self.items['test-service']['Mounts'].pop()
        self.binary.unlink()
        with self.assertRaisesRegex(ValueError, 'missing'):
            runtime.export('podman', 'trial', self.output, 0)
        self.assertFalse(self.output.exists())

    def test_native_podman_health_and_missing_configured_health_fail_closed(self):
        runtime.export('podman', 'trial', self.output, 0)
        state = self.root / 'stack.json'
        state.write_text(json.dumps({'binaries_dir': str(self.output)}))
        item = self.items['test-service']
        item['Mounts'][0]['Source'] = str(self.output)
        item['State'].pop('Health')
        item['State']['Healthcheck'] = {'Status': 'unhealthy'}
        with self.assertRaisesRegex(ValueError, 'health'):
            runtime.verify('podman', 'trial', self.output, state)
        item['State']['Healthcheck']['Status'] = 'healthy'
        self.assertEqual(runtime.verify('podman', 'trial', self.output, state), 1)
        item['State']['Healthcheck'] = None
        item['State']['Health'] = None
        item['Config']['Healthcheck'] = None
        self.assertEqual(runtime.verify('podman', 'trial', self.output, state), 1)
        item['State']['Healthcheck'] = {'Status': ''}
        item['Config']['Healthcheck'] = {'Test': ['CMD', 'curl', 'localhost/health']}
        with self.assertRaisesRegex(ValueError, 'health'):
            runtime.verify('podman', 'trial', self.output, state)
        item['State']['Healthcheck']['Status'] = 'healthy'
        item['State']['Paused'] = True
        with self.assertRaisesRegex(ValueError, 'health'):
            runtime.verify('podman', 'trial', self.output, state)

    def test_verification_rejects_wrong_project_unadopted_state_and_unhealthy_service(self):
        runtime.export('podman', 'trial', self.output, 0)
        state = self.root / 'stack.json'
        state.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'another project'):
            runtime.verify('podman', 'other', self.output, state)
        with self.assertRaisesRegex(ValueError, 'not adopted'):
            runtime.verify('podman', 'trial', self.output, state)
        state.write_text(json.dumps({'binaries_dir': str(self.output)}))
        self.items['test-service']['Mounts'][0]['Source'] = str(self.output)
        self.items['test-service']['State']['Health']['Status'] = 'unhealthy'
        with self.assertRaisesRegex(ValueError, 'health'):
            runtime.verify('podman', 'trial', self.output, state)


if __name__ == '__main__':
    unittest.main()
