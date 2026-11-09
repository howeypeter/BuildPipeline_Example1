"""Minimal sidecar microservice using only Python standard library.

Endpoints:
    GET    /healthz   Liveness probe (200 OK while process is alive)
    GET    /readyz    Readiness probe (200 OK while accepting traffic, 503 during drain)
    GET    /status    Telemetry and sidecar operational status

Shutdown:
    Handles SIGTERM and SIGINT, flips readiness to 503, and drains connections.
"""

import argparse
import json
import logging
import os
import signal
import sys
import threading
import time
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class JsonFormatter(logging.Formatter):
    """Outputs log records formatted as single line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": "svc_sidecar",
        }
        for key in ("method", "path", "status", "duration_ms", "client_ip"):
            val = getattr(record, key, None)
            if val is not None:
                log_entry[key] = val
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry)


def setup_logging(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("svc_sidecar")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.propagate = False
    return logger


class SidecarHandler(BaseHTTPRequestHandler):
    """HTTP request handler for sidecar telemetry and probe endpoints."""

    server_version = "SidecarService/1.0"

    def log_message(self, format: str, *args: object) -> None:
        pass

    def _send_json(self, status: HTTPStatus, body: dict) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        start_time = time.perf_counter()
        status_code = HTTPStatus.OK

        if self.path == "/healthz":
            self._send_json(HTTPStatus.OK, {"status": "alive", "service": "svc_sidecar"})
            status_code = HTTPStatus.OK
        elif self.path == "/readyz":
            if self.server.service.is_ready():
                self._send_json(HTTPStatus.OK, {"status": "ready", "service": "svc_sidecar"})
                status_code = HTTPStatus.OK
            else:
                self._send_json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"status": "draining", "service": "svc_sidecar"},
                )
                status_code = HTTPStatus.SERVICE_UNAVAILABLE
        elif self.path == "/status":
            self._send_json(
                HTTPStatus.OK,
                {
                    "service": "svc_sidecar",
                    "role": "metrics_and_telemetry_sidecar",
                    "uptime_seconds": round(time.time() - self.server.service.start_time, 2),
                    "status": "operational",
                },
            )
            status_code = HTTPStatus.OK
        else:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not Found"})
            status_code = HTTPStatus.NOT_FOUND

        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        self.server.service.logger.info(
            f"GET {self.path} {status_code.value}",
            extra={
                "method": "GET",
                "path": self.path,
                "status": status_code.value,
                "duration_ms": duration_ms,
                "client_ip": self.client_address[0] if self.client_address else "unknown",
            },
        )


class SidecarService:
    """Manages sidecar HTTP server and lifecycle."""

    def __init__(self, host: str = "0.0.0.0", port: int = 8000, log_level: str = "INFO"):
        self.host = host
        self.port = port
        self.logger = setup_logging(log_level)
        self._ready = threading.Event()
        self._shutdown_event = threading.Event()
        self.start_time = time.time()
        self.server: ThreadingHTTPServer | None = None

    def is_ready(self) -> bool:
        return self._ready.is_set()

    def set_ready(self, ready: bool) -> None:
        if ready:
            self._ready.set()
        else:
            self._ready.clear()

    def start(self) -> None:
        self.server = ThreadingHTTPServer((self.host, self.port), SidecarHandler)
        self.server.service = self
        self.server.daemon_threads = True

        self.set_ready(True)
        self.logger.info(
            f"Sidecar listening on {self.host}:{self.port}",
            extra={"port": self.port, "host": self.host},
        )

        try:
            self.server.serve_forever()
        finally:
            self.set_ready(False)
            self.server.server_close()
            self.logger.info("Sidecar server stopped")

    def shutdown(self) -> None:
        self.logger.info("Initiating graceful sidecar shutdown")
        self.set_ready(False)
        if self.server:
            self.server.shutdown()


def run_service(host: str, port: int, log_level: str) -> None:
    service = SidecarService(host=host, port=port, log_level=log_level)

    def handle_signal(signum: int, frame: object) -> None:
        sig_name = signal.Signals(signum).name
        service.logger.info(f"Received signal {sig_name}, draining")
        threading.Thread(target=service.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, handle_signal)

    service.start()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sidecar Microservice")
    parser.add_argument(
        "--host",
        default=os.environ.get("HOST", "0.0.0.0"),
        help="Host to bind (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", "8000")),
        help="Port to bind (default: 8000)",
    )
    parser.add_argument(
        "--log-level",
        default=os.environ.get("LOG_LEVEL", "INFO"),
        help="Log level (default: INFO)",
    )
    args = parser.parse_args()
    run_service(host=args.host, port=args.port, log_level=args.log_level)
