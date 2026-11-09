"""Unit and lifecycle tests for svc_sidecar."""

import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from http import HTTPStatus
from http.server import ThreadingHTTPServer

from svc_sidecar.main import SidecarHandler, SidecarService


class TestSidecarService(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = SidecarService(host="127.0.0.1", port=0, log_level="WARNING")
        cls.server = ThreadingHTTPServer(
            (cls.service.host, cls.service.port), SidecarHandler
        )
        cls.server.service = cls.service
        cls.service.server = cls.server
        cls.port = cls.server.server_address[1]
        cls.service.port = cls.port
        cls.service.set_ready(True)

        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.service.set_ready(False)
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2.0)

    def _request(self, path: str):
        url = f"http://127.0.0.1:{self.port}{path}"
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=2) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return resp.status, data
        except urllib.error.HTTPError as err:
            data = json.loads(err.read().decode("utf-8"))
            return err.code, data

    def test_01_healthz_liveness(self):
        status, body = self._request("/healthz")
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(body.get("status"), "alive")
        self.assertEqual(body.get("service"), "svc_sidecar")

    def test_02_readyz_readiness(self):
        status, body = self._request("/readyz")
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(body.get("status"), "ready")

    def test_03_status_telemetry(self):
        status, body = self._request("/status")
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(body.get("service"), "svc_sidecar")
        self.assertEqual(body.get("status"), "operational")

    def test_04_readiness_toggle_to_503(self):
        self.service.set_ready(False)
        try:
            status, body = self._request("/readyz")
            self.assertEqual(status, HTTPStatus.SERVICE_UNAVAILABLE)
            self.assertEqual(body.get("status"), "draining")
        finally:
            self.service.set_ready(True)


if __name__ == "__main__":
    unittest.main()
