# Dedicated VM placement and observation

The broker discovers the VM through its existing cloud inventory, then accepts observations from a separate read-only SSH process. A VM becomes a candidate only when its cloud `provider_instance_id`, guest identity, pinned SSH host key, profile and operator placement agree. A dedicated challenge has no Kubernetes fallback. A VM is exclusive: an active `HELD` or `COMMITTED` reservation blocks another candidate even if a newer agent observation reports capacity.

Apply the `20261020_0001` migration before starting the new app. Set `BROKER_DEDICATED_VM_PLACEMENTS_FILE` for the API service and both `BROKER_DEDICATED_VM_PLACEMENTS_FILE` and `BROKER_DEDICATED_VM_OBSERVERS_FILE` for a separate process running `python -m app.services.dedicated_vm_observer`. The JSON shape is:

```json
{"schema_version":1,"placements":[{"target_id":"vm-afterimage","challenge_ids":["REPLACE_APPROVED_CHALLENGE_UUID"]}]}
```

```json
{"schema_version":1,"targets":[{"resource_target_id":"REPLACE_CLOUD_INVENTORY_RESOURCE_UUID","target_id":"vm-afterimage","ssh_host":"REPLACE_VM_ADDRESS","ssh_port":22,"ssh_user":"msgvm","identity_file":"/etc/msg-broker/vm/observer-key","known_hosts_file":"/etc/msg-broker/vm/known-hosts","profile_sha256":"REPLACE_PROFILE_SHA256"}]}
```

The observer key is forced to the guest's `--observe-only` helper. A failed observation withdraws readiness. Do not place credentials, flags, or participant environment values in these files or observation payloads. Enable the inventory row only after the guest is reachable and its owner is clear. Withdraw placement and wait for the exclusive reservation to release before removing a VM or rolling back the app. Keep released reservation rows for audit.

In the 2026-10-20 QA deployment, the active reservation excluded the dedicated target; after the backend deployment test was deleted, one `RELEASED` reservation was recorded and the target was offered again. The isolated broker integration suite passed 11 tests against PostgreSQL, including wrong-challenge admission, an exclusive committed lease after fresh telemetry, replay, release, and lack of a Kubernetes fallback.
