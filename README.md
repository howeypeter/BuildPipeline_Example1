# Enterprise Delivery Pipeline & Microservices Architecture

A production-minded microservice delivery pipeline demonstrating a clean, repeatable path from source code to runnable, secure container artifacts across multiple application domains.

---

## 1. Architectural Overview

The repository is organized as a lightweight microservice workspace where any directory matching `app*` or `svc*` is treated as an independent deployable service (such as `app/` and `svc_sidecar/`). Each service is designed with **zero external runtime dependencies**, leveraging the Python standard library (`http.server`, `json`, `logging`, `signal`, `threading`) to prioritize:
- **Instant startup & tiny footprint**: No dependency bloat, container build times under 2 seconds.
- **Security**: Minimal attack surface with zero third-party package vulnerabilities in production images.
- **Deep platform fundamentals**: Direct handling of POSIX signals, HTTP socket draining, structured JSON logging, and cloud-native health probes.
- **Multi-service flexibility**: Automatic discovery, testing, and container packaging for both primary applications and auxiliary sidecars.

### Project Layout
```text
.
├── .github/
│   └── workflows/
│       └── ci.yml             # Matrix CI pipeline (lint, test, build per service)
├── app/
│   ├── __init__.py
│   ├── main.py                # Primary HTTP API service implementation
│   └── test_main.py           # Integration and lifecycle tests
├── svc_sidecar/
│   ├── __init__.py
│   ├── main.py                # Telemetry sidecar microservice
│   └── test_main.py           # Sidecar lifecycle tests
├── scripts/
│   └── smoke_test.py          # Cross-platform container verification script
├── .dockerignore
├── .gitignore
├── Dockerfile                 # Parameterized hardened Chainguard image (SERVICE_DIR)
├── Makefile                   # Developer shortcuts (supports SERVICE=app|svc_sidecar)
├── openapi.yaml               # OpenAPI 3.1.0 specification contract
├── requirements.txt           # Production dependencies (zero external packages)
├── requirements-dev.txt       # Development and CI tooling (ruff, pytest)
├── AGENTS.md                  # Project rules and agent pairing guidelines
├── walkthrough.html           # Visual architecture guide and interactive diagrams
└── README.md                  # This runbook
```

---

## 2. API Specifications

### Primary Service (`app/`)
| Method | Path | Status Codes | Description |
| :--- | :--- | :--- | :--- |
| `GET` | `/healthz` | `200 OK` | **Liveness Probe**: Confirms process is running and responding. |
| `GET` | `/readyz` | `200 OK`, `503 Service Unavailable` | **Readiness Probe**: Returns `200` while ready for traffic; immediately flips to `503` when `SIGTERM` is received to drop out of load balancers. |
| `GET` | `/items` | `200 OK` | Lists all created items in memory. |
| `POST` | `/items` | `201 Created`, `422 Unprocessable` | Creates a new item. Validates that `name` is a non-empty string under 100 characters. |
| `GET` | `/openapi.json` | `200 OK` | **OpenAPI Contract**: Returns OpenAPI 3.1.0 schema for clients, gateways, and documentation. |

### Sidecar Service (`svc_sidecar/`)
| Method | Path | Status Codes | Description |
| :--- | :--- | :--- | :--- |
| `GET` | `/healthz` | `200 OK` | **Liveness Probe**: Confirms sidecar process is responsive. |
| `GET` | `/readyz` | `200 OK`, `503 Service Unavailable` | **Readiness Probe**: Flips to `503` during sidecar connection draining. |
| `GET` | `/status` | `200 OK` | **Telemetry Probe**: Returns sidecar uptime and operational role. |

---

## 3. Local Development

All local development and tests execute through the Python 3.12 virtual environment at `app/.venv`.

### Setup Environment
```bash
# Set up virtual environment and install dev tooling (ruff, pytest)
make venv

# Activate the virtual environment
source app/.venv/bin/activate       # Linux/macOS
app\.venv\Scripts\activate          # Windows
```

### Formatting, Linting, and Testing
```bash
# Auto-format all services using ruff
make fmt

# Run mandatory lint checks across all services (app* and svc*)
make lint

# Run AI advisory review (non-blocking quality checks)
make lint-ai

# Run test suite across all discovered services
make test SERVICE=all

# Run test suite for a specific microservice
make test SERVICE=app
make test SERVICE=svc_sidecar

# Run all checks locally before pushing
make validate
```

