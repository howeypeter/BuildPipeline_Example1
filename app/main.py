"""Minimal production-ready HTTP service using only Python standard library.

Endpoints:
    GET    /healthz   Liveness probe (200 OK while process is alive)
    GET    /readyz    Readiness probe (200 OK while accepting traffic, 503 during startup/draining)
    GET    /items     List stored items
    POST   /items     Create item: {"name": "widget"} with validation

Logging:
    Emits structured JSON lines to stdout for cloud log forwarders.

Shutdown:
    Handles SIGTERM / SIGINT, flips readiness to 503, and drains in-flight requests.
"""

import argparse
import json
import logging
import os
import signal
import sys
import threading
import time
import uuid
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# --- Structured JSON Logging -------------------------------------------------


class JsonFormatter(logging.Formatter):
    """Outputs log records formatted as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Include custom context attributes passed via `extra`
        for key in (
            "method",
            "path",
            "status",
            "duration_ms",
            "client_ip",
            "request_id",
        ):
            val = getattr(record, key, None)
            if val is not None:
                log_entry[key] = val
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry)


def setup_logging(level: str = "INFO") -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root_logger = logging.getLogger("service")
    root_logger.handlers = [handler]
    root_logger.setLevel(level.upper())
    return root_logger


logger = logging.getLogger("service")


# --- In-Memory Thread-Safe Store ---------------------------------------------


class ItemStore:
    """Thread-safe in-memory store for items."""

    def __init__(self):
        self._items = []
        self._next_id = 1
        self._lock = threading.Lock()

    def list(self):
        with self._lock:
            return list(self._items)

    def create(self, name: str):
        with self._lock:
            item = {"id": self._next_id, "name": name}
            self._items.append(item)
            self._next_id += 1
            return item


# --- Validation --------------------------------------------------------------


def validate_item_payload(raw_body: bytes):
    """Validates raw request body. Returns (name, error_detail)."""
    if not raw_body:
        return None, "Request body is empty"
    try:
        data = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, "Malformed JSON payload"

    if not isinstance(data, dict):
        return None, "JSON body must be an object"

    name = data.get("name")
    if name is None:
        return None, "Missing required field: 'name'"
    if not isinstance(name, str):
        return None, "Field 'name' must be a string"

    cleaned_name = name.strip()
    if not cleaned_name:
        return None, "Field 'name' cannot be empty or whitespace"
    if len(cleaned_name) > 100:
        return None, "Field 'name' must be 100 characters or fewer"

    return cleaned_name, None


# --- OpenAPI Specification ---------------------------------------------------

OPENAPI_SPEC = {
    "openapi": "3.1.0",
    "info": {
        "title": "Microservice Core HTTP API",
        "version": "1.0.0",
        "description": "Minimal production ready HTTP service contract.",
    },
    "paths": {
        "/healthz": {"get": {"summary": "Liveness Probe"}},
        "/readyz": {"get": {"summary": "Readiness Probe"}},
        "/items": {
            "get": {"summary": "List Items"},
            "post": {"summary": "Create Item"},
        },
        "/openapi.json": {"get": {"summary": "OpenAPI Specification"}},
    },
}


# --- HTTP Request Handler ----------------------------------------------------


class ServiceRequestHandler(BaseHTTPRequestHandler):
    """Routes and handles HTTP requests."""

    # Set by Service instance
    store: ItemStore = None
    is_ready: threading.Event = None

    def handle_one_request(self):
        self._start_time = time.perf_counter()
        self._status_code = None
        self._request_id = str(uuid.uuid4())
        super().handle_one_request()
        if self._status_code is not None:
            duration_ms = round((time.perf_counter() - self._start_time) * 1000, 2)
            logger.info(
                "handled request",
                extra={
                    "method": self.command,
                    "path": self.path,
                    "status": self._status_code,
                    "duration_ms": duration_ms,
                    "client_ip": self.client_address[0] if self.client_address else "unknown",
                    "request_id": self._request_id,
                },
            )

    def log_message(self, format, *args):
        # Suppress default BaseHTTPRequestHandler stderr text logging
        pass

    def _send_json(self, status: HTTPStatus, body: dict):
        self._status_code = status.value
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Request-ID", self._request_id)
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/healthz":
            self._send_json(HTTPStatus.OK, {"status": "ok"})
        elif path == "/readyz":
            if self.is_ready and self.is_ready.is_set():
                self._send_json(HTTPStatus.OK, {"status": "ready"})
            else:
                self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"status": "shutting down"})
        elif path == "/items":
            self._send_json(HTTPStatus.OK, {"items": self.store.list()})
        elif path == "/openapi.json":
            self._send_json(HTTPStatus.OK, OPENAPI_SPEC)
        else:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not Found", "path": path})

    def do_POST(self):
        path = self.path.split("?")[0]
        if path != "/items":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not Found", "path": path})
            return

        content_length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_length)

        name, error = validate_item_payload(raw_body)
        if error:
            self._send_json(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                {"error": "Validation failed", "detail": error},
            )
            return

        item = self.store.create(name)
        self._send_json(HTTPStatus.CREATED, item)


# --- Application Service Server ----------------------------------------------


class Service:
    """Manages server lifecycle, signal trapping, and connection draining."""

    def __init__(self, host: str = "0.0.0.0", port: int = 8000):
        self.host = host
        self.port = port
        self.store = ItemStore()
        self.is_ready = threading.Event()
        self._stopping = threading.Event()

        # Bind shared state to handler class
        handler_class = type(
            "ConfiguredHandler",
            (ServiceRequestHandler,),
            {"store": self.store, "is_ready": self.is_ready},
        )
        self.httpd = ThreadingHTTPServer((host, port), handler_class)
        self.httpd.daemon_threads = False
        self.httpd.block_on_close = True

    @property
    def server_address(self):
        return self.httpd.server_address

    def start(self):
        self.is_ready.set()
        logger.info(
            "service started",
            extra={"path": f"http://{self.host}:{self.server_address[1]}"},
        )
        try:
            self.httpd.serve_forever(poll_interval=0.5)
        finally:
            logger.info("server stopped accepting traffic, draining connections")
            self.httpd.server_close()
            logger.info("service shutdown complete")

    def shutdown(self, reason: str = "signal"):
        if self._stopping.is_set():
            return
        self._stopping.set()
        # Flip readiness probe to 503 so load balancers withdraw traffic
        self.is_ready.clear()
        logger.info("shutdown initiated", extra={"reason": reason})
        # Execute shutdown on a background thread so serve_forever can exit
        threading.Thread(target=self.httpd.shutdown, daemon=True).start()


def main():
    parser = argparse.ArgumentParser(description="Minimal HTTP Web Service")
    parser.add_argument("--host", default=os.getenv("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    args = parser.parse_args()

    setup_logging(os.getenv("LOG_LEVEL", "INFO"))
    service = Service(host=args.host, port=args.port)

    def handle_signal(signum, frame):
        sig_name = signal.Signals(signum).name if hasattr(signal, "Signals") else str(signum)
        service.shutdown(reason=f"received {sig_name}")

    # Register OS signals for graceful shutdown
    for sig in ("SIGTERM", "SIGINT", "SIGBREAK"):
        if hasattr(signal, sig):
            signal.signal(getattr(signal, sig), handle_signal)

    service.start()


if __name__ == "__main__":
    main()
