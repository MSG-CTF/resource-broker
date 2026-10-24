#!/bin/bash
set -euo pipefail

if [[ "$EUID" -ne 0 || "$#" -ne 1 ]]; then
    echo "Usage: sudo bash gateway/deploy/install.sh GATEWAY_PUBLIC_IP" >&2
    exit 1
fi

gateway_repo=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
gateway_ip=$1
gateway_home=/opt/msgctf-gateway
gateway_config=/etc/msgctf-gateway

test -x /usr/sbin/nginx
test -f /etc/nginx/stream-conf.d/00-msgctf-gateway.conf
test -f /etc/nginx/conf.d/msgctf-gateway-health.conf
test -f "$gateway_config/runtime.env"

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y python3-venv sudo

if ! id -u msgctf-gateway >/dev/null 2>&1; then
    useradd --system --user-group --no-create-home --shell /usr/sbin/nologin msgctf-gateway
fi
if systemctl cat msgctf-gateway.service >/dev/null 2>&1; then
    systemctl stop msgctf-gateway.service
fi

install -d -o root -g root -m 755 "$gateway_home" "$gateway_home/gateway"
install -d -o root -g root -m 700 "$gateway_config" /var/lib/msgctf-gateway
chown root:root "$gateway_config/runtime.env"
chmod 600 "$gateway_config/runtime.env"
install -o root -g root -m 644 "$gateway_repo"/gateway/*.py "$gateway_home/gateway/"
install -o root -g root -m 644 "$gateway_repo/gateway/requirements.txt" "$gateway_home/gateway/requirements.txt"

/usr/bin/python3 - "$gateway_ip" "$gateway_config/config.json" <<'PY'
from ipaddress import ip_address
import json
from pathlib import Path
import sys

address = ip_address(sys.argv[1])
if not address.is_global or address.is_multicast or address.is_reserved:
    raise SystemExit("A public Gateway IP is required")
path = Path(sys.argv[2])
expected = {"base_domain": "gate.msgctf.kr", "gateway_public_ip": str(address)}
if path.exists():
    if json.loads(path.read_text()) != expected:
        raise SystemExit("Existing Gateway configuration differs; review it before upgrading")
else:
    path.write_text(json.dumps(expected, indent=2) + "\n", encoding="utf-8")
path.chmod(0o600)
PY

/usr/bin/python3 -m venv "$gateway_home/venv"
"$gateway_home/venv/bin/python" -m pip install -r "$gateway_home/gateway/requirements.txt"
visudo -cf "$gateway_repo/gateway/deploy/msgctf-gateway.sudoers"
install -o root -g root -m 440 "$gateway_repo/gateway/deploy/msgctf-gateway.sudoers" /etc/sudoers.d/msgctf-gateway
install -o root -g root -m 644 "$gateway_repo/gateway/deploy/msgctf-gateway.service" /etc/systemd/system/msgctf-gateway.service

install -d -m 700 /var/backups/msgctf-gateway
gateway_backup=$(mktemp -d /var/backups/msgctf-gateway/api.XXXXXXXX)
cp -a /etc/nginx/conf.d/msgctf-gateway-health.conf "$gateway_backup/health.conf"
install -m 644 "$gateway_repo/deploy/nginx/problem-gateway/health.conf" /etc/nginx/conf.d/msgctf-gateway-health.conf
if ! /usr/sbin/nginx -t; then
    cp -a "$gateway_backup/health.conf" /etc/nginx/conf.d/msgctf-gateway-health.conf
    exit 1
fi
systemctl daemon-reload
systemctl enable msgctf-gateway.service
systemctl restart msgctf-gateway.service
systemctl reload nginx
echo "Installed. Verify the Gateway service and routing before using Runtime routes."
