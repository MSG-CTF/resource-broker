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
- For CPU and memory, allocatable capacity is the smaller of
  `max(node allocatable - allocated workload requests, 0)` and
  `max(Provider capacity - whole-node usage, 0)`. Ephemeral storage uses the
  request-based value.
- Candidate capacity subtracts unexpired `HELD` reservations.
- A `COMMITTED` reservation is also subtracted while `runtime_observed_at` is
  missing or is not strictly later than `committed_at + 30 seconds`. This
  conservative margin accounts for an Agent clock up to 30 seconds ahead of the
  Broker. `runtime_last_seen_at` is receipt time and never proves that a workload
  was observed. Delayed delivery or redelivery cannot remove this deduction.
- Node Agent `0.2.1` timestamps the start of collection, before reading Node,
  Pods, or usage. Roll it out together with this Broker change; older collectors
  timestamped the end of the reads. Keep Broker and VM clocks synchronized
  within 30 seconds. Observations more than 30 seconds ahead of the Broker are
  rejected with `409 FUTURE_AGENT_OBSERVATION` without changing the snapshot.
- The existing integration assumption still applies: Runtime creation must be
  visible in the selected node's Pod list before Scheduler commits. Broker does
  not infer a mapping from `runtime_workload_id` to Pod/container IDs. A Runtime
  success response that only means queued or accepted needs a separate binding
  acknowledgement contract before it can safely be used for commit.
- Candidate results are advisory. `POST /v1/reservations` locks the VM row and
  checks eligibility and capacity again.
- Eligible VMs must have the Provider account and VM enabled, have Provider
  power state `RUNNING`, be Runtime-ready, not be retired, and match architecture.
  Both collection time and receipt time
  must be newer than `SCHEDULER_OBSERVATION_STALE_SECONDS`; collection time must
  also be no more than 30 seconds in the future. The same checks apply to Admin
  candidate registration and final reservation creation. Candidate `valid_until`
  cannot extend past either timestamp's freshness deadline. Redelivering the
  same observation therefore does not extend its placement validity.
- Provider power state comes from the latest successful inventory sync. Only
  `RUNNING` is eligible across AWS, GCP, and Azure; stopped, terminated,
  transitional, missing, unknown, and provisioning-only states are excluded.
  A Cloud-side change takes effect after sync records it; candidate queries do
  not call the Cloud API. `RUNNING` does not imply Runtime readiness.
  A stopped VM's registration flag and existing reservations are preserved.
  After restart and sync, an enabled VM can return once all Runtime observation
  and capacity checks pass. New holds against an ineligible VM return
  `409 CANDIDATE_UNAVAILABLE`; exact retries of existing reservations retain
  their existing idempotency behavior.

The margin can temporarily subtract a newly observed workload twice. Its
reservation deduction stops with the first collection starting strictly more
than 30 seconds after commit (on the Agent clock), provided the snapshot is
still fresh. This favors conservative capacity during observation handoff.

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
  "team_id": "8ac3dc1f-fd12-4c18-b894-1fb1a34c47cb",
  "challenge_id": "91c9ecdc-6f9e-45cc-9aeb-7d44f3f3ebdc",
  "instance_id": "018f3f1e-21b8-7a91-a30b-6d52b82149c1",
  "architecture": "AMD64",
  "resource_profile": {
    "cpu_millicores": 500,
    "memory_mib": 1024,
    "ephemeral_storage_mib": 2048
  }
}
```

The Broker returns at most 50 candidates. The result limit is controlled by
the Broker and is not part of the Scheduler request.

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
  "request_id": "reservation-request-001",
  "requested_at": "2026-08-19T12:00:01Z",
  "instance_id": "018f3f1e-21b8-7a91-a30b-6d52b82149c1",
  "candidate_id": "00000000-0000-4000-8000-000000000001",
  "team_id": "8ac3dc1f-fd12-4c18-b894-1fb1a34c47cb",
  "challenge_id": "91c9ecdc-6f9e-45cc-9aeb-7d44f3f3ebdc",
  "architecture": "AMD64",
  "resource_profile": {
    "cpu_millicores": 500,
    "memory_mib": 1024,
    "ephemeral_storage_mib": 2048
  }
}
```

The response includes `reservation_id`, the Runtime `target_id`, status `HELD`,
and `expires_at`. The Broker uses `request_id` as the idempotency key: an exact
retry returns the existing reservation with `200 OK`, while reusing it with
different data returns `409 REQUEST_ID_REUSED`. Only one `HELD` or `COMMITTED`
reservation may exist for a `(team_id, instance_id)`.

## Read, commit, and release

| Method | Path | Purpose | Success | Errors |
| --- | --- | --- | --- | --- |
| `GET` | `/v1/reservations/{reservation_id}` | Read current state and lazily expire an old hold | `200` | `401`, `404`, `500`, `503` |
| `POST` | `/v1/reservations/{reservation_id}/commit` | Call only after Runtime creation succeeds | `200` | `401`, `404`, `409`, `422`, `500`, `503` |
| `POST` | `/v1/reservations/{reservation_id}/release` | Release after Runtime failure or Scheduler cancellation | `200` | `401`, `404`, `409`, `422`, `500`, `503` |

Commit request:

