import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import operations
import stacks
from test_selfhost import source_config


class OperationsTests(unittest.TestCase):
    def test_generated_environment_is_injected_and_overrides_stale_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory) / "generated.env"
            compose = Path(directory) / "compose.yml"
            env.touch()
            compose.touch()
            with patch.dict(os.environ, {"MACRO_ENV_FILE": "/stale.env"}), \
                    patch.object(operations, "run", return_value='{"services": {}}') as run:
                operations.resolve_source("podman", directory, "trial", [compose], env)
            self.assertEqual(run.call_args.kwargs["env"]["MACRO_ENV_FILE"], str(env.resolve()))
            self.assertEqual(run.call_args.args[0][-5:], ["--env-file", str(env), "config", "--format", "json"])

    def test_preflight_uses_authoritative_json_and_reports_all_missing_keys_without_values(self):
        environment = {"FUSIONAUTH_BASE_URL": "secret-url", "APP_SECRETS_JSON": "{}"}
        model = {"services": {"agent_harness_service": {"environment": environment}}}
        with self.assertRaises(ValueError) as error:
            operations.validate_configuration(model)
        self.assertIn("FUSIONAUTH_BASE_URL", str(error.exception))
        self.assertIn("DATABASE_URL", str(error.exception))
        self.assertIn("APP_SECRETS_JSON", str(error.exception))
        self.assertNotIn("secret-url", str(error.exception))
        for value in ("invalid-secret-json", "[]", "null"):
            with self.assertRaises(ValueError) as error:
                operations.effective_environment({"APP_SECRETS_JSON": value})
            self.assertNotIn(value, str(error.exception))

    def test_partial_credentials_block_mutation_and_preserve_values(self):
        model = {"services": {"api": {"environment": {"PIPEDREAM_CLIENT_ID": "private-value"}}}}
        before = copy.deepcopy(model)
        with patch.object(stacks, "run", return_value=json.dumps(model)) as run:
            with self.assertRaisesRegex(ValueError, "PIPEDREAM_CLIENT_SECRET"):
                stacks.compose("podman", Path("/tmp/state"), "macro", "up", "-d")
        self.assertEqual(run.call_count, 1)
        self.assertEqual(model, before)

    def test_dependency_order_rejects_cycles(self):
        config = {"services": {"search": {}, "api": {"depends_on": {"search": {}}},
                               "worker": {"depends_on": {"api": {}}}}}
        self.assertEqual(operations.dependency_layers(config, config["services"]),
                         [["search"], ["api"], ["worker"]])
        config["services"]["search"]["depends_on"] = {"worker": {}}
        with self.assertRaisesRegex(ValueError, "cycle"):
            operations.dependency_layers(config, config["services"])

    def test_timeout_and_nonzero_exit_do_not_expose_arguments_or_output(self):
        with redirect_stderr(io.StringIO()) as output:
            with self.assertRaises(RuntimeError) as error:
                operations.run([sys.executable, "-c", "import sys; print('private-value'); sys.exit(4)"])
            self.assertNotIn("private-value", str(error.exception) + output.getvalue())
            with patch.object(operations.subprocess, "run", side_effect=subprocess.TimeoutExpired("private-value", 2)):
                with self.assertRaisesRegex(RuntimeError, "exceeded 2s"):
                    operations.run(["podman", "compose", "private-value"], timeout=2)

    def test_atomic_receipt_preserves_previous_state_on_write_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipt.json"
            stacks.private_json(path, {"stage": "prepared"})
            with patch.object(stacks.os, "replace", side_effect=OSError("interrupted")):
                with self.assertRaises(OSError):
                    stacks.private_json(path, {"stage": "migrated"})
            self.assertEqual(json.loads(path.read_text()), {"stage": "prepared"})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_split_updates_authoritative_public_origin_and_isolates_mailbox(self):
        model = source_config()
        model["services"]["api"]["environment"]["APP_SECRETS_JSON"] = json.dumps({
            "FUSIONAUTH_PUBLIC_URL": "https://old.example.test", "ISSUER": "unchanged"})
        model["services"]["mailpit"]["networks"]["services"] = None
        model["services"]["proxy"] = {"networks": {"services": None}}
        macro, auth, _, _ = stacks.split(model, "shared-auth", "https://auth.example.test:23010", "/tmp/state")
        values = json.loads(macro["services"]["api"]["environment"]["APP_SECRETS_JSON"])
        self.assertEqual(values["FUSIONAUTH_PUBLIC_URL"], "https://auth.example.test:23010")
        self.assertEqual(values["ISSUER"], "unchanged")
        self.assertEqual(auth["services"]["mailpit"]["networks"], {"auth": None})
        model["services"]["proxy"]["networks"]["auth"] = None
        with self.assertRaisesRegex(ValueError, "mailbox"):
            stacks.split(model, "shared-auth", "https://auth.example.test:23010", "/tmp/state")

    def test_conflicting_engine_labels_are_rejected(self):
        item = {"Config": {"Labels": {"com.docker.compose.project": "trial",
                                     "io.podman.compose.project": "other",
                                     "com.docker.compose.service": "api"}}}
        with patch.object(stacks, "run", side_effect=["id", "id", json.dumps([item])]):
            with self.assertRaisesRegex(ValueError, "ownership"):
                stacks.containers("podman", "trial")

    def test_failed_infrastructure_readiness_never_starts_application(self):
        model = {"services": {"search": {}, "api": {}}}
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(stacks, "ensure_split_ownership"), \
                patch.object(stacks, "ensure_storage_running"):
            calls = []
            def compose(engine, state, kind, *args):
                calls.append((kind, args))
                if args[0] == "config":
                    return json.dumps(model)
                if kind == "macro" and args[-1] == "search":
                    raise RuntimeError("not ready")
            with patch.object(stacks, "compose", side_effect=compose):
                with self.assertRaisesRegex(RuntimeError, "not ready"):
                    stacks.macro_action("podman", Path(directory), {"macro_services": ["api", "search"]}, "start")
            self.assertFalse(any(args[0] == "up" and args[-1] == "api" for _, args in calls))

    def test_repair_checks_infrastructure_then_recreates_only_requested_service(self):
        model = {"services": {"search": {}, "api": {"depends_on": {"search": {}}}}}
        plan = {"engine": "podman", "macro_services": ["search", "api"]}
        with patch.object(stacks, "ensure_split_ownership"), patch.object(stacks, "ensure_storage_running"), \
                patch.object(stacks, "compose", return_value=json.dumps(model)) as command:
            stacks.repair(Path("/tmp/state"), plan, "api")
        changes = [call.args[2:] for call in command.call_args_list if "up" in call.args]
        self.assertEqual([call[-1] for call in changes[1:]], ["search", "api"])
        self.assertNotIn("--force-recreate", changes[1])
        self.assertIn("--force-recreate", changes[2])
        self.assertIn("--wait", changes[2])

    def test_unhealthy_emulator_blocks_targeted_recreation(self):
        model = {"services": {"api": {}, "localstack": {}}}
        plan = {"engine": "podman", "macro_services": ["api"], "localstack": {"id": "storage-id"}}
        changes = []
        def compose(engine, directory, kind, *args):
            if args[0] == "config":
                return json.dumps(model)
            changes.append(args)
            if args[-1] == "localstack":
                raise RuntimeError("storage is unhealthy")
        with patch.object(stacks, "ensure_split_ownership"), patch.object(stacks, "ensure_storage_running"), \
                patch.object(stacks, "compose", side_effect=compose):
            with self.assertRaisesRegex(RuntimeError, "unhealthy"):
                stacks.repair(Path("/tmp/state"), plan, "api")
        self.assertFalse(any("--force-recreate" in args for args in changes))
