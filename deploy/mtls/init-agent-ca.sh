#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this script as root." >&2
  exit 1
fi

CA_DIR="${MSG_BROKER_AGENT_CA_DIR:-/etc/msg-broker/pki/agent-ca}"
CA_DAYS="${MSG_BROKER_AGENT_CA_DAYS:-365}"
ENROLLMENT_CA_DIR="${MSG_BROKER_AGENT_ENROLLMENT_CA_DIR:-${CA_DIR}/enrollment-ca}"
ENROLLMENT_CA_DAYS="${MSG_BROKER_AGENT_ENROLLMENT_CA_DAYS:-300}"
APP_GID="${MSG_BROKER_APP_GID:-10001}"
umask 077

initialize_root_ca() {
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
basicConstraints = critical, CA:true, pathlen:1
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
  echo "Agent client Root CA created at ${CA_DIR}."
}

initialize_enrollment_ca() {
  install -d -m 0770 "${ENROLLMENT_CA_DIR}"
  install -d -m 0750 "${ENROLLMENT_CA_DIR}/private"
  install -d -m 0770 \
    "${ENROLLMENT_CA_DIR}/newcerts" \
    "${ENROLLMENT_CA_DIR}/requests"
  install -d -m 0755 "${ENROLLMENT_CA_DIR}/crl"
  : > "${ENROLLMENT_CA_DIR}/index.txt"
  printf 'unique_subject = no\n' > "${ENROLLMENT_CA_DIR}/index.txt.attr"
  printf '2000\n' > "${ENROLLMENT_CA_DIR}/serial"
  printf '2000\n' > "${ENROLLMENT_CA_DIR}/crlnumber"

  cat > "${ENROLLMENT_CA_DIR}/openssl.cnf" <<EOF
[ ca ]
default_ca = CA_default

[ CA_default ]
dir = ${ENROLLMENT_CA_DIR}
database = \$dir/index.txt
new_certs_dir = \$dir/newcerts
certificate = \$dir/ca.crt
serial = \$dir/serial
private_key = \$dir/private/ca.key
crlnumber = \$dir/crlnumber
crl = \$dir/crl/enrollment-ca.crl
default_days = 75
default_crl_days = 90
default_md = sha256
policy = policy_agent
unique_subject = no
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
    -out "${ENROLLMENT_CA_DIR}/private/ca.key"
  openssl req \
    -x509 \
    -new \
    -sha256 \
    -days "${ENROLLMENT_CA_DAYS}" \
    -key "${ENROLLMENT_CA_DIR}/private/ca.key" \
    -subj "/CN=MSG Broker Agent Enrollment CA" \
    -addext "basicConstraints=critical,CA:true,pathlen:0" \
    -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -addext "subjectKeyIdentifier=hash" \
    -out "${ENROLLMENT_CA_DIR}/ca.crt"
  openssl ca \
    -batch \
    -config "${ENROLLMENT_CA_DIR}/openssl.cnf" \
    -gencrl \
    -out "${ENROLLMENT_CA_DIR}/crl/enrollment-ca.crl"

  echo "Online Agent enrollment CA created at ${ENROLLMENT_CA_DIR}."
}

if [[ -e "${CA_DIR}/private/ca.key" || -e "${CA_DIR}/ca.crt" ]]; then
  if [[ ! -s "${CA_DIR}/private/ca.key" || ! -s "${CA_DIR}/ca.crt" ]]; then
    echo "Incomplete Root CA state exists at ${CA_DIR}; inspect it before retrying." >&2
    exit 1
  fi
  echo "Agent client Root CA already exists at ${CA_DIR}; preserving it."
else
  initialize_root_ca
fi

if [[ -e "${ENROLLMENT_CA_DIR}/private/ca.key" || -e "${ENROLLMENT_CA_DIR}/ca.crt" ]]; then
  if [[ ! -s "${ENROLLMENT_CA_DIR}/private/ca.key" \
      || ! -s "${ENROLLMENT_CA_DIR}/ca.crt" \
      || ! -s "${ENROLLMENT_CA_DIR}/openssl.cnf" \
      || ! -s "${ENROLLMENT_CA_DIR}/index.txt.attr" ]]; then
    echo "Incomplete enrollment CA state exists at ${ENROLLMENT_CA_DIR}; inspect it before retrying." >&2
    exit 1
  fi
  echo "Online Agent enrollment CA already exists; preserving it."
else
  initialize_enrollment_ca
fi

openssl ca \
  -batch \
  -config "${CA_DIR}/openssl.cnf" \
  -gencrl \
  -out "${CA_DIR}/crl/agent-ca.crl"
openssl ca \
  -batch \
  -config "${ENROLLMENT_CA_DIR}/openssl.cnf" \
  -gencrl \
  -out "${ENROLLMENT_CA_DIR}/crl/enrollment-ca.crl"

openssl verify \
  -CAfile "${ENROLLMENT_CA_DIR}/ca.crt" \
  "${ENROLLMENT_CA_DIR}/ca.crt"

cp "${ENROLLMENT_CA_DIR}/ca.crt" "${ENROLLMENT_CA_DIR}/ca-chain.crt"
cat \
  "${CA_DIR}/ca.crt" \
  "${ENROLLMENT_CA_DIR}/ca.crt" \
  > "${CA_DIR}/client-trust-chain.crt"
cat \
  "${CA_DIR}/crl/agent-ca.crl" \
  "${ENROLLMENT_CA_DIR}/crl/enrollment-ca.crl" \
  > "${CA_DIR}/crl/agent-ca-bundle.crl"

if ! getent group "${APP_GID}" >/dev/null; then
  groupadd --system --gid "${APP_GID}" msg-broker-enrollment
fi

chown -R "root:${APP_GID}" "${ENROLLMENT_CA_DIR}"
find "${ENROLLMENT_CA_DIR}" -type d -exec chmod 0770 {} +
chmod 0750 "${ENROLLMENT_CA_DIR}/private"
chmod 0640 \
  "${ENROLLMENT_CA_DIR}/private/ca.key" \
  "${ENROLLMENT_CA_DIR}/openssl.cnf"
chmod 0660 \
  "${ENROLLMENT_CA_DIR}/index.txt" \
  "${ENROLLMENT_CA_DIR}/index.txt.attr" \
  "${ENROLLMENT_CA_DIR}/serial" \
  "${ENROLLMENT_CA_DIR}/crlnumber"
find "${ENROLLMENT_CA_DIR}" -type f \
  ! -path "${ENROLLMENT_CA_DIR}/private/ca.key" \
  ! -path "${ENROLLMENT_CA_DIR}/openssl.cnf" \
  ! -path "${ENROLLMENT_CA_DIR}/index.txt" \
  ! -path "${ENROLLMENT_CA_DIR}/index.txt.attr" \
  ! -path "${ENROLLMENT_CA_DIR}/serial" \
  ! -path "${ENROLLMENT_CA_DIR}/crlnumber" \
  -exec chmod 0644 {} +
chmod 0644 \
  "${CA_DIR}/client-trust-chain.crt" \
  "${CA_DIR}/crl/agent-ca-bundle.crl"

echo "Agent CA hierarchy is ready."
openssl x509 \
  -in "${ENROLLMENT_CA_DIR}/ca.crt" \
  -noout \
  -subject \
  -issuer \
  -dates \
  -fingerprint \
  -sha256
