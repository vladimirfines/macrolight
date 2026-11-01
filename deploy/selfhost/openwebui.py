#!/usr/bin/env python3
"""Provision a dedicated FusionAuth OIDC client; never reconfigure Macro's client."""

import argparse
import json
from pathlib import Path
import secrets
import ssl
import sys
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener
import uuid

from stacks import origin, private_directory, private_json, run, write_compose


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, new_url):
        return None


class Api:
    def __init__(self, base, tenant, key, ca_file=None):
        self.base = origin(base)
        self.tenant = str(uuid.UUID(tenant))
        self.key = key
        context = ssl.create_default_context()
        if ca_file:
            context.load_verify_locations(cafile=str(Path(ca_file).expanduser()))
        self.opener = build_opener(NoRedirect(), HTTPSHandler(context=context))

    def request(self, method, path, data=None, authenticated=True, missing=False):
        headers = {"Content-Type": "application/json"}
        if authenticated:
            headers.update({"Authorization": self.key, "X-FusionAuth-TenantId": self.tenant})
        request = Request(self.base + path, headers=headers, method=method,
                          data=None if data is None else json.dumps(data).encode())
        try:
            with self.opener.open(request, timeout=20) as response:
                return json.load(response)
        except HTTPError as error:
            if missing and error.code == 404:
                return None
            raise RuntimeError(f"FusionAuth request failed ({error.code}); response omitted") from None


def application(tenant, client_id, client_secret, signing_key, webui_url):
    return {
        "tenantId": tenant,
        "name": "Open WebUI",
        "data": {"macrolightManaged": True},
        "oauthConfiguration": {
            "clientId": client_id, "clientSecret": client_secret,
            "authorizedRedirectURLs": [origin(webui_url) + "/oauth/oidc/callback"],
            "authorizedURLValidationPolicy": "ExactMatch",
            "clientAuthenticationPolicy": "Required",
            "proofKeyForCodeExchangePolicy": "Required",
            "enabledGrants": ["authorization_code", "refresh_token"],
            "generateRefreshTokens": True, "requireRegistration": True,
            "scopeHandlingPolicy": "Strict", "relationship": "FirstParty",
            "logoutBehavior": "RedirectOnly",
        },
        "jwtConfiguration": {
            "enabled": True, "accessTokenKeyId": signing_key, "idTokenKeyId": signing_key,
            "timeToLiveInSeconds": 3600,
        },
        "registrationConfiguration": {"enabled": False},
        "passwordlessConfiguration": {"enabled": True},
    }


def overlay(service, auth_origin, tenant, client_id, secret, webui_url, ca_bundle=None):
    values = {
        "WEBUI_URL": origin(webui_url),
        "ENABLE_OAUTH": "true", "ENABLE_OAUTH_PERSISTENT_CONFIG": "false",
        "OAUTH_PROVIDER_NAME": "FusionAuth",
        "OAUTH_CLIENT_ID": client_id, "OAUTH_CLIENT_SECRET": secret,
        "OPENID_PROVIDER_URL": origin(auth_origin) + "/" + tenant + "/.well-known/openid-configuration",
        "OPENID_REDIRECT_URI": origin(webui_url) + "/oauth/oidc/callback",
        "OAUTH_SCOPES": "openid email profile",
        "OAUTH_CODE_CHALLENGE_METHOD": "S256",
        "OAUTH_TOKEN_ENDPOINT_AUTH_METHOD": "client_secret_basic",
        "ENABLE_OAUTH_SIGNUP": "true", "DEFAULT_USER_ROLE": "user",
        "OAUTH_MERGE_ACCOUNTS_BY_EMAIL": "false", "ENABLE_LOGIN_FORM": "true",
        "OAUTH_AUTO_REDIRECT": "false",
    }
    config = {"environment": values}
    if ca_bundle:
        values["SSL_CERT_FILE"] = "/etc/macrolight/ca-bundle.pem"
        values["REQUESTS_CA_BUNDLE"] = "/etc/macrolight/ca-bundle.pem"
        config["volumes"] = [{"type": "bind", "source": str(ca_bundle).replace("$", "$$"),
                              "target": "/etc/macrolight/ca-bundle.pem", "read_only": True}]
    return {"services": {service: config}}


def verify_metadata(document, auth_origin):
    if not document.get("issuer"):
        raise ValueError("discovery metadata has no issuer")
    for field in ("authorization_endpoint", "token_endpoint", "userinfo_endpoint", "jwks_uri"):
        value = document.get(field, "")
        if not value.startswith(origin(auth_origin) + "/"):
            raise ValueError("discovery endpoint is not on the public auth origin: " + field)
    if "S256" not in document.get("code_challenge_methods_supported", []):
        raise ValueError("provider must support S256 PKCE")
    if "RS256" not in document.get("id_token_signing_alg_values_supported", []):
        raise ValueError("provider must advertise RS256 ID tokens")


