"""Integration and lifecycle tests for the HTTP service."""

import json
import threading
import time
import unittest
from http import HTTPStatus
from http.client import HTTPConnection

from app.main import Service


class TestService(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Bind to port 0 to let OS allocate an ephemeral free port
        cls.service = Service(host="127.0.0.1", port=0)
        cls.server_thread = threading.Thread(target=cls.service.start, daemon=True)
        cls.server_thread.start()

        cls.host, cls.port = cls.service.server_address
        # Wait until server is listening
        time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.service.shutdown(reason="test teardown")
        cls.server_thread.join(timeout=3)

    def _request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        headers: dict | None = None,
    ):
        conn = HTTPConnection(self.host, self.port, timeout=5)
        req_headers = {"Content-Type": "application/json"} if body is not None else {}
        if headers:
            req_headers.update(headers)
        data = json.dumps(body) if body is not None else None
        conn.request(method, path, body=data, headers=req_headers)
        response = conn.getresponse()
        raw_data = response.read()
        conn.close()
        parsed_body = json.loads(raw_data.decode("utf-8")) if raw_data else None
        return response.status, parsed_body, dict(response.getheaders())

    def test_01_health_and_readiness(self):
        """Verify liveness and readiness probe responses."""
        status, body, headers = self._request("GET", "/healthz")
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(body, {"status": "ok"})
        self.assertIn("X-Request-ID", headers)

        status, body, _ = self._request("GET", "/readyz")
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(body, {"status": "ready"})

    def test_02_validation_rejection(self):
        """Verify invalid payloads return 422 Unprocessable Entity."""
        test_cases = [
            ({}, "Missing required field: 'name'"),
            ({"name": ""}, "Field 'name' cannot be empty or whitespace"),
            ({"name": "   "}, "Field 'name' cannot be empty or whitespace"),
            ({"name": 12345}, "Field 'name' must be a string"),
            ({"name": "a" * 101}, "Field 'name' must be 100 characters or fewer"),
        ]
        for payload, expected_error in test_cases:
            with self.subTest(payload=payload):
                status, body, _ = self._request("POST", "/items", payload)
                self.assertEqual(status, HTTPStatus.UNPROCESSABLE_ENTITY)
                self.assertIn("detail", body)
                self.assertEqual(body["detail"], expected_error)

    def test_03_create_and_list_items(self):
        """Verify item creation and listing."""
        status, created_item, _ = self._request("POST", "/items", {"name": "sensor-1"})
        self.assertEqual(status, HTTPStatus.CREATED)
        self.assertEqual(created_item["name"], "sensor-1")
        self.assertIsInstance(created_item["id"], int)

        status, list_resp, _ = self._request("GET", "/items")
        self.assertEqual(status, HTTPStatus.OK)
        self.assertTrue(any(item["name"] == "sensor-1" for item in list_resp["items"]))

    def test_04_readiness_and_shutdown_lifecycle(self):
        """Verify readiness status changes and server cleanly terminates on shutdown."""
        # 1. While running, readyz is 200 OK
        status, body, _ = self._request("GET", "/readyz")
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(body["status"], "ready")

        # 2. If readiness is temporarily withdrawn (simulating draining), readyz is 503
        self.service.is_ready.clear()
        status, body, _ = self._request("GET", "/readyz")
        self.assertEqual(status, HTTPStatus.SERVICE_UNAVAILABLE)
        self.assertEqual(body["status"], "shutting down")

        # Restore ready state for the rest of setupClass/tearDownClass
        self.service.is_ready.set()

        # 3. Dedicated shutdown test instance to verify thread termination
        temp_service = Service(host="127.0.0.1", port=0)
        temp_thread = threading.Thread(target=temp_service.start, daemon=True)
        temp_thread.start()
        time.sleep(0.05)

        self.assertTrue(temp_service.is_ready.is_set())
        temp_service.shutdown(reason="test termination")
        temp_thread.join(timeout=3)

        self.assertFalse(temp_thread.is_alive())
        self.assertFalse(temp_service.is_ready.is_set())

    def test_05_openapi_contract_endpoint(self):
        """Verify the service serves a valid OpenAPI 3.1.0 specification."""
        status, body, _ = self._request("GET", "/openapi.json")
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(body["openapi"], "3.1.0")
        self.assertIn("/healthz", body["paths"])
        self.assertIn("/readyz", body["paths"])
        self.assertIn("/items", body["paths"])


if __name__ == "__main__":
    unittest.main()
