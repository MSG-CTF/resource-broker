# Azure dashboard browser login

The dashboard uses one central multitenant SPA application and MSAL Browser's
OAuth authorization-code flow with PKCE. A user signs in using their existing
Azure resource permissions. Device-code authentication is not used.

## One-time central application setup

1. In Microsoft Entra ID > App registrations > New registration, use the name
   `MSG Broker Dashboard` and **Accounts in any organizational directory**.
2. Register the **Single-page application (SPA)** redirect URI exactly as:
   `https://broker.mjsec.kr/azure-auth-redirect.html`.
3. In API permissions, add **Azure Service Management > Delegated permissions >
   user_impersonation**. Do not grant application-level Azure roles to the SPA.
   Client secrets and public-client/device-code flows are not needed.
4. Put the Application (client) ID in `AZURE_USER_LOGIN_CLIENT_ID` on the Broker
   API container. This ID is shared by all dashboard Azure user connections.
5. Complete normal user consent when signing in. Target tenants may require an
   administrator to approve the central application; RG Owner does not imply
   app-registration or tenant-wide consent privileges.

The redirect page is a standalone Vite build entry running only MSAL's redirect
bridge. It must not receive Cross-Origin-Opener-Policy headers, run the main
application, or redirect to another URL. Nginx sends Cache-Control: no-store.

## Dashboard flow

Register Azure with Tenant ID, display name, and Subscription IDs. Select
**Microsoft 계정 연결**, then **Microsoft로 로그인**. The popup signs into the
configured target tenant. When login completes, Broker checks access to each
configured subscription's readable resource groups, caches the short-lived
access token, then the dashboard runs Sync and opens the inventory.

Resource-group permissions are sufficient for group-by-group VM reads.
Unavailable SKU/network metadata stays unknown on permission denial. Failed VM
reads preserve the last successful snapshot rather than replacing it with an
empty result.

New accounts use `config.auth_method=USER_BROWSER`. Previously stored
`USER_DEVICE_CODE` accounts are accepted as a configuration alias only and use
the same browser flow; successful connection rewrites them to USER_BROWSER.
Google OIDC federation accounts keep their existing behavior and client_id.

## API contract

Both endpoints require `Authorization: Bearer <ADMIN_TOKEN>`.

| Method | Path | Success | Errors |
| --- | --- | --- | --- |
| GET | `/v1/admin/provider-accounts/{account_id}/azure-login` | `200` | `401`, `404`, `409`, `422`, `500` |
| POST | `/v1/admin/provider-accounts/{account_id}/azure-login` | `200` | `400`, `401`, `404`, `409`, `422`, `500`, `502`, `503` |

GET returns `{status, client_id, tenant_id, expires_at, message}`. Status is
DISCONNECTED or CONNECTED; null client_id means central app setup is missing.
POST sends the Microsoft ARM access token in `X-Azure-Access-Token`, with JSON:

```json
{"expires_on": 1790000000}
```

The header is used instead of a body token field so validation errors cannot
reflect it. Tokens are never included in API responses, URLs, documents, or
application logs. POST validates the opaque ARM token against Azure APIs; it
does not use that token to authenticate access to Broker. Only the existing
Admin Bearer grants access to these endpoints.

Errors use `{"error":{"code":"...","message":"..."}}`. Missing tokens return
AZURE_ACCESS_TOKEN_REQUIRED, expired/malformed tokens INVALID_AZURE_ACCESS_TOKEN,
and missing central app configuration AZURE_BROWSER_LOGIN_NOT_CONFIGURED.
Cloud authentication/permission failures return sanitized 502 responses rather
than clearing the dashboard's Admin session with a 401.

## Lifetime and manual verification

MSAL uses memoryStorage. Broker retains only a short-lived ARM access token in
one API process, for at most one hour or the token's expiry, whichever is first.
No refresh token, password, token file, or token DB storage is introduced.
Reconnect after expiry or restart. Multiple API replicas are not supported by
this in-memory credential store. No DB schema migration is required.

Manual verification: configure the central app, register without per-account
Client ID, open browser login, complete Microsoft MFA/consent, and check actual
VM names/power states in accessible resource groups. Cancelled/blocked popups
must show a retry message. Verify/Sync without an active connection must request
Microsoft login. A new API process must require reconnection. Existing
GCP/AWS/Azure federation paths retain their current authentication contracts.

References:
- https://learn.microsoft.com/en-us/entra/msal/javascript/browser/initialization
- https://learn.microsoft.com/en-us/entra/msal/javascript/browser/redirect-bridge
- https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow
- https://learn.microsoft.com/en-us/entra/fundamentals/security-defaults#block-device-code-flow
