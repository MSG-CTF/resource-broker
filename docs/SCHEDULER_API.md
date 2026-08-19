# Scheduler Candidate and Reservation API

The Scheduler API is separate from the Admin and Node Agent APIs. Every request
uses a dedicated service token.

```json
{
  "headers": {
    "Content-Type": "application/json",
    "Authorization": "Bearer <SCHEDULER_API_TOKEN>"
  }
}
```

`SCHEDULER_API_TOKEN` must contain at least 32 characters and must not reuse the
Admin token or Admin signing secret.

## Capacity contract

- Candidate input and reservations use CPU millicores, memory MiB, and
  ephemeral-storage MiB.
- The Node Agent stores `node allocatable - allocated workload requests` as the
  resource target's allocatable capacity.
- Candidate capacity subtracts unexpired `HELD` reservations.
- A `COMMITTED` reservation is also subtracted until the first Node Agent
  observation newer than `committed_at`. That newer observation is assumed to
  include the Runtime-created workload, preventing permanent double subtraction.
- Candidate results are advisory. `POST /v1/reservations` locks the VM row and
  checks eligibility and capacity again.
- Eligible VMs must have the Provider account and VM enabled, be Runtime-ready,
  not be retired, match architecture, and have a heartbeat newer than
  `SCHEDULER_OBSERVATION_STALE_SECONDS`.

## Query candidates

- Method: `POST`
- Path: `/v1/candidates/query`
- Success: `200 OK`
- Errors: `401`, `422`, `500`, `503`

Request:

```json
{
  "request_id": "schedule-request-001",
  "requested_at": "2026-08-19T12:00:00Z",
  "team_id": 101,
  "challenge_id": 202,
  "instance_id": "instance-001",
  "resource_profile": {
    "cpu_millicores": 500,
    "memory_mib": 1024,
    "ephemeral_storage_mib": 2048,
    "architecture": "AMD64"
  },
  "max_candidates": 50
}
```

Response:

```json
{
  "request_id": "schedule-request-001",
  "generated_at": "2026-08-19T12:00:01Z",
  "status": "OK",
  "candidates": [
    {
      "candidate_id": "00000000-0000-4000-8000-000000000001",
      "provider": "GCP",
      "account_id": "00000000-0000-4000-8000-000000000002",
      "region": "example-region",
      "zone": "example-zone",
      "runtime": {
        "type": "KUBERNETES",
        "target_id": "runtime-target-001"
      },
      "architecture": "AMD64",
      "remaining_capacity": {
        "cpu_millicores": 1500,
        "memory_mib": 3072,
        "ephemeral_storage_mib": 8192,
        "fit_count": 3
      },
      "runtime_observed_at": "2026-08-19T11:59:30Z",
      "valid_until": "2026-08-19T12:00:31Z"
    }
  ]
}
```

No match is a successful response with `status: NO_CANDIDATES` and an empty
`candidates` list.

## Hold a reservation

- Method: `POST`
- Path: `/v1/reservations`
- Success: `201 Created`; an identical idempotent replay returns `200 OK`
- Errors: `401`, `404`, `409`, `422`, `500`, `503`

Request:

```json
{
  "idempotency_key": "scheduler-attempt-001",
  "request_id": "schedule-request-001",
  "candidate_id": "00000000-0000-4000-8000-000000000001",
  "team_id": 101,
  "challenge_id": 202,
  "instance_id": "instance-001",
  "resource_profile": {
    "cpu_millicores": 500,
    "memory_mib": 1024,
    "ephemeral_storage_mib": 2048,
    "architecture": "AMD64"
  }
}
```

The response includes `reservation_id`, the Runtime `target_id`, status `HELD`,
and `expires_at`. A different request cannot reuse an idempotency key. Only one
`HELD` or `COMMITTED` reservation may exist for a `(team_id, instance_id)`.

## Read, commit, and release

| Method | Path | Purpose | Success | Errors |
| --- | --- | --- | --- | --- |
| `GET` | `/v1/reservations/{reservation_id}` | Read current state and lazily expire an old hold | `200` | `401`, `404`, `500`, `503` |
| `POST` | `/v1/reservations/{reservation_id}/commit` | Call only after Runtime creation succeeds | `200` | `401`, `404`, `409`, `500`, `503` |
| `POST` | `/v1/reservations/{reservation_id}/release` | Release after Runtime failure or workload deletion | `200` | `401`, `404`, `409`, `500`, `503` |

Commit and release are idempotent when repeated in their resulting state.
Uncommitted holds expire after `SCHEDULER_RESERVATION_TTL_SECONDS`. Committed
reservations do not expire automatically and must be released after workload
deletion.

## Enable a VM for candidates

Newly synchronized VMs default to `enabled: false`. An Admin explicitly enables
a VM after Bootstrap and Runtime observation are ready.

- Method: `PATCH`
- Path: `/v1/admin/resource-targets/{resource_target_id}`
- Authentication: Admin Bearer JWT
- Body: `{ "enabled": true }`
- Success: `200`
- Errors: `401`, `404`, `422`, `500`

## Error format

```json
{
  "error": {
    "code": "STABLE_ERROR_CODE",
    "message": "Sanitized public message."
  }
}
```

Reservation conflicts use stable codes including `CANDIDATE_UNAVAILABLE`,
`INSUFFICIENT_CAPACITY`, `IDEMPOTENCY_KEY_REUSED`,
`INSTANCE_ALREADY_RESERVED`, and `INVALID_RESERVATION_STATE`.
