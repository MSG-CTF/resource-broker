#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"

RESOURCE_TARGET_ID="${MSG_BROKER_RESOURCE_TARGET_ID:-}"
K3S_VERSION="${MSG_BROKER_K3S_VERSION:-}"
AGENT_IMAGE="${MSG_BROKER_AGENT_IMAGE:-}"
ENROLLMENT_TOKEN_FILE="${MSG_BROKER_ENROLLMENT_TOKEN_FILE:-}"
ENROLLMENT_URL="${MSG_BROKER_ENROLLMENT_URL:-https://agents.mjsec.kr/v1/agent/enrollments}"
OBSERVATIONS_URL="${MSG_BROKER_OBSERVATIONS_URL:-https://agents.mjsec.kr/v1/agent/observations}"
MANIFEST_DIR="${MSG_BROKER_MANIFEST_DIR:-${PROJECT_ROOT}/node-agent/k8s}"
TLS_DIR="${MSG_BROKER_AGENT_TLS_DIR:-/etc/msg-broker-agent/tls}"
STATE_DIR="${MSG_BROKER_BOOTSTRAP_STATE_DIR:-/var/lib/msg-broker-bootstrap}"
STATE_FILE="${STATE_DIR}/node-agent.state"
RENEW_BEFORE_SECONDS="${MSG_BROKER_CERT_RENEW_BEFORE_SECONDS:-604800}"
DELIVERY_TIMEOUT_SECONDS="${MSG_BROKER_DELIVERY_TIMEOUT_SECONDS:-90}"
VERIFY_DELIVERY="${MSG_BROKER_VERIFY_DELIVERY:-true}"
K3S_INSTALL_URL="${MSG_BROKER_K3S_INSTALL_URL:-https://get.k3s.io}"
WORK_DIR=""

log() {
  printf '[msg-broker-bootstrap] %s\n' "$*"
}

fail() {
  printf '[msg-broker-bootstrap] ERROR: %s\n' "$*" >&2
  exit 1
}

cleanup() {
  if [[ -n "${WORK_DIR}" && -d "${WORK_DIR}" ]]; then
    rm -rf -- "${WORK_DIR}"
  fi
}
trap cleanup EXIT

usage() {
  cat <<'EOF'
Usage: sudo env <settings> bash node-agent-bootstrap.sh <action>

Actions:
  install  Install/reconcile k3s, certificate, and Node Agent.
  update   Reconcile to the requested k3s and Node Agent versions.
  check    Verify the local k3s, certificate, DaemonSet, and delivery state.
  remove   Remove only the Node Agent and local certificate. k3s is preserved.

Required settings for install/update:
  MSG_BROKER_RESOURCE_TARGET_ID=<canonical-lowercase-uuid>
  MSG_BROKER_K3S_VERSION=<vX.Y.Z+k3sN>
  MSG_BROKER_AGENT_IMAGE=<repository>@sha256:<64-hex-digest>

Required only when no usable certificate is already installed:
  MSG_BROKER_ENROLLMENT_TOKEN_FILE=<root-readable-token-file>
EOF
}

require_root() {
  if [[ ${EUID} -ne 0 ]]; then
    fail "Run this script as root."
  fi
}

validate_platform() {
  if [[ ! -r /etc/os-release ]]; then
    fail "Ubuntu could not be identified because /etc/os-release is missing."
  fi
  # shellcheck disable=SC1091
  . /etc/os-release
  if [[ "${ID:-}" != "ubuntu" ]]; then
    fail "Only Ubuntu is supported by this Bootstrap version."
  fi
  if [[ "$(uname -m)" != "x86_64" ]]; then
    fail "Only Ubuntu AMD64 is supported by this Bootstrap version."
  fi
}

