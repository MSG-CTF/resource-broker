"""Root-owned controller; accepts structured input, never shell commands."""

from contextlib import contextmanager
from datetime import UTC, datetime
from ipaddress import IPv6Address, ip_address
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile


CONFIG = Path("/etc/msgctf-gateway/config.json")
REGISTRY = Path("/etc/nginx/stream-conf.d/10-msgctf-routes.conf")
STATE = Path("/var/lib/msgctf-gateway")
JOURNAL = STATE / "pending.json"
HEADER = "# Managed by msgctf-gateway. Do not edit.\n"
META = "# route: "
DELETED = "# deleted: "
INSTANCE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,254}$"
COMMAND_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}


class ControllerError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def public_ip(value: object) -> str:
    if not isinstance(value, str) or "%" in value:
        raise ValueError("A public IP address is required.")
    try:
        address = ip_address(value)
    except ValueError:
        raise ValueError("A public IP address is required.") from None
    if (
        not address.is_global
        or address.is_multicast
        or address.is_reserved
        or (isinstance(address, IPv6Address) and address.ipv4_mapped is not None)
    ):
        raise ValueError("A public IP address is required.")
    return str(address)


def validate_target(data: dict, own_ip: str) -> dict:
    address = public_ip(data.get("target_ip"))
    port = data.get("target_port")
    if address == own_ip:
        raise ValueError("The Gateway cannot be used as a backend.")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("The backend port must be an integer from 1 to 65535.")
    if data.get("protocol") not in ("http", "tcp"):
        raise ValueError("Protocol must be http or tcp.")
    return {"target_ip": address, "target_port": port, "protocol": data["protocol"]}


def read_config() -> tuple[str, str]:
    data = json.loads(CONFIG.read_text(encoding="utf-8"))
    domain = data["base_domain"]
    if not isinstance(domain, str) or len(domain) > 200 or not re.fullmatch(
        r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
        r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+",
        domain,
    ):
        raise ValueError("Invalid base domain.")
    return domain, public_ip(data["gateway_public_ip"])


def render(routes: dict[str, dict], deleted: set[str]) -> str:
    parts = [HEADER]
    for instance_id in sorted(deleted):
        parts.append(f"{DELETED}{json.dumps(instance_id)}\n")
    for instance_id in sorted(routes):
        route = routes[instance_id]
        address = route["target_ip"]
        destination = f"[{address}]" if ":" in address else address
        parts.append(
            f"{META}{json.dumps(route, sort_keys=True, ensure_ascii=True)}\n"
            "server {\n"
            "    listen 443 ssl;\n"
            "    listen [::]:443 ssl;\n"
            f"    server_name {route['hostname']};\n"
            f"    proxy_pass {destination}:{route['target_port']};\n"
            "}\n"
        )
    return "".join(parts)


def parse_registry(text: str, domain: str, own_ip: str) -> tuple[dict[str, dict], set[str]]:
    routes: dict[str, dict] = {}
    deleted: set[str] = set()
    hosts: set[str] = set()
    targets: set[tuple[str, int]] = set()
    for line in text.splitlines():
        if line.startswith(DELETED):
            instance_id = json.loads(line[len(DELETED):])
            if not isinstance(instance_id, str) or not re.fullmatch(INSTANCE_PATTERN, instance_id):
                raise ValueError("Invalid deleted instance ID.")
            deleted.add(instance_id)
            continue
        if not line.startswith(META):
            continue
        route = json.loads(line[len(META):])
        if set(route) != {
            "instance_id", "hostname", "target_ip", "target_port", "protocol", "created_at"
        }:
            raise ValueError("Invalid registry fields.")
        instance_id = route["instance_id"]
        if not isinstance(instance_id, str) or not re.fullmatch(INSTANCE_PATTERN, instance_id):
            raise ValueError("Invalid registry instance ID.")
        if validate_target(route, own_ip)["target_ip"] != route["target_ip"]:
            raise ValueError("Invalid registry IP.")
        if not re.fullmatch(r"[0-9a-f]{32}\." + re.escape(domain), route["hostname"]):
            raise ValueError("Invalid registry hostname.")
        if datetime.fromisoformat(route["created_at"]).tzinfo is None:
            raise ValueError("Invalid registry timestamp.")
        target = (route["target_ip"], route["target_port"])
        if instance_id in routes or route["hostname"] in hosts or target in targets:
            raise ValueError("Duplicate registry entry.")
        routes[instance_id] = route
        hosts.add(route["hostname"])
        targets.add(target)
    if routes.keys() & deleted or text != render(routes, deleted):
        raise ValueError("The managed registry was edited outside the controller.")
    return routes, deleted


def sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write(path: Path, content: str) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def reload_nginx() -> None:
    for command in (
        ["/usr/sbin/nginx", "-t", "-q"],
        ["/usr/bin/systemctl", "reload", "nginx"],
    ):
        result = subprocess.run(
            command, capture_output=True, timeout=10, check=False, env=COMMAND_ENV,
        )
        if result.returncode != 0:
            import syslog

            syslog.openlog("msgctf-gateway-controller")
            syslog.syslog(syslog.LOG_ERR, result.stderr.decode("utf-8", errors="replace")[:4000])
            raise ControllerError(502, "NGINX_APPLY_FAILED", "Nginx rejected the route change.")


def recover() -> None:
    if not JOURNAL.exists():
        return
    try:
        previous = json.loads(JOURNAL.read_text(encoding="utf-8"))["previous"]
        if previous is None:
            REGISTRY.unlink(missing_ok=True)
            sync_directory(REGISTRY.parent)
        elif isinstance(previous, str):
            atomic_write(REGISTRY, previous)
        else:
            raise ValueError("Invalid recovery journal.")
        reload_nginx()
        JOURNAL.unlink()
        sync_directory(STATE)
    except (OSError, ValueError, KeyError, ControllerError, subprocess.SubprocessError) as error:
        raise ControllerError(
            503, "NGINX_RECOVERY_REQUIRED", "Gateway recovery could not complete; operator attention is required.",
        ) from error


