import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stacks
import openwebui


def source_config():
    return {
        "name": "macro-test",
        "networks": {"auth": {"name": "auth-test"}, "internal": {"name": "macro-test_internal"}},
        "volumes": {"db_data": {"name": "existing-auth-db"}, "config": {"name": "existing-auth-config"}},
        "services": {
            "db": {"image": "postgres:16.0-bookworm", "networks": {"internal": None},
                   "environment": {"POSTGRES_PASSWORD": "literal$$part$${keep}"},
                   "volumes": [{"type": "volume", "source": "db_data", "target": "/var/lib/postgresql/data"}]},
            "fusionauth": {"image": "fusionauth/fusionauth-app:1.62.1", "networks": {"auth": None, "internal": None},
                           "depends_on": {"db": {"condition": "service_started"}},
                           "environment": {"FUSIONAUTH_APP_KICKSTART_FILE": "/kickstart.json"},
                           "volumes": [{"type": "volume", "source": "config", "target": "/usr/local/fusionauth/config"},
                                       {"type": "bind", "source": "/example/kickstart", "target": "/usr/local/fusionauth/kickstart"}]},
            "mailpit": {"image": "axllent/mailpit:v1.20", "networks": {"auth": None},
                        "ports": [{"target": 8025, "published": "23008", "protocol": "tcp"}]},
            "api": {"image": "example/api:local", "networks": {"auth": None},
                    "depends_on": {"fusionauth": {"condition": "service_started"}},
                    "environment": {"FUSIONAUTH_PUBLIC_URL": "https://old.example.test", "ISSUER": "existing-issuer"}},
        },
    }


