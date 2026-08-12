# GCP Bootstrap automation

The Broker stores provider-neutral bootstrap jobs, but the first execution
adapter is GCP. AWS and Azure requests return
`PROVIDER_BOOTSTRAP_NOT_IMPLEMENTED` until their adapters are added.

## Flow

1. An administrator creates a job with
   `POST /v1/admin/resource-targets/{resource_target_id}/bootstrap-jobs`.
2. The Broker worker adds the unique
   `msg-broker-bootstrap-job=job-...` label without replacing any existing VM
   labels.
3. It creates a zonal OS policy assignment that selects that label and runs a
   checksum-pinned, action-specific bootstrap runner.
4. A VM without an Agent certificate obtains a full-format GCP metadata
   identity JWT. The Broker verifies its audience, project ID, instance ID,
   and zone before signing the VM-local CSR.
5. After a compliant report, or after a terminal failure, the worker deletes
   the OS policy assignment first and then removes only its own label value.
   A label changed by another actor is never deleted.

The database partial unique index permits only one active job per resource
target. The API accepts only `INSTALL`, `UPDATE`, `CHECK`, and `REMOVE`; it
does not accept arbitrary shell commands.

The Broker limits its own active GCP assignments to 10 per project and zone by
default, leaving headroom below the GCP quota of 20 assignments per project and
zone. Additional jobs remain `QUEUED`. Configure this with
`BOOTSTRAP_GCP_MAX_ACTIVE_ASSIGNMENTS_PER_ZONE` (1 through 19). The execution
timeout starts when a queued job begins applying and defaults to 7200 seconds.

## Required deployment settings

- Run the enrollment-enabled compose overlay so `AGENT_ENROLLMENT_CA_DIR` is
  mounted.
- Keep `BOOTSTRAP_PUBLIC_BASE_URL` on the public HTTPS Agent hostname.
- Enable `osconfig.googleapis.com` and full VM Manager functionality in every
  target project. Target VMs need a running OS Config agent and an attached
  service account so the metadata identity endpoint is available.
- By explicit deployment decision, the existing central Broker service account
  is used for both inventory reads and Bootstrap execution. It needs permission
  to read and set VM labels, administer OS policy assignments, and read
  assignment reports. OS policy administration is equivalent to remote code
  execution on matching VMs, so this accepted single-identity design must be
  reflected in IAM review and audit monitoring.
  Prefer a custom Compute role containing `compute.instances.get` and
  `compute.instances.setLabels`, plus
  `roles/osconfig.osPolicyAssignmentAdmin` and
  `roles/osconfig.osPolicyAssignmentReportViewer`.

The Broker does not enable APIs, change project metadata, or grant IAM roles.
Those remain explicit project setup operations.

## Artifact versions

The Docker image builds Bootstrap `0.1.0` by default. Set the Docker build arg
`BOOTSTRAP_VERSION` to publish another immutable bundle. The UI/API version
must match an artifact present in `BOOTSTRAP_ARTIFACT_DIR`; otherwise job
creation fails before any VM label is changed.
