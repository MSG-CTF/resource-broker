# Provider Bootstrap automation

The Broker stores provider-neutral Bootstrap jobs and currently executes them
through GCP VM Manager or AWS Systems Manager. Azure jobs still return
`PROVIDER_BOOTSTRAP_NOT_IMPLEMENTED`.

## Common API and safety boundary

An administrator creates a job with:

```http
POST /v1/admin/resource-targets/{resource_target_id}/bootstrap-jobs
Authorization: Bearer <ADMIN_JWT>
Content-Type: application/json
```

```json
{
  "action": "INSTALL",
  "bootstrap_version": "0.3.1",
  "k3s_version": "v1.33.3+k3s1",
  "agent_image": "repository/agent@sha256:<64-hex-digest>"
}
```

Only `INSTALL`, `UPDATE`, `CHECK`, and `REMOVE` are accepted. There is no
arbitrary-command API. A database partial unique index permits only one active
job per resource target. The execution deadline starts when a queued job enters
`APPLYING` and defaults to 7200 seconds.

Every provider runs the same immutable Bootstrap bundle and job-specific runner.
Both files are downloaded over HTTPS and checked against SHA-256 values stored
when the job is created.

GCP and AWS jobs accept inventory targets normalized as `AMD64` or `ARM64`.
Targets with an unknown or unsupported architecture are rejected before any
provider-side label, policy, or command is created. For `INSTALL` and `UPDATE`,
`agent_image` must identify the multi-platform OCI image index containing both
`linux/amd64` and `linux/arm64`, not a platform-specific child manifest.

## GCP flow

1. The worker adds its unique `msg-broker-bootstrap-job=job-...` VM label.
2. It creates a zonal OS Policy Assignment selected by that label.
3. The VM obtains a full-format GCP metadata identity JWT. The Broker verifies
   audience, project, instance ID, and zone before signing the VM-local CSR.
4. After compliance or terminal failure, the worker deletes the assignment and
   removes only its own label value.

The Broker defaults to ten active GCP assignments per project and zone, leaving
headroom below the quota of twenty. Configure the limit with
`BOOTSTRAP_GCP_MAX_ACTIVE_ASSIGNMENTS_PER_ZONE` from 1 through 19.

Required GCP setup remains OS Config API/agent, VM Manager, and the central
Broker Service Account permissions documented for the deployment. By explicit
project decision, the same GCP Service Account is used, even though its inventory
and Bootstrap permissions should still be separate IAM grants and audited as
different capabilities.

## AWS flow

1. The worker uses the same Google OIDC -> Hub STS exchange as inventory.
2. It assumes the account's separate `MsgBrokerBootstrapRole` with the
   account-specific External ID.
3. It sends `AWS-RunShellScript` to exactly one EC2 instance and stores the SSM
   Command ID as `provider_job_id`.
4. SSM downloads and checksum-verifies the job runner. No one-time secret is in
   the command.
5. The VM obtains the EC2 instance identity document and base64 RSA signature
   from IMDSv2. The Broker verifies AWS's Region public key and matches account,
   Region, and instance ID before signing the VM-local CSR.
6. The worker polls `GetCommandInvocation`; the documented eventual-consistency
   `InvocationDoesNotExist` response remains pending. A deadline cancels the SSM
   command before marking the job timed out.

The AWS role can send only the AWS-managed `AWS-RunShellScript` document and only
to instances tagged `msg-broker-bootstrap=enabled`. The EC2 must be an online SSM
managed node with an instance profile containing
`AmazonSSMManagedInstanceCore`.

## Artifact versions

The Docker image builds Bootstrap `0.3.1` by default. Set the Docker build arg
`BOOTSTRAP_VERSION` to publish another immutable bundle. The UI/API version must
match an artifact present in `BOOTSTRAP_ARTIFACT_DIR`; otherwise job creation
fails before any provider-side change.