require_https_url() {
  local name="$1"
  local value="$2"
  if [[ ! "${value}" =~ ^https://[^[:space:]]+$ ]]; then
    fail "${name} must be an https:// URL without whitespace."
  fi
}

validate_non_negative_integer() {
  local name="$1"
  local value="$2"
  if [[ ! "${value}" =~ ^[0-9]+$ ]]; then
    fail "${name} must be a non-negative integer."
  fi
}

validate_resource_target_id() {
  if [[ ! "${RESOURCE_TARGET_ID}" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]]; then
    fail "MSG_BROKER_RESOURCE_TARGET_ID must be a canonical lowercase UUID."
  fi
}

validate_desired_versions() {
  if [[ ! "${K3S_VERSION}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+\+k3s[0-9]+$ ]]; then
    fail "MSG_BROKER_K3S_VERSION must look like vX.Y.Z+k3sN."
  fi
  if [[ ! "${AGENT_IMAGE}" =~ ^[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}$ ]]; then
    fail "MSG_BROKER_AGENT_IMAGE must be pinned by a sha256 digest."
  fi
}

state_value() {
  local key="$1"
  if [[ ! -r "${STATE_FILE}" ]]; then
    return 0
  fi
  sed -n "s/^${key}=//p" "${STATE_FILE}" | head -n 1
}

load_state_defaults() {
  if [[ -z "${RESOURCE_TARGET_ID}" ]]; then
    RESOURCE_TARGET_ID="$(state_value RESOURCE_TARGET_ID)"
  fi
  if [[ -z "${K3S_VERSION}" ]]; then
    K3S_VERSION="$(state_value K3S_VERSION)"
  fi
  if [[ -z "${AGENT_IMAGE}" ]]; then
    AGENT_IMAGE="$(state_value AGENT_IMAGE)"
  fi
}

install_dependencies() {
  local missing=false
  local command_name
  for command_name in curl jq openssl; do
    if ! command -v "${command_name}" >/dev/null 2>&1; then
      missing=true
    fi
  done
  if [[ "${missing}" == "false" ]]; then
    return
  fi

  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install --yes --no-install-recommends \
    ca-certificates \
    curl \
    jq \
    openssl
}

current_k3s_version() {
  if ! command -v k3s >/dev/null 2>&1; then
    return 0
  fi
  k3s --version 2>/dev/null | awk 'NR == 1 {print $3}'
}

install_or_update_k3s() {
  local current_version
  current_version="$(current_k3s_version)"
  if [[ "${current_version}" == "${K3S_VERSION}" ]]; then
    log "k3s ${K3S_VERSION} is already installed."
  else
    require_https_url "MSG_BROKER_K3S_INSTALL_URL" "${K3S_INSTALL_URL}"
    WORK_DIR="$(mktemp -d)"
    curl \
      --fail \
      --silent \
      --show-error \
      --location \
      --proto '=https' \
      --tlsv1.2 \
      "${K3S_INSTALL_URL}" \
      --output "${WORK_DIR}/install-k3s.sh"
    if [[ ! -s "${WORK_DIR}/install-k3s.sh" ]]; then
      fail "The k3s installer download was empty."
    fi
    log "Installing k3s ${K3S_VERSION}."
    INSTALL_K3S_VERSION="${K3S_VERSION}" \
      INSTALL_K3S_EXEC="server --write-kubeconfig-mode=0600" \
      sh "${WORK_DIR}/install-k3s.sh"
    rm -rf -- "${WORK_DIR}"
    WORK_DIR=""
  fi

  systemctl enable --now k3s
  local attempt
  for attempt in $(seq 1 60); do
    if systemctl is-active --quiet k3s \
        && k3s kubectl get nodes >/dev/null 2>&1; then
      return
    fi
    sleep 2
  done
  fail "k3s did not become ready within 120 seconds."
}

ensure_agent_identity() {
  if ! getent group 10001 >/dev/null; then
    if getent group msg-broker-node-agent >/dev/null; then
      fail "The msg-broker-node-agent group exists with an unexpected GID."
    fi
    groupadd --system --gid 10001 msg-broker-node-agent
  fi
  if ! getent passwd 10001 >/dev/null; then
    if getent passwd msg-broker-node-agent >/dev/null; then
      fail "The msg-broker-node-agent user exists with an unexpected UID."
    fi
    useradd \
      --system \
      --uid 10001 \
      --gid 10001 \
      --home-dir /nonexistent \
      --shell /usr/sbin/nologin \
      msg-broker-node-agent
  fi
  install -d -o root -g 10001 -m 0750 "${TLS_DIR}"
}

certificate_common_name() {
  openssl x509 \
    -in "${TLS_DIR}/client.crt" \
    -noout \
    -subject \
    -nameopt RFC2253 2>/dev/null |
    sed 's/^subject=//'
}

certificate_is_usable() {
  local check_seconds="$1"
  local filename
  for filename in client.crt client.key client-ca.crt resource_target_id; do
    if [[ ! -s "${TLS_DIR}/${filename}" ]]; then
      return 1
    fi
  done
  if [[ "$(tr -d '\r\n' < "${TLS_DIR}/resource_target_id")" != "${RESOURCE_TARGET_ID}" ]]; then
    return 1
  fi
  if [[ "$(certificate_common_name)" != "CN=${RESOURCE_TARGET_ID}" ]]; then
    return 1
  fi
  if ! openssl x509 \
      -in "${TLS_DIR}/client.crt" \
      -checkend "${check_seconds}" \
      -noout >/dev/null 2>&1; then
    return 1
  fi
  if ! cmp -s \
      <(openssl pkey -in "${TLS_DIR}/client.key" -pubout 2>/dev/null) \
      <(openssl x509 -in "${TLS_DIR}/client.crt" -pubkey -noout 2>/dev/null); then
    return 1
  fi
  openssl verify \
    -CAfile "${TLS_DIR}/client-ca.crt" \
    -purpose sslclient \
    "${TLS_DIR}/client.crt" >/dev/null 2>&1
}

enroll_certificate() {
  if [[ -z "${ENROLLMENT_TOKEN_FILE}" \
      || ! -f "${ENROLLMENT_TOKEN_FILE}" \
      || ! -r "${ENROLLMENT_TOKEN_FILE}" ]]; then
    fail "A readable MSG_BROKER_ENROLLMENT_TOKEN_FILE is required to enroll a certificate."
  fi
  require_https_url "MSG_BROKER_ENROLLMENT_URL" "${ENROLLMENT_URL}"

  local enrollment_token
  enrollment_token="$(tr -d '\r\n' < "${ENROLLMENT_TOKEN_FILE}")"
  if [[ ! "${enrollment_token}" =~ ^mbe_[A-Za-z0-9_-]{40,252}$ ]]; then
    fail "The enrollment token file does not contain a valid token."
  fi

  WORK_DIR="$(mktemp -d)"
  chmod 0700 "${WORK_DIR}"
  openssl genpkey \
    -algorithm EC \
    -pkeyopt ec_paramgen_curve:P-256 \
    -out "${WORK_DIR}/client.key"
  openssl req \
    -new \
    -sha256 \
    -key "${WORK_DIR}/client.key" \
    -subj "/CN=${RESOURCE_TARGET_ID}" \
    -out "${WORK_DIR}/client.csr"

  jq -n \
    --arg resource_target_id "${RESOURCE_TARGET_ID}" \
    --rawfile csr "${WORK_DIR}/client.csr" \
    '{
      resource_target_id: $resource_target_id,
      certificate_signing_request_pem: $csr
    }' > "${WORK_DIR}/request.json"
  cat > "${WORK_DIR}/curl.conf" <<EOF
request = "POST"
header = "Accept: application/json"
header = "Content-Type: application/json"
header = "Authorization: Bearer ${enrollment_token}"
EOF
  chmod 0600 "${WORK_DIR}/curl.conf" "${WORK_DIR}/request.json"
  unset enrollment_token

  local http_status
  http_status="$(curl \
    --silent \
    --show-error \
    --proto '=https' \
    --tlsv1.2 \
    --config "${WORK_DIR}/curl.conf" \
    --data-binary "@${WORK_DIR}/request.json" \
    --output "${WORK_DIR}/response.json" \
    --write-out '%{http_code}' \
    "${ENROLLMENT_URL}")"
  if [[ "${http_status}" != "200" ]]; then
    local error_code
    error_code="$(jq -r '.error.code // "ENROLLMENT_REQUEST_FAILED"' \
      "${WORK_DIR}/response.json" 2>/dev/null || true)"
    fail "Certificate enrollment failed with HTTP ${http_status} (${error_code})."
  fi

  if [[ "$(jq -r '.resource_target_id // empty' "${WORK_DIR}/response.json")" != "${RESOURCE_TARGET_ID}" ]]; then
    fail "The enrollment response resource_target_id did not match."
  fi
  jq -er '.client_certificate_pem' \
    "${WORK_DIR}/response.json" > "${WORK_DIR}/client.crt"
  jq -er '.client_ca_pem' \
    "${WORK_DIR}/response.json" > "${WORK_DIR}/client-ca.crt"

  if [[ "$(openssl x509 \
      -in "${WORK_DIR}/client.crt" \
      -noout \
      -subject \
      -nameopt RFC2253 | sed 's/^subject=//')" != "CN=${RESOURCE_TARGET_ID}" ]]; then
    fail "The issued certificate identity did not match."
  fi
  openssl verify \
    -CAfile "${WORK_DIR}/client-ca.crt" \
    -purpose sslclient \
    "${WORK_DIR}/client.crt" >/dev/null
  if ! cmp -s \
      <(openssl pkey -in "${WORK_DIR}/client.key" -pubout) \
      <(openssl x509 -in "${WORK_DIR}/client.crt" -pubkey -noout); then
    fail "The issued certificate did not match the VM-local private key."
  fi

  install -o root -g 10001 -m 0640 \
    "${WORK_DIR}/client.crt" \
    "${TLS_DIR}/client.crt"
  install -o 10001 -g 10001 -m 0600 \
    "${WORK_DIR}/client.key" \
    "${TLS_DIR}/client.key"
  install -o root -g root -m 0644 \
    "${WORK_DIR}/client-ca.crt" \
    "${TLS_DIR}/client-ca.crt"
  printf '%s\n' "${RESOURCE_TARGET_ID}" > "${TLS_DIR}/resource_target_id"
  chown root:root "${TLS_DIR}/resource_target_id"
  chmod 0644 "${TLS_DIR}/resource_target_id"

  rm -rf -- "${WORK_DIR}"
  WORK_DIR=""
  log "A VM-local key and Agent certificate were enrolled."
}

single_node_name() {
  local nodes
  nodes="$(k3s kubectl get nodes -o jsonpath='{.items[*].metadata.name}')"
  # Intentionally split the Kubernetes node names on spaces.
  # shellcheck disable=SC2206
  local node_names=(${nodes})
  if [[ ${#node_names[@]} -ne 1 ]]; then
    fail "This Bootstrap version requires exactly one k3s node per VM."
  fi
  printf '%s' "${node_names[0]}"
}

render_daemonset() {
  local output_file="$1"
  local base_file="${MANIFEST_DIR}/daemonset.yaml"
  if [[ ! -s "${base_file}" ]]; then
    fail "Missing DaemonSet manifest: ${base_file}"
  fi

  local image_file
  image_file="$(mktemp)"
  k3s kubectl set image \
    --local \
    -f "${base_file}" \
    node-agent="${AGENT_IMAGE}" \
    -o yaml > "${image_file}"
  k3s kubectl set env \
    --local \
    -f "${image_file}" \
    --containers=node-agent \
    AGENT_DRY_RUN=false \
    BROKER_OBSERVATIONS_URL="${OBSERVATIONS_URL}" \
    -o yaml > "${output_file}"
  rm -f -- "${image_file}"
}

wait_for_delivery() {
  if [[ "${VERIFY_DELIVERY}" != "true" ]]; then
    log "Central delivery verification was explicitly disabled."
    return
  fi

  local elapsed=0
  while (( elapsed < DELIVERY_TIMEOUT_SECONDS )); do
    if k3s kubectl \
        -n msg-broker-system \
        logs daemonset/msg-broker-node-agent \
        --since=3m \
        --tail=200 2>/dev/null |
        grep -Fq "Delivered observation"; then
      log "The Node Agent delivered an observation to the Broker."
      return
    fi
    sleep 5
    elapsed=$((elapsed + 5))
  done
  fail "No successful Agent observation was seen within ${DELIVERY_TIMEOUT_SECONDS} seconds."
}

write_state() {
  install -d -o root -g root -m 0700 "${STATE_DIR}"
  local temporary_state
  temporary_state="$(mktemp "${STATE_DIR}/node-agent.state.XXXXXX")"
  cat > "${temporary_state}" <<EOF
RESOURCE_TARGET_ID=${RESOURCE_TARGET_ID}
K3S_VERSION=${K3S_VERSION}
AGENT_IMAGE=${AGENT_IMAGE}
OBSERVATIONS_URL=${OBSERVATIONS_URL}
CERTIFICATE_SERIAL=$(openssl x509 -in "${TLS_DIR}/client.crt" -noout -serial | sed 's/^serial=//')
CERTIFICATE_NOT_AFTER=$(openssl x509 -in "${TLS_DIR}/client.crt" -noout -enddate | sed 's/^notAfter=//')
LAST_RECONCILED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF
  chown root:root "${temporary_state}"
  chmod 0600 "${temporary_state}"
  mv -f -- "${temporary_state}" "${STATE_FILE}"
}

reconcile() {
  validate_resource_target_id
  validate_desired_versions
  validate_non_negative_integer \
    "MSG_BROKER_CERT_RENEW_BEFORE_SECONDS" \
    "${RENEW_BEFORE_SECONDS}"
  validate_non_negative_integer \
    "MSG_BROKER_DELIVERY_TIMEOUT_SECONDS" \
    "${DELIVERY_TIMEOUT_SECONDS}"
  require_https_url "MSG_BROKER_OBSERVATIONS_URL" "${OBSERVATIONS_URL}"
  if [[ "${VERIFY_DELIVERY}" != "true" && "${VERIFY_DELIVERY}" != "false" ]]; then
    fail "MSG_BROKER_VERIFY_DELIVERY must be true or false."
  fi
  if [[ ! -s "${MANIFEST_DIR}/rbac.yaml" ]]; then
    fail "Missing RBAC manifest: ${MANIFEST_DIR}/rbac.yaml"
  fi

  install_dependencies
  install_or_update_k3s
  ensure_agent_identity

  local certificate_changed=false
  if certificate_is_usable "${RENEW_BEFORE_SECONDS}"; then
    log "The installed Agent certificate is valid beyond the renewal window."
  else
    enroll_certificate
    certificate_changed=true
  fi

  local node_name
  node_name="$(single_node_name)"
  k3s kubectl annotate node "${node_name}" \
    "msg-broker.io/resource-target-id=${RESOURCE_TARGET_ID}" \
    --overwrite

  WORK_DIR="$(mktemp -d)"
  render_daemonset "${WORK_DIR}/daemonset.yaml"
  k3s kubectl apply -f "${MANIFEST_DIR}/rbac.yaml"
  k3s kubectl apply -f "${WORK_DIR}/daemonset.yaml"
  if [[ "${certificate_changed}" == "true" ]]; then
    k3s kubectl \
      -n msg-broker-system \
      rollout restart daemonset/msg-broker-node-agent
  fi
  k3s kubectl \
    -n msg-broker-system \
    rollout status daemonset/msg-broker-node-agent \
    --timeout=180s

  local applied_image
  applied_image="$(k3s kubectl \
    -n msg-broker-system \
    get daemonset msg-broker-node-agent \
    -o jsonpath='{.spec.template.spec.containers[?(@.name=="node-agent")].image}')"
  if [[ "${applied_image}" != "${AGENT_IMAGE}" ]]; then
    fail "The applied DaemonSet image did not match the requested digest."
  fi

  rm -rf -- "${WORK_DIR}"
  WORK_DIR=""
  wait_for_delivery
  write_state
  log "BOOTSTRAP_STATUS=ready"
}

check_installation() {
  load_state_defaults
  validate_resource_target_id
  validate_desired_versions
  command -v k3s >/dev/null 2>&1 || fail "k3s is not installed."
  command -v openssl >/dev/null 2>&1 || fail "OpenSSL is not installed."
  systemctl is-active --quiet k3s || fail "The k3s service is not active."
  if [[ "$(current_k3s_version)" != "${K3S_VERSION}" ]]; then
    fail "The installed k3s version does not match Bootstrap state."
  fi
  certificate_is_usable 0 || fail "The Agent certificate is missing or invalid."

  local node_name annotation applied_image
  node_name="$(single_node_name)"
  annotation="$(k3s kubectl get node "${node_name}" \
    -o jsonpath='{.metadata.annotations.msg-broker\.io/resource-target-id}')"
  if [[ "${annotation}" != "${RESOURCE_TARGET_ID}" ]]; then
    fail "The Kubernetes Node annotation does not match the resource target."
  fi
  k3s kubectl \
    -n msg-broker-system \
    rollout status daemonset/msg-broker-node-agent \
    --timeout=30s >/dev/null
  applied_image="$(k3s kubectl \
    -n msg-broker-system \
    get daemonset msg-broker-node-agent \
    -o jsonpath='{.spec.template.spec.containers[?(@.name=="node-agent")].image}')"
  if [[ "${applied_image}" != "${AGENT_IMAGE}" ]]; then
    fail "The DaemonSet image does not match Bootstrap state."
  fi
  if ! k3s kubectl \
      -n msg-broker-system \
      logs daemonset/msg-broker-node-agent \
      --since=10m \
      --tail=300 2>/dev/null |
      grep -Fq "Delivered observation"; then
    fail "No successful Agent delivery was found in the last 10 minutes."
  fi
  log "BOOTSTRAP_STATUS=ready"
  log "RESOURCE_TARGET_ID=${RESOURCE_TARGET_ID}"
  log "K3S_VERSION=${K3S_VERSION}"
  log "AGENT_IMAGE=${AGENT_IMAGE}"
}

remove_agent() {
  load_state_defaults
  local certificate_serial="unknown"
  if [[ -s "${TLS_DIR}/client.crt" ]]; then
    certificate_serial="$(openssl x509 \
      -in "${TLS_DIR}/client.crt" \
      -noout \
      -serial 2>/dev/null | sed 's/^serial=//' || true)"
  fi

  if command -v k3s >/dev/null 2>&1; then
    k3s kubectl \
      -n msg-broker-system \
      delete daemonset msg-broker-node-agent \
      --ignore-not-found
    k3s kubectl delete clusterrolebinding msg-broker-node-agent \
      --ignore-not-found
    k3s kubectl delete clusterrole msg-broker-node-agent \
      --ignore-not-found
    k3s kubectl delete namespace msg-broker-system \
      --ignore-not-found \
      --wait=true \
      --timeout=60s
  fi

  rm -f -- \
    "${TLS_DIR}/client.crt" \
    "${TLS_DIR}/client.key" \
    "${TLS_DIR}/client-ca.crt" \
    "${TLS_DIR}/resource_target_id"
  rm -f -- "${STATE_FILE}"
  log "The Node Agent and its local certificate were removed. k3s was preserved."
  log "CENTRAL_CERTIFICATE_REVOCATION_REQUIRED resource_target_id=${RESOURCE_TARGET_ID:-unknown} serial=${certificate_serial}"
  log "BOOTSTRAP_STATUS=removed"
}

case "${ACTION}" in
  install | update)
    require_root
    validate_platform
    reconcile
    ;;
  check)
    require_root
    validate_platform
    check_installation
    ;;
  remove)
    require_root
    validate_platform
    remove_agent
    ;;
  -h | --help | help)
    usage
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