```json
{
  "request_id": "commit-request-001",
  "requested_at": "2026-08-19T12:00:05Z",
  "instance_id": "018f3f1e-21b8-7a91-a30b-6d52b82149c1",
  "reservation_id": "00000000-0000-4000-8000-000000000003",
  "runtime_workload_id": "workload-001",
  "resource_profile": {
    "cpu_millicores": 500,
    "memory_mib": 1024,
    "ephemeral_storage_mib": 2048
  }
}
```

The deployed `resource_profile` must exactly match the held profile. A mismatch
returns `409 DEPLOYED_SPEC_MISMATCH` and leaves the reservation `HELD` so the
Scheduler can release it with reason `DEPLOYED_SPEC_MISMATCH`.

Release request:

```json
{
  "request_id": "release-request-001",
  "requested_at": "2026-08-19T12:00:06Z",
  "instance_id": "018f3f1e-21b8-7a91-a30b-6d52b82149c1",
  "reservation_id": "00000000-0000-4000-8000-000000000003",
  "release_reason": "RUNTIME_CREATE_FAILED"
}
```

Allowed `release_reason` values are `RUNTIME_CREATE_FAILED`,
`DEPLOYED_SPEC_MISMATCH`, and `SCHEDULER_CANCELLED`. The body
`reservation_id` must equal the path ID, and `instance_id` must equal the one
stored on the reservation.

Commit and release are idempotent only when the same `request_id` and identical
body are retried. Reusing a mutation request ID with different data returns
`409 REQUEST_ID_REUSED`.
Uncommitted holds expire after `SCHEDULER_RESERVATION_TTL_SECONDS`. Committed
reservations do not expire automatically and must be released after workload
deletion.

Lazy expiry uses a conditional database update that only matches an expired
`HELD` row. GET and create retries refresh the current row after that update;
they cannot overwrite a concurrent commit or release using an older ORM object.
Commit and release hold the reservation row lock through their final write.
Release records expiry and its request metadata in one transaction; the first
release of an already expired hold records the reason and keeps `EXPIRED`.
An identical release retry returns the recorded result, and a different release
request cannot replace it. A commit after expiry returns
`409 INVALID_RESERVATION_STATE`.

Reservation response:

```json
{
  "reservation_id": "00000000-0000-4000-8000-000000000003",
  "request_id": "reservation-request-001",
  "candidate_id": "00000000-0000-4000-8000-000000000001",
  "target_id": "runtime-target-001",
  "team_id": "8ac3dc1f-fd12-4c18-b894-1fb1a34c47cb",
  "challenge_id": "91c9ecdc-6f9e-45cc-9aeb-7d44f3f3ebdc",
  "instance_id": "018f3f1e-21b8-7a91-a30b-6d52b82149c1",
  "resource_profile": {
    "cpu_millicores": 500,
    "memory_mib": 1024,
    "ephemeral_storage_mib": 2048,
    "architecture": "AMD64"
  },
  "status": "COMMITTED",
  "expires_at": null,
  "created_at": "2026-08-19T12:00:01Z",
  "committed_at": "2026-08-19T12:00:05Z",
  "released_at": null,
  "runtime_workload_id": "workload-001",
  "deployed_resource_profile": {
    "cpu_millicores": 500,
    "memory_mib": 1024,
    "ephemeral_storage_mib": 2048
  },
  "release_reason": null,
  "updated_at": "2026-08-19T12:00:05Z"
}
```

## Enable a VM for candidates

Newly synchronized VMs default to `enabled: false`. An Admin explicitly enables
a VM after Bootstrap and Runtime observation are ready. Provider sync preserves
the existing value and never enables a VM automatically.

The Admin dashboard exposes this operation as `후보 등록` and
`후보 등록 해제`. Its registration filter defines the candidate pool as the
set of VMs with `enabled: true`. Candidate queries still apply Provider power state, Runtime
readiness, observation freshness, architecture, capacity, and reservation
checks to that pool.

- Method: `PATCH`
- Path: `/v1/admin/resource-targets/{resource_target_id}`
- Authentication: Admin Bearer JWT
- Body: `{ "enabled": true }`
- Success: `200`
- Errors: `401`, `404`, `409`, `422`, `500`, `503`

Registration with `enabled: true` is rejected with `409` unless the Provider
account is enabled, the VM is not retired, Provider power state is `RUNNING`,
Runtime is ready, the observation is
fresh, and Provider capacity, whole-node usage, and allocatable capacity are
all present. Setting `enabled: false` always stops new candidate placement and
does not delete running workloads or active reservations.
An otherwise enabled, non-retired account/VM with a non-`RUNNING` power state
returns `409 PROVIDER_INSTANCE_NOT_RUNNING` on registration. The Admin dashboard
shows the same reason and permits unregistration even while the VM is stopped.

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
`INSUFFICIENT_CAPACITY`, `REQUEST_ID_REUSED`, `INSTANCE_ALREADY_RESERVED`,
`RESERVATION_ID_MISMATCH`, `INSTANCE_ID_MISMATCH`,
`DEPLOYED_SPEC_MISMATCH`, and `INVALID_RESERVATION_STATE`.

Manual rollout and integration checks are in
[RESERVATION_VALIDATION.md](RESERVATION_VALIDATION.md).
