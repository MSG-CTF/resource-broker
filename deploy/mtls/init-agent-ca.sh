#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this script as root." >&2
  exit 1
fi

CA_DIR="${MSG_BROKER_AGENT_CA_DIR:-/etc/msg-broker/pki/agent-ca}"
CA_DAYS="${MSG_BROKER_AGENT_CA_DAYS:-365}"
umask 077

if [[ -e "${CA_DIR}/private/ca.key" || -e "${CA_DIR}/ca.crt" ]]; then
  if [[ -s "${CA_DIR}/private/ca.key" && -s "${CA_DIR}/ca.crt" ]]; then
    echo "Agent client CA already exists at ${CA_DIR}; nothing changed."
    exit 0
  fi
  echo "Incomplete CA state exists at ${CA_DIR}; inspect it before retrying." >&2
  exit 1
fi

install -d -m 0700 "${CA_DIR}" "${CA_DIR}/private" "${CA_DIR}/newcerts"
install -d -m 0755 "${CA_DIR}/crl"
install -d -m 0700 "${CA_DIR}/issued" "${CA_DIR}/requests"
: > "${CA_DIR}/index.txt"
printf '1000\n' > "${CA_DIR}/serial"
printf '1000\n' > "${CA_DIR}/crlnumber"

cat > "${CA_DIR}/openssl.cnf" <<EOF
[ ca ]
default_ca = CA_default

[ CA_default ]
dir = ${CA_DIR}
database = \$dir/index.txt
new_certs_dir = \$dir/newcerts
certificate = \$dir/ca.crt
serial = \$dir/serial
private_key = \$dir/private/ca.key
crlnumber = \$dir/crlnumber
crl = \$dir/crl/agent-ca.crl
default_days = 75
default_crl_days = 90
default_md = sha256
policy = policy_agent
unique_subject = yes
copy_extensions = none
x509_extensions = client_cert

[ policy_agent ]
commonName = supplied
countryName = optional
stateOrProvinceName = optional
localityName = optional
organizationName = optional
organizationalUnitName = optional
emailAddress = optional

[ req ]
distinguished_name = req_dn
prompt = no

[ req_dn ]
commonName = MSG Broker Node Agent Client CA

[ v3_ca ]
basicConstraints = critical, CA:true, pathlen:0
keyUsage = critical, keyCertSign, cRLSign
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid:always

[ client_cert ]
basicConstraints = critical, CA:false
keyUsage = critical, digitalSignature
extendedKeyUsage = critical, clientAuth
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid,issuer
EOF

openssl genpkey \
  -algorithm EC \
  -pkeyopt ec_paramgen_curve:P-256 \
  -out "${CA_DIR}/private/ca.key"

openssl req \
  -x509 \
  -new \
  -sha256 \
  -days "${CA_DAYS}" \
  -config "${CA_DIR}/openssl.cnf" \
  -extensions v3_ca \
  -key "${CA_DIR}/private/ca.key" \
  -out "${CA_DIR}/ca.crt"

openssl ca \
  -batch \
  -config "${CA_DIR}/openssl.cnf" \
  -gencrl \
  -out "${CA_DIR}/crl/agent-ca.crl"

chmod 0600 "${CA_DIR}/private/ca.key" "${CA_DIR}/openssl.cnf"
chmod 0644 "${CA_DIR}/ca.crt" "${CA_DIR}/crl/agent-ca.crl"

echo "Agent client CA created at ${CA_DIR}."
openssl x509 -in "${CA_DIR}/ca.crt" -noout -subject -dates -fingerprint -sha256
