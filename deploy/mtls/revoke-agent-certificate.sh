#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this script as root." >&2
  exit 1
fi

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <resource_target_id>" >&2
  exit 1
fi

CA_DIR="${MSG_BROKER_AGENT_CA_DIR:-/etc/msg-broker/pki/agent-ca}"
RESOURCE_TARGET_ID="$(printf '%s' "$1" | tr 'A-F' 'a-f')"
CERT_FILE="${CA_DIR}/issued/${RESOURCE_TARGET_ID}/client.crt"

if [[ ! -s "${CERT_FILE}" ]]; then
  echo "Certificate not found: ${CERT_FILE}" >&2
  exit 1
fi

openssl ca \
  -batch \
  -config "${CA_DIR}/openssl.cnf" \
  -revoke "${CERT_FILE}"

openssl ca \
  -batch \
  -config "${CA_DIR}/openssl.cnf" \
  -gencrl \
  -out "${CA_DIR}/crl/agent-ca.crl"

chmod 0644 "${CA_DIR}/crl/agent-ca.crl"
echo "Certificate revoked. Reload Nginx after checking its configuration:"
echo "  sudo nginx -t && sudo systemctl reload nginx"
