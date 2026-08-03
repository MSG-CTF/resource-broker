#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this script as root." >&2
  exit 1
fi

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "Usage: $0 <resource_target_id> [output_directory]" >&2
  exit 1
fi

CA_DIR="${MSG_BROKER_AGENT_CA_DIR:-/etc/msg-broker/pki/agent-ca}"
CERT_DAYS="${MSG_BROKER_AGENT_CERT_DAYS:-75}"
RESOURCE_TARGET_ID="$(printf '%s' "$1" | tr 'A-F' 'a-f')"

if [[ ! "${RESOURCE_TARGET_ID}" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]]; then
  echo "resource_target_id must be a canonical UUID." >&2
  exit 1
fi

if [[ ! -s "${CA_DIR}/private/ca.key" || ! -s "${CA_DIR}/ca.crt" ]]; then
  echo "Agent client CA is not initialized at ${CA_DIR}." >&2
  exit 1
fi

OUTPUT_DIR="${2:-${CA_DIR}/issued/${RESOURCE_TARGET_ID}}"
if [[ -e "${OUTPUT_DIR}/client.key" || -e "${OUTPUT_DIR}/client.crt" ]]; then
  echo "A bundle already exists at ${OUTPUT_DIR}; refusing to overwrite it." >&2
  exit 1
fi

umask 077
install -d -m 0700 "${OUTPUT_DIR}"
CSR_FILE="${CA_DIR}/requests/${RESOURCE_TARGET_ID}.csr"

openssl genpkey \
  -algorithm EC \
  -pkeyopt ec_paramgen_curve:P-256 \
  -out "${OUTPUT_DIR}/client.key"

openssl req \
  -new \
  -sha256 \
  -key "${OUTPUT_DIR}/client.key" \
  -subj "/CN=${RESOURCE_TARGET_ID}" \
  -out "${CSR_FILE}"

openssl ca \
  -batch \
  -notext \
  -config "${CA_DIR}/openssl.cnf" \
  -extensions client_cert \
  -days "${CERT_DAYS}" \
  -in "${CSR_FILE}" \
  -out "${OUTPUT_DIR}/client.crt"

cp "${CA_DIR}/ca.crt" "${OUTPUT_DIR}/client-ca.crt"
printf '%s\n' "${RESOURCE_TARGET_ID}" > "${OUTPUT_DIR}/resource_target_id"
chmod 0600 "${OUTPUT_DIR}/client.key"
chmod 0644 \
  "${OUTPUT_DIR}/client.crt" \
  "${OUTPUT_DIR}/client-ca.crt" \
  "${OUTPUT_DIR}/resource_target_id"

openssl verify \
  -CAfile "${CA_DIR}/ca.crt" \
  -purpose sslclient \
  "${OUTPUT_DIR}/client.crt"

echo "Agent certificate bundle created at ${OUTPUT_DIR}."
openssl x509 \
  -in "${OUTPUT_DIR}/client.crt" \
  -noout \
  -subject \
  -dates \
  -fingerprint \
  -sha256