---

## 4. Deterministic & Secure Packaging

The root `Dockerfile` is parameterized via `--build-arg SERVICE_DIR=<service>` to package any `app*` or `svc*` directory with zero boilerplate:

1. **Approved Golden Base Image**: Built exclusively on Chainguard's minimal Python image (`cgr.dev/chainguard/python`), eliminating package managers (`apk`), shells (`sh`, `bash`), and system compilers.
2. **Cryptographic Base Pinning**: Pinned by multi-architecture image digest:
   `cgr.dev/chainguard/python:latest@sha256:1a00964779bf570a8cb2ea4fa086614af19c35069029785c08fcec0c50b66ac0`
3. **Numeric Non-Root User**: Runs as `USER 65532:65532` (`nonroot`), satisfying Kubernetes `runAsNonRoot: true` policies without name lookup dependencies.
4. **Read-Only Root Filesystem Ready**: Code is owned by `root:root` with read-only permissions for the runtime user. The container runs with `--read-only` and dropped capabilities (`--cap-drop=ALL`).
5. **PID 1 Signal Forwarding**: Exec form `ENTRYPOINT ["python", "/opt/app/main.py"]` guarantees Python receives OS signals directly without shell signal swallowing.
6. **Shell-less Healthcheck**: Exec form `HEALTHCHECK` using Python standard library `urllib.request`.

### Build and Run Containers Locally
```bash
# Build container image for primary service
make docker-build SERVICE=app

# Build container image for sidecar service
make docker-build SERVICE=svc_sidecar

# Build all discovered services
make docker-build-all

# Run container and execute automated smoke test against probes
make docker-run SERVICE=app
make docker-run SERVICE=svc_sidecar PORT=8001
```

---

## 5. Continuous Integration (CI/CD)

The GitHub Actions workflow (`.github/workflows/ci.yml`) executes on every `push` and `pull_request` to the `main` branch:
1. **Dynamic Service Discovery**: Detects all microservice directories matching `app*` or `svc*` containing `main.py` and feeds them into a parallel GitHub Actions matrix.
2. **Linting & Code Quality**: Validates syntax and styling using `ruff` inside `app/.venv`.
3. **Automated Testing**: Executes full unit and integration tests across each service.
4. **Golden Base Image Verification**: Verifies base images originate from approved catalogs and are pinned by digest.
5. **Container Image Build**: Packages each service using the hardened Chainguard base.
6. **Strictly Immutable Tagging**: Images are tagged exclusively with the Git commit SHA (such as `:sha-a1b2c3d`). Mutable `:latest` tags are avoided to prevent configuration drift and guarantee deterministic rollbacks.
7. **Software Bill of Materials (SBOM)**: Automatically generates a machine-readable SPDX SBOM (`anchore/sbom-action`) for every image build and uploads it as a verifiable CI artifact.
8. **Runtime Verification**: Spun up in CI with `--read-only` and `--cap-drop=ALL` to validate healthchecks before publishing.
9. **Registry Agnostic Publishing**: Publishes immutable container artifacts to the configured container registry (such as AWS ECR, GHCR, or Artifactory) upon push to `main`.
10. **Pipeline Step Summaries**: Outputs a formatted markdown summary directly to `$GITHUB_STEP_SUMMARY` detailing test counts, image digest, and security checks.

---

## 6. Operational Runbook

### Health Probe Architecture
* **Kubernetes Liveness (`/healthz`)**: Detects deadlocks or hung event loops. If this fails, the orchestrator restarts the container replica.
* **Kubernetes Readiness (`/readyz`)**: Decides if traffic should route to this replica. When `SIGTERM` is received during a deployment:
  1. The service marks `is_ready = False`.
  2. The next probe to `/readyz` returns `503 Service Unavailable`.
  3. External load balancers (such as AWS ALB) and internal EndpointSlices drop the pod from active routing.
  4. The service drains existing in-flight connections before closing listener sockets.
  5. Zero dropped requests during rolling upgrades.

### Structured JSON Logging
Every log entry is emitted to `stdout` as a structured JSON object, enabling ingestion into cloud logging engines (AWS CloudWatch, Datadog, Elasticsearch, Loki):
```json
{
  "timestamp": "2026-11-08T05:31:55.123456+00:00",
  "level": "INFO",
  "logger": "service",
  "message": "handled request",
  "method": "POST",
  "path": "/items",
  "status": 201,
  "duration_ms": 1.45,
  "client_ip": "127.0.0.1",
  "request_id": "a17f2231-155e-42ab-9cf0-df8efbc2a2ee"
}
```