def apply_registry(candidate: str, previous: str | None) -> None:
    atomic_write(JOURNAL, json.dumps({"previous": previous}))
    try:
        atomic_write(REGISTRY, candidate)
        reload_nginx()
    except (OSError, ControllerError, subprocess.SubprocessError) as error:
        recover()
        raise ControllerError(
            502, "NGINX_APPLY_FAILED", "The route change failed; the previous configuration was restored.",
        ) from error
    JOURNAL.unlink()
    sync_directory(STATE)


@contextmanager
def registry_lock():
    import fcntl

    with (STATE / "controller.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ControllerError(503, "GATEWAY_BUSY", "Retry the request with the same instance ID.") from error
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def public_route(route: dict) -> dict:
    hostname = route["hostname"]
    return {
        "instance_id": route["instance_id"],
        "hostname": hostname,
        "port": 443,
        "tls": True,
        "protocol": route["protocol"],
        "url": f"https://{hostname}" if route["protocol"] == "http" else None,
        "created_at": route["created_at"],
    }


def dispatch(request: dict) -> tuple[int, dict | None]:
    operation = request.get("operation")
    fields = {"operation"} if operation == "reconcile" else {"operation", "instance_id"}
    if operation == "put":
        fields |= {"target_ip", "target_port", "protocol"}
    if operation not in {"put", "get", "delete", "reconcile"} or set(request) != fields:
        raise ControllerError(400, "INVALID_REQUEST", "Invalid controller request.")
    instance_id = request.get("instance_id", "")
    if operation != "reconcile" and (
        not isinstance(instance_id, str) or not re.fullmatch(INSTANCE_PATTERN, instance_id)
    ):
        raise ControllerError(422, "INVALID_INSTANCE_ID", "Instance ID must be 1-255 URL-safe characters.")
    try:
        domain, own_ip = read_config()
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise ControllerError(503, "GATEWAY_NOT_CONFIGURED", "Gateway configuration is missing or invalid.") from error
    target = None
    if operation == "put":
        try:
            target = validate_target(request, own_ip)
        except ValueError as error:
            raise ControllerError(422, "INVALID_TARGET", str(error)) from error
    with registry_lock():
        recover()
        previous = REGISTRY.read_text(encoding="utf-8") if REGISTRY.exists() else None
        try:
            routes, deleted = parse_registry(previous, domain, own_ip) if previous is not None else ({}, set())
        except (ValueError, TypeError, KeyError) as error:
            raise ControllerError(503, "REGISTRY_INVALID", "The managed route registry is invalid.") from error
        existing = routes.get(instance_id)
        if operation == "reconcile":
            if previous is None:
                apply_registry(render(routes, deleted), None)
            else:
                reload_nginx()
            return 200, None
        if operation == "get":
            if existing is None:
                raise ControllerError(404, "ROUTE_NOT_FOUND", "The instance has no registered route.")
            return 200, public_route(existing)
        if operation == "put":
            if instance_id in deleted:
                raise ControllerError(409, "INSTANCE_DELETED", "This instance ID was deleted; use a new deployment ID.")
            if existing is not None:
                if any(existing[key] != value for key, value in target.items()):
                    raise ControllerError(409, "INSTANCE_CONFLICT", "This instance ID already has a different destination or protocol.")
                return 200, public_route(existing)
            if any(
                (route["target_ip"], route["target_port"]) == (target["target_ip"], target["target_port"])
                for route in routes.values()
            ):
                raise ControllerError(409, "TARGET_IN_USE", "This VM port is already registered to another instance.")
            hostnames = {route["hostname"] for route in routes.values()}
            hostname = f"{secrets.token_hex(16)}.{domain}"
            while hostname in hostnames:
                hostname = f"{secrets.token_hex(16)}.{domain}"
            existing = {
                "instance_id": instance_id, "hostname": hostname, **target,
                "created_at": datetime.now(UTC).isoformat(),
            }
            routes[instance_id] = existing
        else:
            if instance_id in deleted:
                return 204, None
            routes.pop(instance_id, None)
            # shortcut: retain deleted IDs indefinitely; add deployment epochs before pruning.
            deleted.add(instance_id)
        candidate = render(routes, deleted)
        # Keep the durable registry and its generated Nginx content inseparable.
        assert parse_registry(candidate, domain, own_ip) == (routes, deleted)
        apply_registry(candidate, previous)
        return (201, public_route(existing)) if operation == "put" else (204, None)


def main() -> None:
    try:
        if os.geteuid() != 0 or len(sys.argv) != 1:
            raise ControllerError(503, "CONTROLLER_UNAVAILABLE", "The privileged controller is unavailable.")
        data = sys.stdin.buffer.read(4097)
        if len(data) > 4096:
            raise ControllerError(400, "INVALID_REQUEST", "The controller request is too large.")
        request = json.loads(data)
        if not isinstance(request, dict):
            raise ControllerError(400, "INVALID_REQUEST", "A JSON object is required.")
        status, body = dispatch(request)
    except ControllerError as error:
        status, body = error.status, {"error": {"code": error.code, "message": str(error)}}
    except (ValueError, UnicodeError):
        status, body = 400, {"error": {"code": "INVALID_REQUEST", "message": "Invalid controller JSON."}}
    except Exception:
        status, body = 503, {"error": {"code": "CONTROLLER_UNAVAILABLE", "message": "The route controller is unavailable."}}
    print(json.dumps({"status": status, "body": body}))


if __name__ == "__main__":
    main()
