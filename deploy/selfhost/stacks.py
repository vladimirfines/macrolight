#!/usr/bin/env python3
"""Move an initialized stack's identity services into an independent project."""

import argparse
import copy
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import fcntl
import sys
from urllib.parse import urlsplit

from operations import (run, resolve_source, effective_environment, validate_configuration,
                        dependency_layers, dependencies)


AUTH_SERVICES = {"db", "fusionauth", "mailpit"}


def origin(value):
    url = urlsplit(value)
    if (url.scheme != "https" or not url.hostname or url.username or url.password
            or url.path not in ("", "/") or url.query or url.fragment
            or not re.fullmatch(r"[A-Za-z0-9.:[\]-]+", url.netloc)):
        raise ValueError("use an HTTPS origin without a path, credentials or query")
    if not 1 <= (url.port or 443) <= 65535:
        raise ValueError("invalid HTTPS port")
    return "https://" + url.netloc.lower()


def private_json(path, data):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as file:
            json.dump(data, file, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_compose(path, data):
    # Compose's resolved JSON already escapes dollars for subsequent reads.
    # Preserve that representation instead of escaping existing secrets again.
    private_json(path, data)


def private_directory(path):
    path = Path(path).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def split(config, auth_project, auth_origin, directory):
    """Partition normalized Compose JSON without changing persistent identities."""
    auth_origin = origin(auth_origin)
    directory = Path(directory).resolve()
    source = config.get("name")
    if not source or source == auth_project or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", auth_project):
        raise ValueError("source and auth project names must be distinct")
    if not AUTH_SERVICES.issubset(config.get("services", {})):
        raise ValueError("expected initialized db, fusionauth and mailpit services")
    if "auth_gateway" in config["services"]:
        raise ValueError("auth_gateway service name is already used")
    for service in config["services"].values():
        for port in service.get("ports", []):
            if str(port.get("published")) == str(urlsplit(auth_origin).port or 443):
                raise ValueError("the auth HTTPS port is already published by the source stack")
    if not any(v.get("type") == "volume" and v.get("target", "").startswith("/var/lib/postgresql/data")
               for v in config["services"]["db"].get("volumes", [])):
        raise ValueError("auth database must use an existing named data volume")

    macro = copy.deepcopy(config)
    macro.pop("include", None)
    auth = {"name": auth_project, "services": {}}
    for section in ("networks", "volumes"):
        external = {}
        for key, value in config.get(section, {}).items():
            if not value or not value.get("name"):
                raise ValueError("resolved network/volume names are required")
            external[key] = {"external": True, "name": value["name"]}
        macro[section] = copy.deepcopy(external)
        auth[section] = copy.deepcopy(external)
    for section in ("configs", "secrets"):
        if section in config:
            auth[section] = copy.deepcopy(config[section])
    for name in AUTH_SERVICES:
        service = macro["services"].pop(name)
        service.pop("container_name", None)
        service.pop("profiles", None)
        service["restart"] = "unless-stopped"
        for port in service.get("ports", []):
            port["host_ip"] = "127.0.0.1"
        for dependency in service.get("depends_on", {}):
            if dependency not in AUTH_SERVICES:
                raise ValueError("auth service depends on a Macro service: " + dependency)
        auth["services"][name] = service
    # Keep the old gateway's stale mailbox routes from reaching login codes.
    if "auth" not in auth["services"]["mailpit"].get("networks", {}):
        raise ValueError("Mailpit must share the auth network with FusionAuth")
    if "auth" in macro["services"].get("proxy", {}).get("networks", {}):
        raise ValueError("source proxy shares the mailbox auth network; isolate it before migration")
    auth["services"]["mailpit"]["networks"] = {"auth": None}
    fusionauth = auth["services"]["fusionauth"]
    environment = fusionauth.setdefault("environment", {})
    environment.pop("FUSIONAUTH_APP_KICKSTART_FILE", None)
    environment["FUSIONAUTH_APP_URL"] = auth_origin
    fusionauth["volumes"] = [v for v in fusionauth.get("volumes", [])
                             if v.get("target") != "/usr/local/fusionauth/kickstart"]
    auth_networks = fusionauth.get("networks", {})
    if "auth" not in auth_networks:
        raise ValueError("FusionAuth must be attached to the shared auth network")
    port = urlsplit(auth_origin).port or 443
    auth["services"]["auth_gateway"] = {
        "image": config["services"].get("proxy", {}).get("image", "caddy:2-alpine"),
        "restart": "unless-stopped",
        "ports": [{"target": port, "published": str(port), "host_ip": "0.0.0.0", "protocol": "tcp"}],
        "networks": {"auth": None},
        "volumes": [
            {"type": "bind", "source": str(directory / "Caddyfile").replace("$", "$$"), "target": "/etc/caddy/Caddyfile", "read_only": True},
            {"type": "bind", "source": str(directory / "tls").replace("$", "$$"), "target": "/certs", "read_only": True},
        ],
        "depends_on": {"fusionauth": {"condition": "service_healthy"}},
    }
    for name, service in macro["services"].items():
        if "depends_on" in service:
            service["depends_on"] = {key: value for key, value in service["depends_on"].items()
                                     if key not in AUTH_SERVICES}
        env = service.get("environment", {})
        values, source = effective_environment(env)
        if name == "agent_harness_service" or "FUSIONAUTH_PUBLIC_URL" in values or "FUSIONAUTH_PUBLIC_URL" in env:
            values["FUSIONAUTH_PUBLIC_URL"] = auth_origin
            if source == "APP_SECRETS_JSON":
                env[source] = json.dumps(values)
        # Keep plain values in agreement for consumers outside MacroConfig.
        if "FUSIONAUTH_PUBLIC_URL" in env:
            env["FUSIONAUTH_PUBLIC_URL"] = auth_origin
    original = copy.deepcopy(config)
    original["services"] = {name: config["services"][name] for name in AUTH_SERVICES}
    for section in ("networks", "volumes"):
        original[section] = copy.deepcopy(macro.get(section, {}))
    caddy = ("{\n    admin off\n    auto_https disable_redirects\n}\n" + auth_origin
             + " {\n    tls /certs/server.pem /certs/server-key.pem\n"
               "    reverse_proxy fusionauth:9011\n}\n")
    return macro, auth, original, caddy


def containers(engine, project):
    ids = set()
    for label in ("com.docker.compose.project", "io.podman.compose.project"):
        ids.update(run([engine, "ps", "-aq", "--filter", f"label={label}={project}"], text=True).split())
    if not ids:
        return {}
    result = {}
    for item in json.loads(run([engine, "inspect", *sorted(ids)], text=True)):
        labels = item["Config"].get("Labels", {})
        projects = {labels[key] for key in ("com.docker.compose.project", "io.podman.compose.project") if labels.get(key)}
        services = {labels[key] for key in ("com.docker.compose.service", "io.podman.compose.service") if labels.get(key)}
        if projects != {project} or len(services) != 1:
            raise ValueError("container ownership changed")
        name = labels.get("com.docker.compose.service") or labels.get("io.podman.compose.service")
        if name in result:
            raise ValueError("multiple containers found for service " + str(name))
        result[name] = item
    return result


def compose(engine, directory, kind, *args):
    command = [engine, "compose", "-f", str(directory / (kind + ".compose.json"))]
    if args and args[0] in ("up", "restart", "start"):
        config = json.loads(run(command + ["config", "--format", "json"], text=True))
        validate_configuration(config)
    return run([*command, *args], text=True)


def ensure_volume_owner(engine, directory, project):
    auth = json.loads((directory / "auth.compose.json").read_text())
    mount = next(v for v in auth["services"]["db"]["volumes"]
                 if v.get("type") == "volume" and v.get("target", "").startswith("/var/lib/postgresql/data"))
    volume = auth["volumes"][mount["source"]]["name"]
    ids = run([engine, "ps", "-aq", "--filter", "volume=" + volume], text=True).split()
    if not ids:
        return
    writers = 0
    for item in json.loads(run([engine, "inspect", *ids], text=True)):
        if not item["State"]["Running"]:
            continue
        if not any(v.get("Name") == volume and v.get("RW", True) for v in item.get("Mounts", [])):
            continue
        writers += 1
        labels = item["Config"].get("Labels", {})
        projects = {labels[key] for key in ("com.docker.compose.project", "io.podman.compose.project") if labels.get(key)}
        services = {labels[key] for key in ("com.docker.compose.service", "io.podman.compose.service") if labels.get(key)}
        if projects != {project} or services != {"db"} or writers > 1:
            raise ValueError("auth data volume has an unexpected running writer; no containers started")


def ensure_split_ownership(engine, directory, plan):
    if AUTH_SERVICES.intersection(containers(engine, plan["source_project"])):
        raise ValueError("old source auth containers were recreated; inspect them before starting shared auth")
    ensure_volume_owner(engine, directory, plan["auth_project"])


def prepare(args):
    directory = private_directory(args.output)
    if any(directory.iterdir()):
        raise ValueError("output directory must be empty; use a new directory for another plan")
    if args.source_config:
        config = json.loads(Path(args.source_config).read_text())
    else:
        if not args.compose_file or not args.source_project or not args.project_directory:
            raise ValueError("supply source config, or project, project directory and Compose files")
        config = resolve_source(args.engine, args.project_directory, args.source_project,
                                args.compose_file, args.env_file)
    validate_configuration(config)
    macro, auth, original, caddy = split(config, args.auth_project, args.auth_origin, directory)
    current = containers(args.engine, config["name"])
    if not AUTH_SERVICES.issubset(current):
        raise ValueError("all three source auth containers must exist before preparing migration")
    if "localstack" in current and not current["localstack"]["State"]["Running"]:
        raise ValueError("restore and verify LocalStack before preparing a migration")
    for name in AUTH_SERVICES:
        mounts = current[name].get("Mounts", [])
        expected = {v["target"]: auth["volumes"][v["source"]]["name"]
                    for v in auth["services"][name].get("volumes", []) if v.get("type") == "volume"}
        actual = {v["Destination"]: v.get("Name") for v in mounts if v.get("Type") == "volume"}
        if expected != actual:
            raise ValueError("live volume does not match resolved configuration")
    write_compose(directory / "macro.compose.json", macro)
    write_compose(directory / "auth.compose.json", auth)
    write_compose(directory / "original-auth.compose.json", original)
    private_json(directory / "plan.json", {
        "engine": args.engine, "source_project": config["name"], "auth_project": args.auth_project,
        "auth_origin": origin(args.auth_origin),
        "source_ids": {name: current[name]["Id"] for name in AUTH_SERVICES},
        "macro_services": sorted(set(current).intersection(macro["services"]) - {"localstack"}),
        "localstack": ({"id": current["localstack"]["Id"],
                        "started_at": current["localstack"]["State"].get("StartedAt")}
                       if "localstack" in current else None),
    })
    (directory / "Caddyfile").write_text(caddy)
    (directory / "tls").mkdir(mode=0o700)
    for source, target in ((args.cert, "server.pem"), (args.key, "server-key.pem")):
        shutil.copyfile(Path(source).expanduser(), directory / "tls" / target)
        (directory / "tls" / target).chmod(0o600)
    for kind in ("auth", "macro", "original-auth"):
        compose(args.engine, directory, kind, "config", "--quiet")
    print("Prepared private migration plan. No containers changed.")


def ensure_storage_running(engine, plan):
    expected = plan.get("localstack")
    if not expected:
        return
    current = containers(engine, plan["source_project"]).get("localstack", {})
    if (current.get("Id") != expected["id"] or not current.get("State", {}).get("Running")
            or current["State"].get("StartedAt") != expected["started_at"]):
        raise ValueError("LocalStack was stopped or replaced; restore and verify its checkpoint before resuming Macro")


def macro_action(engine, directory, plan, action):
    services = plan["macro_services"]
    if not services:
        return "No application containers recorded; nothing changed.\n"
    if action == "start":
        ensure_split_ownership(engine, directory, plan)
        ensure_storage_running(engine, plan)
        # LocalStack is volatile in the upstream local stack. Never stop or
        # recreate it implicitly while switching applications.
        model = json.loads(compose(engine, directory, "macro", "config", "--format", "json"))
        validate_configuration(model)
        dependency_layers(model, services)
        compose(engine, directory, "auth", "up", "-d", "--no-build", "--no-recreate",
                "--wait", "--wait-timeout", "180")
        if plan.get("localstack"):
            compose(engine, directory, "macro", "up", "-d", "--no-build", "--no-deps",
                    "--no-recreate", "--wait", "--wait-timeout", "180", "localstack")
        infrastructure = set(services).intersection({"postgres", "redis", "search", "kafka"})
        applied = directory / "macro-applied.json"
        for group in (infrastructure, set(services) - infrastructure):
            for layer in dependency_layers(model, group):
                recreate = ("--no-recreate",) if applied.exists() or group == infrastructure else ()
                compose(engine, directory, "macro", "up", "-d", "--no-build", "--no-deps",
                        *recreate, "--wait", "--wait-timeout", "180", *layer)
        private_json(applied, {"auth_origin": plan["auth_origin"]})
        return "Recorded services started in dependency order; inspect status for components without health checks.\n"
    if action == "stop":
        return compose(engine, directory, "macro", "stop", *services)
    return compose(engine, directory, "macro", "ps")


def migrate(directory, plan):
    engine = plan["engine"]
    if (directory / "migrated.json").exists():
        raise ValueError("migration already completed; use auth/macro lifecycle commands")
    if containers(engine, plan["auth_project"]):
        raise ValueError("target auth project already has containers")
    current = containers(engine, plan["source_project"])
    for name, expected in plan["source_ids"].items():
        if current.get(name, {}).get("Id") != expected or not current[name]["State"]["Running"]:
            raise ValueError("source containers changed or stopped; prepare a fresh plan")
    ensure_volume_owner(engine, directory, plan["source_project"])
    for kind in ("auth", "macro", "original-auth"):
        validate_configuration(json.loads(compose(engine, directory, kind, "config", "--format", "json")))
    ids = plan["source_ids"]
    run([engine, "stop", "--time", "45", ids["fusionauth"]])
    try:
        user = next((v.split("=", 1)[1] for v in current["db"]["Config"].get("Env", [])
                     if v.startswith("POSTGRES_USER=")), "postgres")
        backup = run([engine, "exec", ids["db"], "pg_dumpall", "-U", user])
        path = directory / "auth-before-migration.sql"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as file:
            file.write(backup)
        run([engine, "stop", "--time", "45", ids["db"], ids["mailpit"]])
        run([engine, "rm", ids["fusionauth"], ids["db"], ids["mailpit"]])
        ensure_split_ownership(engine, directory, plan)
        compose(engine, directory, "auth", "up", "-d", "--wait", "--wait-timeout", "180")
    except Exception:
        # Remove only the new project's containers, never its external volumes.
        try:
            compose(engine, directory, "auth", "stop")
            compose(engine, directory, "auth", "rm", "-f")
            ensure_volume_owner(engine, directory, plan["source_project"])
            compose(engine, directory, "original-auth", "up", "-d", "--no-build")
        except Exception:
            raise RuntimeError("migration failed and rollback is incomplete; inspect both projects before retrying; no volumes removed") from None
        raise RuntimeError("migration failed; previous auth project restored; no volumes removed") from None
    private_json(directory / "migrated.json", {"auth_project": plan["auth_project"]})
    print("Auth migrated with existing volumes. Use macro start to apply its new public auth URL.")


def repair(directory, plan, service):
    engine = plan["engine"]
    if service not in plan["macro_services"]:
        raise ValueError("repair accepts only recorded Macro services; auth and LocalStack require separate recovery")
    ensure_split_ownership(engine, directory, plan)
    ensure_storage_running(engine, plan)
    model = json.loads(compose(engine, directory, "macro", "config", "--format", "json"))
    validate_configuration(model)
    required = (dependencies(model, service) | set(plan["macro_services"]).intersection(
        {"postgres", "redis", "search", "kafka"})) - {"localstack", service}
    if not required.issubset(plan["macro_services"]):
        raise ValueError("repair would start an unrecorded dependency; prepare a new plan")
    compose(engine, directory, "auth", "up", "-d", "--no-build", "--no-recreate",
            "--wait", "--wait-timeout", "180")
    if plan.get("localstack"):
        compose(engine, directory, "macro", "up", "-d", "--no-build", "--no-deps",
                "--no-recreate", "--wait", "--wait-timeout", "180", "localstack")
    for layer in dependency_layers(model, required):
        compose(engine, directory, "macro", "up", "-d", "--no-build", "--no-deps",
                "--no-recreate", "--wait", "--wait-timeout", "180", *layer)
    compose(engine, directory, "macro", "up", "-d", "--no-build", "--no-deps",
            "--force-recreate", "--wait", "--wait-timeout", "180", service)
    print("Recreated the selected service; no initialization or storage restore performed.")


def doctor(directory, plan):
    engine = plan["engine"]
    failures = []
    for kind in ("auth", "macro"):
        try:
            model = json.loads(compose(engine, directory, kind, "config", "--format", "json"))
            validate_configuration(model)
            print(kind + ": Compose model and known configuration prerequisites valid")
        except (ValueError, RuntimeError) as error:
            failures.append(str(error))
    for project in (plan["auth_project"], plan["source_project"]):
        for name, item in sorted(containers(engine, project).items()):
            state = item.get("State", {})
            health = state.get("Health", {}).get("Status", "no healthcheck")
            print(f"{project}/{name}: running={bool(state.get('Running'))}, health={health}, "
                  f"restarts={item.get('RestartCount', 0)}, oom={bool(state.get('OOMKilled'))}")
    free = shutil.disk_usage(directory).free // (1024 ** 3)
    print(f"State filesystem free: {free} GiB; VM free space and full application readiness are not verified")
    try:
        ensure_storage_running(engine, plan)
    except ValueError as error:
        failures.append(str(error))
    if failures:
        raise ValueError("doctor found issues:\n" + "\n".join(failures))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare")
    prep.add_argument("--engine", choices=("podman", "docker"), default="podman")
    prep.add_argument("--source-config")
    prep.add_argument("--source-project")
    prep.add_argument("--project-directory")
    prep.add_argument("--compose-file", action="append")
    prep.add_argument("--env-file")
    prep.add_argument("--auth-project", default="macrolight-auth")
    prep.add_argument("--auth-origin", required=True)
    prep.add_argument("--cert", required=True)
    prep.add_argument("--key", required=True)
    prep.add_argument("--output", required=True)
    for command in ("migrate", "auth", "macro", "storage-restored", "doctor", "repair"):
        sub = commands.add_parser(command)
        sub.add_argument("--state", required=True)
        if command in ("auth", "macro"):
            sub.add_argument("action", choices=("start", "stop", "status"))
        if command == "repair":
            sub.add_argument("service")
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args)
        return
    directory = Path(args.state).expanduser().resolve()
    plan = json.loads((directory / "plan.json").read_text())
    if args.command == "doctor":
        doctor(directory, plan)
        return
    # One controller per private state directory; a crash releases the lock.
    lock = (directory / ".operation.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise ValueError("another lifecycle operation holds this state directory") from None
    if args.command == "migrate":
        migrate(directory, plan)
        return
    if not (directory / "migrated.json").exists():
        raise ValueError("complete migration before using split-stack lifecycle commands")
    if args.command == "storage-restored":
        current = containers(plan["engine"], plan["source_project"]).get("localstack", {})
        if not current.get("State", {}).get("Running"):
            raise ValueError("LocalStack must be running after a completed checkpoint recovery")
        plan["localstack"] = {"id": current["Id"], "started_at": current["State"].get("StartedAt")}
        private_json(directory / "plan.json", plan)
        print("Recorded restored storage identity; this command did not restore or validate its contents.")
        return
    if args.command == "repair":
        repair(directory, plan, args.service)
        return
    if args.command == "macro":
        output = macro_action(plan["engine"], directory, plan, args.action)
    elif args.action == "start":
        ensure_split_ownership(plan["engine"], directory, plan)
        output = compose(plan["engine"], directory, args.command, "up", "-d", "--no-build",
                         "--no-recreate", "--wait", "--wait-timeout", "180")
    else:
        output = compose(plan["engine"], directory, args.command, "stop" if args.action == "stop" else "ps")
    print(output, end="")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