### Promotion & Deployment Strategy
1. **Build Once, Deploy Anywhere**: CI builds an immutable image tagged with the commit SHA (`<service>:sha-<git-commit>`). The exact same image artifact is promoted across environments (Development -> Staging -> Production).
2. **Configuration by Environment**: Runtime configuration (`PORT`, `LOG_LEVEL`) is injected purely via environment variables or secret stores (Kubernetes ConfigMaps/Secrets, AWS SSM/Secrets Manager).

### Rollback Strategy
* **Zero Rebuilds on Rollback**: If a newly promoted image exhibits defects, never trigger a rebuild. Rollback is executed by updating the Kubernetes deployment manifest (Helm release, ArgoCD Application, Flux Kustomization) to point to the previously known good immutable SHA tag (`<service>:sha-<previous-commit>`).
* **Instant Reversion**: Because container images are immutable and cached in the registry, rolling back to the previous tag takes seconds.

---

## 7. Production Considerations & Gaps

While this service intentionally remains lightweight for the assessment scope, moving to enterprise production requires addressing the following areas:

### 1. Persistence & Concurrency
* **Current State**: Thread-safe in-memory store. If the container restarts or crashes, data is reset.
* **Production Path**: Replace in-memory store with an external datastore (such as AWS Aurora PostgreSQL or DynamoDB) using connection pooling (`psycopg`, `asyncpg`, or `boto3`) and schema migration tooling (`alembic`).

### 2. ASGI / Production Web Server
* **Current State**: Python built-in `http.server.ThreadingHTTPServer`.
* **Production Path**: While suitable for internal sidecars and low-footprint services, high-concurrency external traffic benefits from an asynchronous ASGI server (such as `uvicorn` or `granian` with `FastAPI` or `Starlette`) or running behind an Envoy reverse proxy to handle slow-loris attacks, TLS termination, and HTTP/2.

### 3. Observability & Telemetry
* **Current State**: JSON structured access logs and request timing on `stdout`.
* **Production Path**:
  - Expose a `/metrics` endpoint with Prometheus counters and histograms (request duration percentiles: p50, p95, p99).
  - OpenTelemetry distributed tracing headers (`traceparent`) propagated through request lifecycles.
  - Alerting on error budget burn rate (such as 5xx status codes > 1% over a 5 minute window).

### 4. Security & Hardening
* **Current State**: Shell-less Chainguard base image pinned by digest, running as unprivileged UID 65532.
* **Production Path**:
  - Run container with a read-only root filesystem (`--read-only`) with a temporary volume (`tmpfs`) for `/tmp`.
  - Drop all default Linux capabilities (`cap_drop: ["ALL"]`).
  - Scan dependencies and container image in CI using Snyk, Trivy, or Grype for CVEs.
  - Mutual TLS (mTLS) via service mesh (Istio, Linkerd) for intra-cluster communication.

### 5. The True Production Artifact: Beyond the Container Image
* **Scope Realism**: Within a one hour assessment, the runnable container image is the appropriate scope. In true enterprise platform delivery, a container image alone is only half the artifact.
* **Upstream Chart Inheritance**: Helm charts are built and maintained by an upstream platform process. Product developers do not author raw deployment manifests from scratch.
* **90/10 Configuration Philosophy**: Developers inherit the upstream chart where 90%+ hardened defaults are pre-configured. Developers do not need to specify "Use port 8000", probes, security contexts (UID 65532), or CPU autoscaling rules because they are baked into the inherited upstream chart by default. The developer local values file contains only the remaining 10% application-specific configuration.
* **Autoscaling Mechanics**: Horizontal Pod Autoscaling (HPA) must be driven by CPU utilization, request throughput, or external event queues (such as AWS SQS or Kafka consumer lag via KEDA). Autoscaling must never scale on memory utilization, because language runtimes like Python retain allocated heap buffers, which leads to scaling oscillation and prevents downscaling.
* **Platform Perimeter**: Automated cert-manager TLS certificates, service mesh mutual TLS policies, and cross-AZ ingress controllers wrap the container artifact before it receives live customer traffic.
