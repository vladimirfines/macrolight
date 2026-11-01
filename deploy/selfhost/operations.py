"""Shared, bounded Compose operations for self-host administration."""

import json
import os
from pathlib import Path
import subprocess
import sys
import time


def run(args, **kwargs):
    # Never print argv: API keys and resolved settings can appear in arguments.
    timeout = kwargs.pop("timeout", 240)
    stage = " ".join(args[:2])
    started = time.monotonic()
    print(f"Starting {stage} (deadline {timeout}s)", file=sys.stderr, flush=True)
    try:
        result = subprocess.run(args, capture_output=True, timeout=timeout, **kwargs)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{stage} exceeded {timeout}s; inspect owned containers before retrying") from None
    except OSError:
        raise RuntimeError(f"cannot execute {stage}; check the selected engine and client") from None
    if result.returncode:
        # Both stderr and stdout may contain credentials. Do not echo them.
        raise RuntimeError(f"{stage} failed (exit {result.returncode}); diagnostic output withheld")
    print(f"Finished {stage} in {time.monotonic() - started:.1f}s", file=sys.stderr, flush=True)
    return result.stdout


def resolve_source(engine, project_directory, project, files, env_file):
    if not env_file:
        raise ValueError("--env-file is required when resolving source Compose files")
    path = Path(env_file).expanduser().resolve(strict=True)
    command = [engine, "compose", "--project-directory", str(Path(project_directory).expanduser().resolve()),
               "-p", project]
    for file in files:
        command += ["-f", str(Path(file).expanduser().resolve(strict=True))]
    command += ["--env-file", str(path), "config", "--format", "json"]
    environment = {**os.environ, "MACRO_ENV_FILE": str(path)}
    return json.loads(run(command, env=environment, text=True))


def effective_environment(environment):
    """Mirror MacroConfig's authoritative JSON behavior without leaking values."""
    if "APP_SECRETS_JSON" not in environment:
        return environment, "environment"
    try:
        values = json.loads(environment["APP_SECRETS_JSON"])
    except (ValueError, TypeError):
        raise ValueError("APP_SECRETS_JSON must contain a JSON object; values omitted") from None
    if not isinstance(values, dict):
        raise ValueError("APP_SECRETS_JSON must contain a JSON object; values omitted")
    return values, "APP_SECRETS_JSON"


def validate_configuration(config):
    """Check the known boot prerequisites, not a replacement for service config types."""
    errors = []
    for name, service in config.get("services", {}).items():
        values, source = effective_environment(service.get("environment", {}))
        if name == "agent_harness_service":
            required = ("DATABASE_URL", "REDIS_URI", "KAFKA_BROKERS", "FUSIONAUTH_BASE_URL",
                        "FUSIONAUTH_CLIENT_ID", "FUSIONAUTH_API_KEY_SECRET_KEY", "FUSIONAUTH_CLIENT_SECRET_KEY",
                        "MCP_OAUTH_KMS_KEY_ID")
            missing = [key for key in required if not str(values.get(key) or "").strip()]
            if missing:
                errors.append(f"{name} ({source}): missing " + ", ".join(missing))
        pipedream = ("PIPEDREAM_CLIENT_ID", "PIPEDREAM_CLIENT_SECRET", "PIPEDREAM_PROJECT_ID")
        present = [key for key in pipedream if str(values.get(key) or "").strip()]
        if present and len(present) != len(pipedream):
            errors.append(f"{name} ({source}): incomplete Pipedream credentials; missing "
                          + ", ".join(key for key in pipedream if key not in present))
    if errors:
        raise ValueError("configuration preflight failed:\n" + "\n".join(errors))


def dependency_layers(config, selected):
    """Order only the recorded services; never pull in unowned dependencies."""
    services = config["services"]
    remaining = set(selected)
    if remaining - services.keys():
        raise ValueError("recorded services are absent from the prepared Compose model")
    layers = []
    while remaining:
        ready = sorted(name for name in remaining
                       if not remaining.intersection(services[name].get("depends_on", {})))
        if not ready:
            raise ValueError("dependency cycle in recorded Compose services")
        layers.append(ready)
        remaining.difference_update(ready)
    return layers


def dependencies(config, service):
    found = set()
    pending = list(config["services"][service].get("depends_on", {}))
    while pending:
        name = pending.pop()
        if name not in found:
            if name not in config["services"]:
                raise ValueError("dependency is absent from the prepared Compose model")
            found.add(name)
            pending.extend(config["services"][name].get("depends_on", {}))
    return found
