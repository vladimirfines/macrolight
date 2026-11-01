# Shared sign-in compatibility

Checked against upstream documentation/source on 2026-11-01. This is a
capability review, not a live test of the installed images or licenses.
Open WebUI is the first prepared integration; the other apps are not modified.

| Application | Generic OIDC / shared sign-in | Next action |
| --- | --- | --- |
| Open WebUI | Native generic OIDC; one configured OIDC provider | Use this directory's dedicated client and overlay; complete the live login checks. |
| AFFiNE | Generic OIDC documented in self-host admin settings | Add a separate client; callback `<origin>/oauth/callback`. Verify the installed release's private-network discovery policy and CA trust. |
| Nextcloud | External OIDC through the `user_oidc` app | Install a compatible plugin version and configure its provider/discovery URL; preserve a local admin recovery route. Do not confuse `user_oidc` with Nextcloud acting as an identity provider. |
| Penpot | Generic OIDC via backend env settings and frontend login flags | Separate client; callback `<origin>/api/auth/oidc/callback`. Request `openid email profile`. |
| Beszel | Native custom OIDC through PocketBase OAuth settings | Separate client; callback `<origin>/api/oauth2-redirect`. User auto-creation is off by default. |
| Homepage | Native password/OIDC gate documented starting with v2.0 | Check the deployed version; configure `HOMEPAGE_AUTH_ENABLED`, issuer, client credentials and external URL. Callback `<origin>/api/auth/callback/homepage-oidc`. |
| n8n | Native SSO is licensed; generic OIDC's specific guide says Enterprise | Community edition is not a free native OIDC integration. General SSO docs also list self-hosted Business; confirm the precise OIDC entitlement in the installed license. A proxy gate does not replace n8n user/role management. |
| Uptime Kuma | Native OIDC is not established by the checked official docs | Upstream tracks OIDC under issue #553. The current 2.5.9 package contains `openid-client`, which alone is not proof of an available login flow. Keep native login; consider a separate proxy gate after checking the exact deployed build. |
| Dockge | No documented generic OIDC in the checked 1.5.0 release | Keep native login; a proxy gate can add central access protection. Its upstream OIDC discussion remains a feature request. |
| IT Tools | Static browser utilities; no documented native user/OIDC flow | Optional proxy gate if access restriction is needed; there are no application accounts to provision. |
| Printamos / CUPS | No documented generic OIDC in Printamos; CUPS uses its own printing authentication | Gate the web UI separately if required; retain CUPS/IPP permissions. A browser OIDC session is not printer authentication. |
| ComfyUI | Its documented account flow covers hosted/partner services, not generic OIDC for the local server | Keep local service access private; a proxy gate is a separate integration. Do not treat a Comfy account login as local-server SSO. |

## Interoperability rules

Keep one client ID/secret per application and exact callback URLs. Browser and
container must reach the same public HTTPS endpoints and trust the certificate.
Keep each application's groups, roles, enrollment and recovery behavior explicit.
Shared sign-in is not a shared application session or automatic user-role sync.

The existing local tenant issuer may be a legacy identifier. Preserve it during
the container migration so Macro's `ISSUER` checks continue to work. Apps that
require an HTTPS issuer may need a separate, coordinated issuer migration with
all Macro validators updated and existing users signed in again; do not disable
issuer validation to bypass that requirement.

## Primary sources

- [Open WebUI SSO](https://docs.openwebui.com/features/authentication-access/auth/sso/)
- [AFFiNE self-host OAuth](https://docs.affine.pro/self-host-affine/administer/oauth-2-0)
- [Nextcloud external OIDC backend](https://github.com/nextcloud/user_oidc)
- [Penpot authentication providers](https://help.penpot.app/technical-guide/configuration/)
- [Beszel OAuth/OIDC](https://beszel.dev/guide/oauth)
- [Homepage auth and OIDC](https://gethomepage.dev/installation/)
- [n8n general SSO availability](https://docs.n8n.io/hosting/securing/set-up-sso/)
- [n8n specific OIDC availability](https://docs.n8n.io/user-management/oidc/)
- [Uptime Kuma OIDC tracking](https://github.com/louislam/uptime-kuma/issues/553)
- [Uptime Kuma 2.5.9 package](https://github.com/louislam/uptime-kuma/blob/2.5.9/package.json)
- [Dockge releases](https://github.com/louislam/dockge/releases)
- [Dockge OIDC discussion](https://github.com/louislam/dockge/discussions)
- [IT Tools source](https://github.com/CorentinTh/it-tools)
- [Printamos source](https://github.com/PrzemyslawSwiderski/printamos)
- [Comfy account scope](https://github.com/Comfy-Org/docs/blob/main/interface/user.mdx)
