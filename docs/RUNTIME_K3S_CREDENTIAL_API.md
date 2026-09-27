# Runtime k3s credential pull API

Broker stores the latest k3s administrator kubeconfig for each installed VM as
an encrypted database blob. Runtime authenticates with a short-lived service JWT
and pulls either one credential or a paginated collection.

## Authentication

Runtime signs an HS256 JWT using the same `RUNTIME_API_TOKEN_SECRET` configured
on Broker. Required claims:

```json
{
  "iss": "msg-runtime",
  "aud": "msg-broker-k3s-credentials",
  "type": "runtime_access",
  "sub": "runtime",
  "iat": 1788220800,
  "nbf": 1788220795,
  "exp": 1788221100,
  "jti": "<unique-request-uuid>"
}
```

`sub` must equal Broker's `RUNTIME_API_TOKEN_SUBJECT`. `exp - iat` must not
exceed 300 seconds. Every request uses:

```http
Authorization: Bearer <RUNTIME_JWT>
Accept: application/json
```

## List credentials

- Method: `GET`
- Internal app path: `/v1/runtime/k3s-credentials`
- Gateway path: `/api/v1/runtime/k3s-credentials`
- Query: optional `limit` from 1 to 200, default 200; optional `after` cursor
- Success: `200 OK`

```json
{
  "items": [
    {
      "schema_version": 1,
      "resource_target_id": "<resource-target-uuid>",
      "source_bootstrap_job_id": "<bootstrap-job-uuid>",
      "server_url": "https://<vm-address>:6443",
      "kubeconfig_base64": "<base64-kubeconfig-yaml>",
      "client_certificate_fingerprint_sha256": "<64-lowercase-hex>",
      "client_certificate_not_after": "2027-09-01T00:00:00Z",
      "generated_at": "2026-09-01T00:00:00Z",
      "uploaded_at": "2026-09-01T00:00:01Z"
    }
  ],
  "next_cursor": null
}
```

When `next_cursor` is not null, pass it as the next request's `after` value.
Only enabled, non-retired resource targets are returned.

## Get one credential

- Method: `GET`
- Internal app path: `/v1/runtime/k3s-credentials/{resource_target_id}`
- Gateway path: `/api/v1/runtime/k3s-credentials/{resource_target_id}`
- Success: `200 OK` with one item in the same shape as above

Runtime base64-decodes `kubeconfig_base64` before giving it to its Kubernetes
client. Base64 is encoding, not encryption; HTTPS protects the response in
transit.

## Errors

- `401 RUNTIME_TOKEN_REQUIRED`: Bearer token missing
- `401 INVALID_RUNTIME_TOKEN`: invalid, expired, wrong audience or wrong subject
- `404 K3S_CREDENTIAL_NOT_FOUND`: one requested target has no visible credential
- `500 K3S_CREDENTIAL_DECRYPTION_FAILED`: stored ciphertext failed integrity or
  decryption validation
- `500 DATABASE_ERROR`: credential query failed
- `503 RUNTIME_AUTH_NOT_CONFIGURED`: Runtime JWT settings missing
- `503 K3S_CREDENTIAL_STORAGE_NOT_CONFIGURED`: encryption setting missing

Error envelope:

```json
{
  "error": {
    "code": "STABLE_ERROR_CODE",
    "message": "Sanitized public message."
  }
}
```

Runtime must not log, cache in shared plaintext storage, or return the decoded
kubeconfig through its own public API. Network access from Runtime to each
`server_url` on TCP 6443 remains a separate firewall requirement.