def provision(args):
    directory = private_directory(args.output)
    receipt_path = directory / "client.json"
    identity = {"tenant": str(uuid.UUID(args.tenant)), "auth_origin": origin(args.auth_origin),
                "webui_url": origin(args.webui_url), "service": args.service}
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text())
        if any(receipt.get(key) != value for key, value in identity.items()):
            raise ValueError("existing client receipt belongs to a different configuration")
    else:
        receipt = {**identity, "client_id": str(uuid.uuid4()), "key_id": str(uuid.uuid4()),
                   "client_secret": secrets.token_urlsafe(48)}
        private_json(receipt_path, receipt)
    ca_bundle = None
    if args.ca_file:
        if not args.webui_container:
            raise ValueError("custom CA requires --webui-container to preserve its existing trust roots")
        roots = run([args.engine, "exec", args.webui_container, "python", "-c",
                     "import ssl; from pathlib import Path; print(Path(ssl.get_default_verify_paths().cafile).read_text())"], text=True)
        ca_bundle = directory / "ca-bundle.pem"
        ca_bundle.write_text(roots + "\n" + Path(args.ca_file).expanduser().read_text())
        ca_bundle.chmod(0o644)
    api = Api(args.auth_origin, args.tenant, Path(args.api_key_file).expanduser().read_text().strip(), args.ca_file)
    discovery_path = "/" + identity["tenant"] + "/.well-known/openid-configuration"
    metadata = api.request("GET", discovery_path, authenticated=False)
    verify_metadata(metadata, identity["auth_origin"])
    # Keep the tenant issuer and the old application's signing keys untouched.
    # Local Macro uses symmetric JWTs; this new app needs public JWKS verification.
    key = api.request("GET", "/api/key/" + receipt["key_id"], missing=True)
    if key is None:
        api.request("POST", "/api/key/generate/" + receipt["key_id"], {
            "key": {"name": "Open WebUI OIDC", "algorithm": "RS256", "length": 2048}})
    elif key.get("key", {}).get("algorithm") != "RS256":
        raise ValueError("recorded OIDC signing key is not RS256")
    expected = application(identity["tenant"], receipt["client_id"], receipt["client_secret"],
                           receipt["key_id"], identity["webui_url"])
    existing = api.request("GET", "/api/application/" + receipt["client_id"], missing=True)
    if existing is None:
        api.request("POST", "/api/application/" + receipt["client_id"], {"application": expected})
    else:
        app = existing["application"]
        if (not app.get("data", {}).get("macrolightManaged")
                or app.get("tenantId") != identity["tenant"]
                or any(app.get("oauthConfiguration", {}).get(key) != value
                       for key, value in expected["oauthConfiguration"].items())
                or any(app.get("jwtConfiguration", {}).get(key) != value
                       for key, value in expected["jwtConfiguration"].items())
                or app.get("registrationConfiguration", {}).get("enabled") is not False
                or app.get("passwordlessConfiguration", {}).get("enabled") is not True):
            raise ValueError("existing application differs from the saved client; no settings overwritten")
    write_compose(directory / "openwebui.compose.json", overlay(
        args.service, identity["auth_origin"], identity["tenant"], receipt["client_id"],
        receipt["client_secret"], identity["webui_url"], ca_bundle))
    print("OIDC client prepared. Register approved users for this application in FusionAuth.")
    print("Apply openwebui.compose.json over the existing WebUI Compose file; preserve its data volume and session key.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("provision")
    prep.add_argument("--auth-origin", required=True)
    prep.add_argument("--tenant", required=True)
    prep.add_argument("--api-key-file", required=True)
    prep.add_argument("--webui-url", required=True)
    prep.add_argument("--service", required=True)
    prep.add_argument("--output", required=True)
    prep.add_argument("--ca-file")
    prep.add_argument("--webui-container")
    prep.add_argument("--engine", choices=("podman", "docker"), default="podman")
    check = sub.add_parser("check")
    check.add_argument("--auth-origin", required=True)
    check.add_argument("--tenant", required=True)
    check.add_argument("--ca-file")
    args = parser.parse_args()
    if args.command == "provision":
        provision(args)
    else:
        api = Api(args.auth_origin, args.tenant, "", args.ca_file)
        metadata = api.request("GET", "/" + api.tenant + "/.well-known/openid-configuration", authenticated=False)
        verify_metadata(metadata, args.auth_origin)
        print("HTTPS discovery and public endpoints verified; browser login has not been tested.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
