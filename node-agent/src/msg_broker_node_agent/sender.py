from __future__ import annotations

import json
from pathlib import Path
import ssl
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ObservationDeliveryError(RuntimeError):
    pass


class ObservationSender:
    def __init__(
        self,
        *,
        url: str,
        timeout_seconds: int,
        ca_file: Path | None,
        client_cert_file: Path | None,
        client_key_file: Path | None,
    ) -> None:
        self._url = url
        self._timeout_seconds = timeout_seconds
        self._ssl_context: ssl.SSLContext | None = None
        if url.lower().startswith("https://"):
            try:
                self._ssl_context = ssl.create_default_context(
                    cafile=str(ca_file) if ca_file else None
                )
                if client_cert_file and client_key_file:
                    self._ssl_context.load_cert_chain(
                        certfile=str(client_cert_file),
                        keyfile=str(client_key_file),
                    )
            except (OSError, ssl.SSLError) as error:
                raise ObservationDeliveryError(
                    "The Broker TLS trust or client certificate could not be loaded"
                ) from error

    def send(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            self._url,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "msg-broker-node-agent/0.1.0",
            },
            method="POST",
        )
        try:
            with urlopen(
                request,
                timeout=self._timeout_seconds,
                context=self._ssl_context,
            ) as response:
                response_body = response.read(1_048_576)
                if not response_body:
                    return {}
                parsed = json.loads(response_body.decode("utf-8"))
                return parsed if isinstance(parsed, dict) else {}
        except HTTPError as error:
            raise ObservationDeliveryError(
                f"Broker rejected the observation with HTTP {error.code}"
            ) from error
        except (URLError, TimeoutError, OSError) as error:
            raise ObservationDeliveryError(
                "The observation could not be delivered to the Broker"
            ) from error
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ObservationDeliveryError(
                "The Broker returned an invalid JSON response"
            ) from error
