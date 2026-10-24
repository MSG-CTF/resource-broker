#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "Usage: $0 <bootstrap-version> [output-directory]" >&2
  exit 1
fi

VERSION="$1"
if [[ ! "${VERSION}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "bootstrap-version must use X.Y.Z format." >&2
  exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
if [[ $# -eq 1 ]]; then
  cd "${PROJECT_ROOT}"
  OUTPUT_DIR="dist"
else
  OUTPUT_DIR="$2"
fi
BUNDLE_NAME="msg-broker-node-agent-bootstrap-${VERSION}"
WORK_DIR="$(mktemp -d)"

cleanup() {
  rm -rf -- "${WORK_DIR}"
}
trap cleanup EXIT

mkdir -p \
  "${WORK_DIR}/${BUNDLE_NAME}/deploy/bootstrap" \
  "${WORK_DIR}/${BUNDLE_NAME}/node-agent/k8s" \
  "${OUTPUT_DIR}"
cp \
  "${SCRIPT_DIR}/node-agent-bootstrap.sh" \
  "${WORK_DIR}/${BUNDLE_NAME}/deploy/bootstrap/node-agent-bootstrap.sh"
cp \
  "${PROJECT_ROOT}/node-agent/k8s/rbac.yaml" \
  "${PROJECT_ROOT}/node-agent/k8s/daemonset.yaml" \
  "${WORK_DIR}/${BUNDLE_NAME}/node-agent/k8s/"
chmod 0755 \
  "${WORK_DIR}/${BUNDLE_NAME}/deploy/bootstrap/node-agent-bootstrap.sh"
chmod 0644 \
  "${WORK_DIR}/${BUNDLE_NAME}/node-agent/k8s/rbac.yaml" \
  "${WORK_DIR}/${BUNDLE_NAME}/node-agent/k8s/daemonset.yaml"

(
  cd "${WORK_DIR}/${BUNDLE_NAME}"
  sha256sum \
    deploy/bootstrap/node-agent-bootstrap.sh \
    node-agent/k8s/rbac.yaml \
    node-agent/k8s/daemonset.yaml \
    > SHA256SUMS
)

tar \
  -C "${WORK_DIR}" \
  -czf "${OUTPUT_DIR}/${BUNDLE_NAME}.tar.gz" \
  "${BUNDLE_NAME}"
sha256sum "${OUTPUT_DIR}/${BUNDLE_NAME}.tar.gz" \
  > "${OUTPUT_DIR}/${BUNDLE_NAME}.tar.gz.sha256"

echo "Bootstrap bundle created: ${OUTPUT_DIR}/${BUNDLE_NAME}.tar.gz"
