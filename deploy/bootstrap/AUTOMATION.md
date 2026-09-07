# Provider Bootstrap automation

The Broker stores provider-neutral Bootstrap jobs and executes them through GCP
VM Manager, AWS Systems Manager, or Azure Managed Run Command.

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
  "bootstrap_version": "0.4.2",
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
when the job is created. The bundle then verifies the separately downloaded k3s
installer against the SHA-256 pinned in that Bootstrap version before executing
it as root.

For `INSTALL` and `UPDATE`, the Provider runner uploads the k3s administrator
kubeconfig to the Broker over HTTPS. Job creation fails before any provider-side
mutation unless the Broker has separate upload-token and at-rest encryption
secrets plus a Runtime-reachable public or private VM IP. The runner rewrites only
its temporary kubeconfig copy to `https://<address>:6443` and requires a `200` or
`201` Broker response before Bootstrap reports ready.

The upload bearer is an HS256 JWT scoped to one bootstrap job, resource target,
and k3s server URL. It expires after 24 hours. The Broker validates the embedded
administrator certificate and stores one Fernet-encrypted current kubeconfig per
resource target. Runtime authenticates to separate pull endpoints with a
short-lived HS256 service JWT and receives credentials in pages of at most 200.
Neither plaintext kubeconfig nor either JWT secret is stored in job output.

GCP, AWS, and Azure jobs accept inventory targets normalized as `AMD64` or
`ARM64`.
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

## Azure flow

1. The worker exchanges a Google metadata ID token with Microsoft Entra by
   using the target tenant's Federated Credential and App Registration.
2. It resolves the inventory `vmId` to one exact Resource Group and VM, then
   requires `msg-broker-bootstrap=enabled` and a ready Azure Linux Agent.
3. It creates a job-named Managed Run Command and stores that resource name as
   `provider_job_id`. The command downloads and checksum-verifies the common
   runner; no enrollment secret is included.
4. The VM requests an Azure IMDS PKCS#7 attested document with a job-bound
   10-digit nonce. The Broker validates the public certificate chain, signed
   lifetime and nonce, then matches Subscription ID and `vmId` before signing
   the VM-local CSR.
5. The worker polls the Run Command instance view and deletes the command
   resource after success, failure, or timeout.

The Entra application needs Reader access for inventory and a minimal custom
role at each approved Resource Group containing the Managed Run Command
read/write/delete operations. The VM must be an Ubuntu AMD64 or ARM64 VM with a
ready Azure Linux Agent and outbound HTTPS connectivity.

## Artifact versions

The Docker image builds Bootstrap `0.4.2` by default. Set the Docker build arg
`BOOTSTRAP_VERSION` to publish another immutable bundle. The UI/API version must
match an artifact present in `BOOTSTRAP_ARTIFACT_DIR`; otherwise job creation
fails before any provider-side change.

Bootstrap `0.4.1` re-executes the runner with `/bin/bash` when a Provider starts
it with `/bin/sh`, before using Bash options or conditionals. GCP's `SHELL`
interpreter does not honor the runner's Bash shebang. The bundle also accepts
upload JWTs up to 4096 characters; the previous 512-character limit could reject
the Broker's own job-scoped JWT. Upload signature and claim validation remain on
the Broker.

Bootstrap `0.4.2` also pins the k3s installer URL to upstream commit
`2977c525a2e7a487886107ce4df43630ae9b03b2` and verifies SHA-256
`e5cc3b3d9dfc1662c2d9be6da5abc9a4cd317d6abc3a5ffc02e3dd3248207fee`.
The mutable `get.k3s.io` endpoint is no longer the default. The requested k3s
binary version still comes from `k3s_version`. See the README for provenance.

The Node Agent stays at `0.2.1`; an existing Agent image digest can be reused.
Do not republish the modified bundle as `0.4.0` or `0.4.1`: artifact URLs are immutable and
existing jobs pin their checksums. Older runner rendering stays unchanged, but
serving an old job still requires its original bundle and configuration. Use a
new `0.4.2` job after the earlier job reaches a terminal state.

See [Bootstrap recovery](../../docs/BOOTSTRAP_RECOVERY.md) for rollout and manual
checks when a VM reports `[[: not found` while the Broker still shows `RUNNING`.
