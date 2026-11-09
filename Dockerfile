# Hardened runtime image for Python microservices (supporting app* and svc* services).
#
# Base: Chainguard's free, minimal Python image (Wolfi-based, no shell, no
# package manager, runs as non-root UID 65532 "nonroot").
# The free tier only publishes floating tags (latest / latest-dev), so the
# base is pinned by multi-arch index digest for reproducible builds. Bump the
# digest deliberately (or via Renovate/Dependabot) to pick up CVE fixes:
#   docker buildx imagetools inspect cgr.dev/chainguard/python:latest
#
# The runtime uses the standard library only, so no build stage / pip install
# is needed; requirements.txt is intentionally empty.
FROM cgr.dev/chainguard/python:latest@sha256:1a00964779bf570a8cb2ea4fa086614af19c35069029785c08fcec0c50b66ac0

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOST=0.0.0.0 \
    PORT=8000

# Target service directory: defaults to app, supports app* and svc* directories
ARG SERVICE_DIR=app

# Only the service code from ${SERVICE_DIR}/ ships in the image (tests excluded via .dockerignore).
# COPY (not WORKDIR) creates /opt/app so the directory and files stay root-owned:
# the runtime user can read but not modify, add, or delete its own code.
# (The base image's /app is pre-owned by nonroot, hence /opt/app.)
COPY ${SERVICE_DIR}/ /opt/app/
WORKDIR /opt/app

# Never run as root (see AGENTS.md rule 6). Numeric so Kubernetes runAsNonRoot can verify it.
USER 65532:65532

EXPOSE 8000

# No shell in the image: exec-form healthcheck using Python standard library.
HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import os,sys,urllib.request; sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/healthz', timeout=2).status == 200 else 1)"]

# Exec form: python is PID 1 and receives SIGTERM/SIGINT directly for graceful drain.
ENTRYPOINT ["python", "/opt/app/main.py"]