class SplitTests(unittest.TestCase):
    def test_separates_lifecycle_without_changing_identity_or_volume_names(self):
        source = source_config()
        before = copy.deepcopy(source)
        macro, auth, original, caddy = stacks.split(source, "shared-auth", "https://auth.example.test:23010", "/tmp/state")
        self.assertEqual(source, before)
        self.assertEqual(set(macro["services"]), {"api"})
        self.assertEqual(set(auth["services"]), {"db", "fusionauth", "mailpit", "auth_gateway"})
        self.assertEqual(original["name"], "macro-test")
        self.assertEqual(auth["volumes"]["db_data"], {"external": True, "name": "existing-auth-db"})
        self.assertTrue(all(v["external"] for v in macro["networks"].values()))
        self.assertEqual(macro["services"]["api"]["depends_on"], {})
        self.assertEqual(macro["services"]["api"]["environment"]["ISSUER"], "existing-issuer")
        self.assertEqual(macro["services"]["api"]["environment"]["FUSIONAUTH_PUBLIC_URL"], "https://auth.example.test:23010")
        self.assertNotIn("FUSIONAUTH_APP_KICKSTART_FILE", auth["services"]["fusionauth"]["environment"])
        self.assertEqual(auth["services"]["mailpit"]["ports"][0]["host_ip"], "127.0.0.1")
        self.assertNotIn("mailpit", caddy)
        self.assertIn("reverse_proxy fusionauth:9011", caddy)

    def test_refuses_project_collision_and_nonpersistent_database(self):
        with self.assertRaises(ValueError):
            stacks.split(source_config(), "macro-test", "https://auth.example.test:23010", "/tmp/state")
        source = source_config()
        source["services"]["db"]["volumes"] = []
        with self.assertRaises(ValueError):
            stacks.split(source, "shared-auth", "https://auth.example.test:23010", "/tmp/state")

    def test_refuses_plaintext_and_caddy_config_injection(self):
        for value in ("http://auth.example.test", "https://auth.example.test/path", "https://auth.example.test\n{}"):
            with self.assertRaises(ValueError):
                stacks.origin(value)

    def test_compose_render_preserves_literal_secret_and_lifecycle(self):
        binary = os.environ.get("MACROLIGHT_COMPOSE")
        if not binary:
            self.skipTest("set MACROLIGHT_COMPOSE to a Compose V2 executable")
        with tempfile.TemporaryDirectory() as directory:
            macro, auth, original, _ = stacks.split(source_config(), "shared-auth", "https://auth.example.test:23010", directory)
            for name, model in (("macro", macro), ("auth", auth), ("original", original)):
                path = Path(directory) / (name + ".json")
                stacks.write_compose(path, model)
                rendered = subprocess.run([binary, "-f", str(path), "config", "--format", "json"],
                                          check=True, capture_output=True, text=True)
                result = json.loads(rendered.stdout)
                if name != "macro":
                    self.assertEqual(result["services"]["db"]["environment"]["POSTGRES_PASSWORD"],
                                     source_config()["services"]["db"]["environment"]["POSTGRES_PASSWORD"])
                self.assertEqual(result["name"], model["name"])

    def test_failed_migration_restores_old_project_without_removing_volumes(self):
        current = {name: {"Id": name + "-id", "State": {"Running": True}, "Config": {"Env": []}}
                   for name in stacks.AUTH_SERVICES}
        plan = {"engine": "podman", "source_project": "macro-test", "auth_project": "shared-auth",
                "source_ids": {name: item["Id"] for name, item in current.items()}}
        calls = []
        def fake_compose(engine, directory, kind, *args):
            calls.append((kind, args))
            if args[0] == "config":
                return '{"services": {}}'
            if kind == "auth" and args[0] == "up":
                raise RuntimeError("startup failed")
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(stacks, "containers", side_effect=[{}, current]), \
                patch.object(stacks, "ensure_volume_owner"), \
                patch.object(stacks, "ensure_split_ownership"), \
                patch.object(stacks, "run", return_value=b"backup") as command, \
                patch.object(stacks, "compose", side_effect=fake_compose):
            with self.assertRaisesRegex(RuntimeError, "previous auth project restored"):
                stacks.migrate(Path(directory), plan)
            self.assertEqual(calls[-1], ("original-auth", ("up", "-d", "--no-build")))
            self.assertEqual((Path(directory) / "auth-before-migration.sql").read_bytes(), b"backup")
            self.assertFalse((Path(directory) / "migrated.json").exists())
            for call in command.call_args_list:
                self.assertNotIn("--volumes", call.args[0])
                self.assertNotIn("-v", call.args[0])

    def test_recreated_source_auth_blocks_lifecycle_start(self):
        plan = {"source_project": "macro-test", "auth_project": "shared-auth"}
        with patch.object(stacks, "containers", return_value={"db": {}}), \
                patch.object(stacks, "ensure_volume_owner") as volumes:
            with self.assertRaisesRegex(ValueError, "were recreated"):
                stacks.ensure_split_ownership("podman", Path("/tmp/state"), plan)
            volumes.assert_not_called()

    def test_macro_switching_never_stops_or_recreates_volatile_storage(self):
        plan = {"macro_services": ["api"], "auth_origin": "https://auth.example.test"}
        with tempfile.TemporaryDirectory() as directory, patch.object(stacks, "ensure_split_ownership"), \
                patch.object(stacks, "ensure_storage_running"), \
                patch.object(stacks, "compose", return_value=json.dumps({"services": {"api": {}}})) as command:
            stacks.macro_action("podman", Path(directory), plan, "stop")
            self.assertEqual(command.call_args.args[2:], ("macro", "stop", "api"))
            stacks.macro_action("podman", Path(directory), plan, "start")
            self.assertEqual(command.call_args.args[2:], ("macro", "up", "-d", "--no-build", "--no-deps", "--wait", "--wait-timeout", "180", "api"))
            stacks.macro_action("podman", Path(directory), plan, "start")
            self.assertIn("--no-recreate", command.call_args.args)

    def test_restarted_volatile_storage_blocks_resume(self):
        plan = {"source_project": "macro-test", "localstack": {"id": "storage-id", "started_at": "original"}}
        restarted = {"localstack": {"Id": "storage-id", "State": {"Running": True, "StartedAt": "later"}}}
        with patch.object(stacks, "containers", return_value=restarted):
            with self.assertRaisesRegex(ValueError, "restore and verify"):
                stacks.ensure_storage_running("podman", plan)

    def test_foreign_running_database_writer_is_rejected(self):
        _, auth, _, _ = stacks.split(source_config(), "shared-auth", "https://auth.example.test:23010", "/tmp/state")
        writer = {"State": {"Running": True}, "Mounts": [{"Name": "existing-auth-db", "RW": True}],
                  "Config": {"Labels": {"com.docker.compose.project": "another-project", "com.docker.compose.service": "db"}}}
        with tempfile.TemporaryDirectory() as directory, patch.object(stacks, "run", side_effect=["other-db", json.dumps([writer])]):
            stacks.private_json(Path(directory) / "auth.compose.json", auth)
            with self.assertRaisesRegex(ValueError, "unexpected running writer"):
                stacks.ensure_volume_owner("podman", Path(directory), "shared-auth")


