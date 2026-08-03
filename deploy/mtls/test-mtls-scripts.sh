#!/usr/bin/env bash
set -euo pipefail

TEST_ROOT="$(mktemp -d)"
CA_DIR="${TEST_ROOT}/ca"
TLS_DIR="${TEST_ROOT}/installed-tls"
RESOURCE_TARGET_ID="11111111-2222-4333-8444-555555555555"

cleanup() {
  rm -rf -- "${TEST_ROOT}"
}
trap cleanup EXIT

export MSG_BROKER_AGENT_CA_DIR="${CA_DIR}"
export MSG_BROKER_AGENT_TLS_DIR="${TLS_DIR}"

bash "$(dirname "$0")/init-agent-ca.sh"
bash "$(dirname "$0")/issue-agent-certificate.sh" "${RESOURCE_TARGET_ID}"
bash "$(dirname "$0")/install-agent-certificate.sh" \
  "${CA_DIR}/issued/${RESOURCE_TARGET_ID}"

test -s "${TLS_DIR}/client.crt"
test -s "${TLS_DIR}/client.key"

bash "$(dirname "$0")/revoke-agent-certificate.sh" "${RESOURCE_TARGET_ID}"

if openssl verify \
  -crl_check \
  -CAfile "${CA_DIR}/ca.crt" \
  -CRLfile "${CA_DIR}/crl/agent-ca.crl" \
  "${CA_DIR}/issued/${RESOURCE_TARGET_ID}/client.crt"; then
  echo "Revoked certificate was unexpectedly accepted." >&2
  exit 1
fi

echo "mTLS CA issue/install/revoke validation passed."
