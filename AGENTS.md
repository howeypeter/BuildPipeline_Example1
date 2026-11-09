# Project Guidelines & Agent Context (AGENTS.md)

## Overview & Objective
This repository contains a demonstration project for a production-grade build pipeline and microservice architecture.
The objective is not to build an elaborate monolithic web application, but to demonstrate a clean, repeatable, and secure engineering path from source code to runnable, production-ready microservice artifacts.

### Core Evaluation Pillars
1. **Application Fundamentals**: Clean API design, input validation, structured JSON logging, separation of liveness and readiness probes, and graceful shutdown with socket draining.
2. **Multi-Service Architecture**: Flexible microservice support allowing independent discovery and builds for any `app*` and `svc*` directory (such as `app/` and `svc_sidecar/`).
3. **Deterministic & Secure Packaging**: Minimal hardened container images (Chainguard `cgr.dev/chainguard/python`, pinned by digest), non-root user execution, layer caching, and proper PID 1 signal forwarding (`exec` form).
4. **Automated CI/CD Pipeline & Supply Chain**: Continuous integration matrix workflow running linting, automated unit/integration tests, Software Bill of Materials (SBOM) generation, container smoke checks, and immutable image publishing across container registries (AWS ECR, GHCR, Artifactory).
5. **Operations & Runbook**: Clear guidance for another engineer to run, test, promote, roll back, and operate services in production.
6. **Architectural Reasoning**: Realistic identification of production gaps (persistence, metrics/alerting, security posture, horizontal scalability).

---

## Technical Stack & Constraints
- **Language**: Python 3.12+ (standard library only for runtime: zero external production dependencies).
- **Service Directories**: Any directory matching `app*` or `svc*` (such as `app/`, `svc_sidecar/`) containing `main.py` is automatically recognized as an independent deployable service.
- **Entrypoints**: `<service>/main.py` (for example `app/main.py` or `svc_sidecar/main.py`).
- **Tests**: `<service>/test_main.py` executed via `python -m unittest discover -s <service> -p "test_*.py"`.
- **Virtual Environment**: All local and CI work runs inside `app/.venv` (Python 3.12, matching the container base image), populated from `requirements-dev.txt`. It is git-ignored and docker-ignored; never commit it or copy it into the image.
- **Packaging**: Containerized via root `Dockerfile` using `--build-arg SERVICE_DIR=<service>` or service-level `<service>/Dockerfile`, running as Chainguard built-in non-root user `nonroot` (UID 65532). The base image has no shell, so any `RUN`/`HEALTHCHECK` uses exec form with `python`.
- **CI Automation**: GitHub Actions workflow at `.github/workflows/ci.yml` featuring automatic service discovery and parallel matrix builds.

---

## Agent Working Rules
1. **Always Discuss Before Major Changes**: Never make sweeping architectural changes or introduce heavy frameworks without explicit user consent.
2. **Preserve Simplicity**: Avoid adding unnecessary third-party runtime packages unless strictly justified. Standard library reliability and speed are prioritized.
3. **Signal & Drain Integrity**: Any changes to HTTP servers must preserve graceful shutdown (`SIGTERM`/`SIGINT`), flipping `/readyz` to 503 before closing listeners to avoid dropping requests during rolling deployments.
4. **Immutability in CI/CD**: Ensure build artifacts are tagged with git commit hashes (`sha-<commit>`) rather than relying solely on mutable `:latest` tags.
5. **Always Use `app/.venv`**: Run Python, tests, and tooling through `app/.venv`, never the system interpreter or a global `pip install`. If it is missing, create it before doing anything else. CI must build the same venv the same way so local and CI environments match.
6. **Containers Never Run as Root**: The image must always run as the unprivileged numeric user `65532:65532` (Chainguard `nonroot`).
   - Keep an explicit numeric `USER 65532:65532` in the `Dockerfile` (numeric so Kubernetes `runAsNonRoot` can verify it); never set `USER root`/`USER 0`, even temporarily in the final stage.
   - Never add `--user 0`/`--user root`, `--privileged`, or added capabilities to `docker run`, Compose, or CI commands.
   - Deployment manifests must enforce it: `runAsNonRoot: true`, `runAsUser: 65532`, `runAsGroup: 65532`, `allowPrivilegeEscalation: false`, `capabilities.drop: ["ALL"]`, `readOnlyRootFilesystem: true`.
   - Application code is owned by root and read-only to the runtime user (do not `COPY --chown` it to `65532`); the service must not need to write to its own filesystem.
   - Verify after Dockerfile changes: `docker image inspect <image> -f '{{.Config.User}}'` must print `65532:65532`.
7. **Supply Chain Integrity & SBOM Generation**: The CI build pipeline must automatically generate a Software Bill of Materials (SBOM) (SPDX/CycloneDX format) alongside each container image, ensuring full transparency of all OS components and cryptographic traceability to the Git commit.
8. **Deterministic Enforcement vs. AI Advisory**: Deterministic linters (`ruff`) are hard, blocking gates (`exit code 1`). AI code suggestions (`make lint-ai`) provide advisory recommendations for quality, consistency, and readability, but must remain non-blocking (`exit code 0`) to preserve pipeline determinism.
9. **Scope Boundary & The True Production Artifact (1-Hour Assessment)**: The live code focuses on MVP runnable container artifacts within a one hour window. However, agents and engineers must articulate the full production reality. A true deployment artifact encompasses both the container image and its accompanying deployment package (such as a Helm chart built by an upstream platform process). That upstream package provides 90%+ hardened defaults out of the box so developers write zero boilerplate (they do not declare port 8000, probe timings, or security contexts), leaving at most 10% environment configuration. Autoscaling must be driven by CPU utilization, request throughput, or queue depth (such as SQS), and never by memory due to language runtime memory retention patterns. Cert-manager TLS, service mesh topology, and multi-AZ ingress form the production perimeter around the container.

---

## Virtual Environment Setup
Run from the repository root:

```bash
# Create the venv (requires Python 3.12 on PATH)
python3.12 -m venv app/.venv
# or, with uv (downloads 3.12 if needed):
uv venv app/.venv --python 3.12 --seed

# Install dev/CI tooling (runtime deps come in via -r requirements.txt)
app/.venv/bin/python -m pip install -r requirements-dev.txt      # Linux/macOS
app/.venv/Scripts/python.exe -m pip install -r requirements-dev.txt  # Windows

# Lint and test across all services (app* and svc*)
app/.venv/bin/python -m ruff check app svc_sidecar
app/.venv/bin/python -m unittest discover -s app -p "test_*.py"
app/.venv/bin/python -m unittest discover -s svc_sidecar -p "test_*.py"
```

Invoke the venv interpreter by path (as above) or activate it first (`source app/.venv/bin/activate`, or `app\.venv\Scripts\activate` on Windows). If `requirements*.txt` changes, re-run the install step; if the venv breaks, delete `app/.venv` and recreate it.