class OidcTests(unittest.TestCase):
    def test_provision_retry_reuses_identity_and_refuses_registration_drift(self):
        tenant = "00000000-0000-4000-8000-000000000001"
        metadata = {"issuer": "existing-issuer", "code_challenge_methods_supported": ["S256"],
                    "id_token_signing_alg_values_supported": ["RS256"]}
        for field in ("authorization_endpoint", "token_endpoint", "userinfo_endpoint", "jwks_uri"):
            metadata[field] = "https://auth.example.test/" + field
        stored = {}
        writes = []

        class FakeApi:
            def __init__(self, *args):
                pass

            def request(self, method, path, data=None, **kwargs):
                if "openid-configuration" in path:
                    return metadata
                if method == "GET":
                    return stored.get(path)
                writes.append(path)
                stored[path.replace("/generate", "")] = copy.deepcopy(data)
                return data

        with tempfile.TemporaryDirectory() as directory, patch.object(openwebui, "Api", FakeApi):
            key = Path(directory) / "api-key"
            key.write_text("test-key")
            args = SimpleNamespace(output=str(Path(directory) / "state"), tenant=tenant,
                                   auth_origin="https://auth.example.test", webui_url="https://webui.example.test",
                                   service="webui", api_key_file=str(key), ca_file=None, webui_container=None)
            openwebui.provision(args)
            receipt = json.loads((Path(args.output) / "client.json").read_text())
            openwebui.provision(args)
            self.assertEqual(len(writes), 2)
            stored["/api/application/" + receipt["client_id"]]["application"]["registrationConfiguration"]["enabled"] = True
            with self.assertRaisesRegex(ValueError, "differs from the saved client"):
                openwebui.provision(args)
            self.assertEqual(len(writes), 2)

    def test_webui_has_dedicated_asymmetric_keys_and_registered_users_only(self):
        app = openwebui.application("tenant", "client", "test-secret", "new-rsa-key", "https://webui.example.test")
        self.assertTrue(app["oauthConfiguration"]["requireRegistration"])
        self.assertEqual(app["oauthConfiguration"]["proofKeyForCodeExchangePolicy"], "Required")
        self.assertEqual(app["oauthConfiguration"]["authorizedRedirectURLs"], ["https://webui.example.test/oauth/oidc/callback"])
        self.assertEqual(app["jwtConfiguration"]["idTokenKeyId"], "new-rsa-key")
        self.assertFalse(app["registrationConfiguration"]["enabled"])

    def test_overlay_keeps_login_recovery_and_does_not_replace_data(self):
        config = openwebui.overlay("webui", "https://auth.example.test", "tenant", "client", "test-secret", "https://webui.example.test")
        service = config["services"]["webui"]
        self.assertNotIn("volumes", service)
        self.assertNotIn("networks", service)
        self.assertNotIn("WEBUI_SECRET_KEY", service["environment"])
        self.assertEqual(service["environment"]["ENABLE_LOGIN_FORM"], "true")
        self.assertEqual(service["environment"]["OAUTH_MERGE_ACCOUNTS_BY_EMAIL"], "false")
        self.assertEqual(service["environment"]["OPENID_PROVIDER_URL"], "https://auth.example.test/tenant/.well-known/openid-configuration")

    def test_rejects_discovery_metadata_pointing_to_stopped_macro_or_plaintext(self):
        metadata = {"issuer": "existing-issuer", "code_challenge_methods_supported": ["S256"],
                    "id_token_signing_alg_values_supported": ["RS256"]}
        for field in ("authorization_endpoint", "token_endpoint", "userinfo_endpoint", "jwks_uri"):
            metadata[field] = "https://auth.example.test/" + field
        openwebui.verify_metadata(metadata, "https://auth.example.test")
        metadata["token_endpoint"] = "http://fusionauth:9011/oauth2/token"
        with self.assertRaises(ValueError):
            openwebui.verify_metadata(metadata, "https://auth.example.test")


if __name__ == "__main__":
    unittest.main()
