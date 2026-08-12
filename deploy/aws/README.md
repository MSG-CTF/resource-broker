# AWS Provider onboarding

The Broker uses no AWS access key. Its attached GCP Service Account obtains a
Google ID token, exchanges it for a temporary Hub Role session with
`AssumeRoleWithWebIdentity`, and then assumes a read-only Spoke Role in each
target AWS account.

```text
GCP Broker Service Account
  -> Google OIDC ID token
  -> tooling member account / MsgBrokerFederationHubRole
  -> target member account / MsgBrokerInventoryRole
  -> EC2 inventory APIs in explicitly configured Regions
```

The AWS Organizations management account should only manage Organizations and
StackSets. Put the Hub Role in a dedicated tooling member account.

## 1. Read the Google token claims

Run this on the GCE VM that runs the Broker, using the same audience that will
be configured as `AWS_FEDERATION_AUDIENCE`. The token itself is not printed.

```bash
AWS_FEDERATION_AUDIENCE=msg-broker-aws
curl -fsS \
  -H 'Metadata-Flavor: Google' \
  "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity?audience=${AWS_FEDERATION_AUDIENCE}&format=full" \
  | python3 -c 'import base64,json,sys; p=sys.stdin.read().split(".")[1]; p += "=" * (-len(p) % 4); c=json.loads(base64.urlsafe_b64decode(p)); print(json.dumps({k:c.get(k) for k in ("aud","azp","sub")}, indent=2))'
```

Use the resulting non-secret claim values as the Hub stack parameters. Do not
copy the ID token into a file, chat, source code, or CloudFormation parameter.

## 2. Create the Hub Role in the tooling member account

In the tooling member account, open **CloudFormation -> Stacks -> Create
stack**, upload `msg-broker-federation-hub.yaml`, acknowledge named IAM
resources, and provide:

- `OrganizationId`: the AWS Organizations ID
- `GoogleAudience`: token `aud`
- `GoogleAuthorizedParty`: token `azp`
- `GoogleSubject`: token `sub`
- the default Hub and Spoke role names unless the Broker configuration uses
  different names

No custom IAM OIDC provider is required for `accounts.google.com`.

## 3. Deploy Spoke Roles from the management account

In the Organizations management account:

1. Open **CloudFormation -> StackSets -> Create StackSet**.
2. Select **Service-managed permissions** and activate trusted access if asked.
3. Upload `msg-broker-inventory-spoke.yaml` and acknowledge named IAM resources.
4. Set the tooling `HubAccountId`, `OrganizationId`, and matching role names.
5. Target the OU containing the inventory accounts.
6. Enable automatic deployment for accounts later added to that OU.
7. Select exactly one StackSet deployment Region. IAM roles are global, so
   deploying the same named role in several Regions would conflict.

Service-managed StackSets do not deploy into the management account. Inventory
for the management account, if ever required, must be onboarded separately.

## 4. Configure and restart the Broker

Set these non-secret values in the Broker `.env`:

```dotenv
AWS_FEDERATION_HUB_ROLE_ARN=arn:aws:iam::<tooling-account-id>:role/MsgBrokerFederationHubRole
AWS_FEDERATION_AUDIENCE=msg-broker-aws
AWS_STS_REGION=us-east-1
```

Then rebuild/restart with the normal production Compose path. Register one
Provider Account per AWS account using that account's 12-digit ID, its
`MsgBrokerInventoryRole` ARN, and an explicit Region list.

The adapter inventories `pending`, `running`, `stopping`, and `stopped`
instances. It excludes `shutting-down` and `terminated` instances. Storage is
the sum of all attached EBS volumes; EC2 instance-store disks are deliberately
excluded.
