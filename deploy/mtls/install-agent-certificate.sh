#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this script as root on the target VM." >&2
  exit 1
fi

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <certificate_bundle_directory>" >&2
  exit 1
fi

BUNDLE_DIR="$(realpath "$1")"
TLS_DIR="${MSG_BROKER_AGENT_TLS_DIR:-/etc/msg-broker-agent/tls}"

for filename in client.crt client.key client-ca.crt resource_target_id; do
  if [[ ! -s "${BUNDLE_DIR}/${filename}" ]]; then
    echo "Missing bundle file: ${BUNDLE_DIR}/${filename}" >&2
    exit 1
  fi
done

RESOURCE_TARGET_ID="$(tr -d '\r\n' < "${BUNDLE_DIR}/resource_target_id")"
if [[ ! "${RESOURCE_TARGET_ID}" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]]; then
  echo "Bundle resource_target_id must be a canonical lowercase UUID." >&2
  exit 1
fi

CERT_SUBJECT="$(
  openssl x509 \
    -in "${BUNDLE_DIR}/client.crt" \
    -noout \
    -subject \
    -nameopt RFC2253 |
    sed 's/^subject=//'
)"

if [[ "${CERT_SUBJECT}" != "CN=${RESOURCE_TARGET_ID}" ]]; then
  echo "Certificate CN does not match resource_target_id." >&2
  exit 1
fi

openssl verify \
  -CAfile "${BUNDLE_DIR}/client-ca.crt" \
  -purpose sslclient \
  "${BUNDLE_DIR}/client.crt"
openssl x509 -in "${BUNDLE_DIR}/client.crt" -checkend 0 -noout

if ! cmp -s \
  <(openssl pkey -in "${BUNDLE_DIR}/client.key" -pubout) \
  <(openssl x509 -in "${BUNDLE_DIR}/client.crt" -pubkey -noout); then
  echo "Client private key does not match the certificate." >&2
  exit 1
fi

install -d -o root -g 10001 -m 0750 "${TLS_DIR}"
install -o root -g 10001 -m 0640 \
  "${BUNDLE_DIR}/client.crt" \
  "${TLS_DIR}/client.crt"
install -o 10001 -g 10001 -m 0600 \
  "${BUNDLE_DIR}/client.key" \
  "${TLS_DIR}/client.key"
install -o root -g root -m 0644 \
  "${BUNDLE_DIR}/client-ca.crt" \
  "${TLS_DIR}/client-ca.crt"

echo "Agent certificate installed for ${RESOURCE_TARGET_ID} at ${TLS_DIR}."
