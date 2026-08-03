from __future__ import annotations

import logging
import random
import signal
import threading
import time

from msg_broker_node_agent.collector import (
    KubernetesCollector,
    ObservationCollectionError,
)
from msg_broker_node_agent.sender import (
    ObservationDeliveryError,
    ObservationSender,
)
from msg_broker_node_agent.settings import AgentSettings, AgentSettingsError


LOGGER = logging.getLogger(__name__)


def _install_signal_handlers(stop_event: threading.Event) -> None:
    def request_stop(signum: int, _frame: object) -> None:
        LOGGER.info("Received signal %s; stopping node agent", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)


def _log_observation(payload: dict[str, object], *, dry_run: bool) -> None:
    node_allocatable = payload["node_allocatable"]
    allocated_requests = payload["allocated_requests"]
    containers = payload["containers"]
    LOGGER.info(
        "%sobservation node_allocatable=%s allocated_requests=%s containers=%d",
        "Dry-run " if dry_run else "Delivered ",
        node_allocatable,
        allocated_requests,
        len(containers) if isinstance(containers, list) else 0,
    )


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        settings = AgentSettings.from_environment()
        collector = KubernetesCollector.from_in_cluster(
            node_name=settings.node_name,
            resource_target_annotation=settings.resource_target_annotation,
            resource_target_id_override=settings.resource_target_id_override,
        )
        sender = None
        if not settings.dry_run:
            assert settings.broker_observations_url is not None
            sender = ObservationSender(
                url=settings.broker_observations_url,
                timeout_seconds=settings.request_timeout_seconds,
                ca_file=settings.broker_ca_file,
                client_cert_file=settings.broker_client_cert_file,
                client_key_file=settings.broker_client_key_file,
            )
    except (AgentSettingsError, ObservationDeliveryError) as error:
        LOGGER.error("Node agent configuration is invalid: %s", error)
        return 2
    except Exception:
        LOGGER.exception("Kubernetes in-cluster configuration could not be loaded")
        return 2

    stop_event = threading.Event()
    _install_signal_handlers(stop_event)

    initial_delay = random.uniform(0, settings.initial_jitter_seconds)
    LOGGER.info(
        "Node agent started node=%s interval=%ss initial_delay=%.1fs dry_run=%s",
        settings.node_name,
        settings.interval_seconds,
        initial_delay,
        settings.dry_run,
    )
    if stop_event.wait(initial_delay):
        return 0

    while not stop_event.is_set():
        cycle_started = time.monotonic()
        try:
            payload = collector.collect()
            if sender is not None:
                sender.send(payload)
            _log_observation(payload, dry_run=settings.dry_run)
        except (ObservationCollectionError, ObservationDeliveryError) as error:
            LOGGER.error("Node agent cycle failed: %s", error)
        except Exception:
            LOGGER.exception("Node agent cycle failed unexpectedly")

        elapsed = time.monotonic() - cycle_started
        wait_seconds = max(settings.interval_seconds - elapsed, 0)
        stop_event.wait(wait_seconds)

    return 0
