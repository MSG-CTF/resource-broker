# AWS Provider onboarding for independent accounts

The Broker does not store an AWS access key. Its attached GCP Service Account
gets a Google OIDC ID token, exchanges it for a temporary Hub Role session, and
then assumes one of two roles in each independently owned AWS account.

```text
GCP Broker Service Account
  -> Google OIDC ID token
  -> central AWS account / MsgBrokerFederationHubRole
  -> customer AWS account / MsgBrokerInventoryRole (read only)
  -> customer AWS account / MsgBrokerBootstrapRole (tag-restricted SSM)
```

AWS Organizations membership is not required. A customer keeps their own AWS
account and credits. The customer deploys one target-account CloudFormation
stack and sends no access key to the Broker operator.

## 1. Create or update the central Hub Role

Use the central AWS account that owns
`MsgBrokerFederationHubRole`. In **CloudFormation -> Stacks**, create or update
the stack with `msg-broker-federation-hub.yaml` and acknowledge named IAM
resources.

Parameters:

- `GoogleAudience`: `msg-broker-aws`
- `GoogleAuthorizedParty`: the GCP token `azp` claim
- `GoogleSubject`: the GCP token `sub` claim
- keep the default Hub, Inventory, and Bootstrap role names unless the Broker
  configuration deliberately uses different names

The Hub policy can request the two fixed role names in any account. This alone
does not grant access: each target role must explicitly trust the exact Hub
role ARN and require that target account's unique External ID.

Set the Hub output in the Broker `.env`:

```dotenv
AWS_FEDERATION_HUB_ROLE_ARN=arn:aws:iam::<HUB_ACCOUNT_ID>:role/MsgBrokerFederationHubRole
AWS_FEDERATION_AUDIENCE=msg-broker-aws
AWS_STS_REGION=us-east-1
```

`AWS_STS_REGION` is only where the Broker calls STS. It does not limit the EC2
Regions registered for customer accounts.

## 2. Pre-register a customer account in the Broker

The Broker operator needs the customer's 12-digit AWS Account ID and the EC2
Regions that should be inventoried. In the Dashboard, register:

- Account ID: the customer's 12-digit ID
- Inventory Role ARN:
  `arn:aws:iam::<CUSTOMER_ACCOUNT_ID>:role/MsgBrokerInventoryRole`
- Bootstrap Role ARN: leave blank to use
  `arn:aws:iam::<CUSTOMER_ACCOUNT_ID>:role/MsgBrokerBootstrapRole`
- Regions: only the Regions the customer wants inventoried

Registration does not call AWS, so the roles may be created afterward. Expand
**AWS 연결 값** on the saved account row and copy:

- External ID: `mbe-<Broker account UUID>`
- Bootstrap Role ARN

The External ID is unique but is not a password. The Broker issues it and the
customer must put exactly that value in the role trust policy. Do not reuse one
customer's External ID for another customer.

## 3. Customer deploys the target-account stack

The customer signs in to their own AWS account and opens **CloudFormation ->
Stacks -> Create stack -> With new resources**. Upload
`msg-broker-inventory-spoke.yaml`, acknowledge named IAM resources, and enter:

- `HubAccountId`: the 12-digit central AWS account ID
- `HubRoleName`: `MsgBrokerFederationHubRole`
- `ExternalId`: copied from the Broker Dashboard
- keep the default Inventory and Bootstrap role names
- `CreateManagedNodeInstanceProfile`: `true` if the target EC2 does not already
  have an SSM-capable instance profile

The stack creates:

- `MsgBrokerInventoryRole`: `DescribeInstances`, `DescribeInstanceTypes`, and
  `DescribeVolumes` only
- `MsgBrokerBootstrapRole`: SSM Run Command lifecycle permissions; it can send
  only the AWS-managed `AWS-RunShellScript` document and only to EC2 instances
  tagged `msg-broker-bootstrap=enabled`
- optional `MsgBrokerManagedNodeRole` instance profile with the AWS-managed
  `AmazonSSMManagedInstanceCore` policy

Both target roles trust only the exact Hub role and require the customer-specific
External ID. Inventory and root-level bootstrap execution permissions remain
separate even though the same GCP Service Account enters the Hub.

## 4. Prepare each target EC2

The current common Bootstrap supports Ubuntu AMD64 (`x86_64`) and ARM64
(`aarch64`). The EC2
instance must:

1. Run Ubuntu on x86_64 or aarch64. ARM64 instances include AWS Graviton types.
2. Have SSM Agent installed and running.
3. Have an instance profile that includes `AmazonSSMManagedInstanceCore`. If it
   has no existing profile, attach the stack output
   `ManagedNodeInstanceProfileName` in **EC2 -> Instances -> select instance ->
   Actions -> Security -> Modify IAM role**.
4. Have the EC2 tag `msg-broker-bootstrap=enabled`. Add it under **EC2 ->
   Instances -> Tags -> Manage tags**.
5. Have outbound HTTPS access to AWS Systems Manager endpoints, the Broker Agent
   hostname, `get.k3s.io`, and the configured container registry.

In **Systems Manager -> Fleet Manager -> Managed nodes**, wait until the
instance appears online. The Broker never opens inbound SSH for this workflow.

AWS documents the managed-node prerequisites and Run Command IAM behavior at:

- https://docs.aws.amazon.com/systems-manager/latest/userguide/managed_nodes.html
- https://docs.aws.amazon.com/systems-manager/latest/userguide/run-command-setting-up.html

## 5. Verify, Sync, and Bootstrap

Back in the Broker Dashboard:

1. Click **Verify** on the AWS account.
2. Click **Sync** and confirm the EC2 appears under **전체 VM**.
3. Open the Ubuntu AMD64 or ARM64 EC2 resource.
4. Create an `INSTALL` Bootstrap job with an available Bootstrap version, a
   pinned k3s version, and the digest of an Agent multi-platform image index
   containing both `linux/amd64` and `linux/arm64`.

The worker assumes `MsgBrokerBootstrapRole`, sends one SSM command, records the
SSM Command ID in `provider_job_id`, and polls it until success or failure.
Immediately after `SendCommand`, AWS may temporarily report that the invocation
does not exist; the worker treats this documented eventual-consistency window as
pending, not as a failure.

The target runner obtains its EC2 instance identity document and RSA/SHA-256
signature from IMDSv2. The Broker verifies the AWS-published Region public key,
then requires the signed account ID, Region, and instance ID to match the exact
resource target before issuing the VM-local CSR certificate. No AWS credential,
Agent private key, or one-time enrollment secret is placed in the SSM command.

## Existing single-account setup

An AWS account registered before External ID support does not need a Broker DB
migration or re-registration. After deploying the new Broker code, expand
**AWS 연결 값**, update the target-account stack with that External ID, add the
Bootstrap role, and prepare the EC2 instance as above. An older Inventory role
continues to accept the extra STS External ID until its trust policy is updated,
but updating the stack is required for the intended confused-deputy protection.
