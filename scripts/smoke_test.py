"""Automated smoke test for running microservice containers.

Tests liveness (/healthz), readiness (/readyz), and service-specific endpoints
for any service matching app* or svc*.
"""

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request


def check_endpoint(url: str, expected_status: int = 200) -> dict | None:
    print(f"--> Checking {url} (expected {expected_status})")
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            if resp.status != expected_status:
                raise RuntimeError(f"Expected status {expected_status}, got {resp.status}")
            data = resp.read()
            print(f"    [OK] {url} passed ({resp.status})")
            return json.loads(data.decode("utf-8")) if data else None
    except urllib.error.HTTPError as err:
        if err.code != expected_status:
            raise RuntimeError(f"Expected status {expected_status}, got {err.code}")
        print(f"    [OK] {url} returned expected {err.code}")
        return None


def run_smoke_test(service: str, port: int, container_name: str) -> None:
    time.sleep(2)
    base_url = f"http://127.0.0.1:{port}"

    try:
        check_endpoint(f"{base_url}/healthz", 200)
        check_endpoint(f"{base_url}/readyz", 200)

        if service == "app":
            check_endpoint(f"{base_url}/openapi.json", 200)
            print(f"--> Testing POST {base_url}/items")
            payload = json.dumps({"name": "smoke-test-item"}).encode("utf-8")
            req = urllib.request.Request(
                f"{base_url}/items",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status != 201:
                    raise RuntimeError(f"Expected 201 from POST /items, got {resp.status}")
                print("    [OK] POST /items passed (201)")

            print(f"--> Testing validation rejection POST {base_url}/items with empty name")
            bad_payload = json.dumps({"name": ""}).encode("utf-8")
            bad_req = urllib.request.Request(
                f"{base_url}/items",
                data=bad_payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                urllib.request.urlopen(bad_req, timeout=3)
                raise RuntimeError("Expected 422 validation failure, but got success")
            except urllib.error.HTTPError as err:
                if err.code != 422:
                    raise RuntimeError(f"Expected 422, got {err.code}")
                print("    [OK] Validation rejection passed (422)")

        elif service == "svc_sidecar":
            status_data = check_endpoint(f"{base_url}/status", 200)
            if not status_data or status_data.get("service") != "svc_sidecar":
                raise RuntimeError("Invalid status payload from svc_sidecar")
            print("    [OK] /status passed")

        print(f"[OK] Container verification succeeded for service '{service}'")

    except (RuntimeError, urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"\nERROR: Smoke test failed: {exc}", file=sys.stderr)
        print("\n--- Container Logs ---", file=sys.stderr)
        subprocess.run(["docker", "logs", container_name], check=False)
        print("----------------------", file=sys.stderr)
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        sys.exit(1)

    finally:
        subprocess.run(
            ["docker", "stop", container_name],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Container smoke tester")
    parser.add_argument("--service", default="app", help="Service name (default: app)")
    parser.add_argument("--port", type=int, default=8000, help="Port to test (default: 8000)")
    parser.add_argument("--container", required=True, help="Docker container name")
    args = parser.parse_args()

    run_smoke_test(service=args.service, port=args.port, container_name=args.container)
