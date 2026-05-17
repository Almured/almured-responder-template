# syntax=docker/dockerfile:1
FROM python:3.11-slim

# Hardening: non-root user owns /app and runs the process.
RUN groupadd -r responder && useradd -r -g responder -u 1000 responder

WORKDIR /app

# Install package metadata first so dependency layers cache independently
# of source edits.
COPY pyproject.toml ./
COPY README.md ./
COPY LICENSE ./

# Create the minimal source layout the package install needs, then
# upgrade pip (default-shipped pip on python:3.11-slim is too old for
# editable installs against pyproject-only projects).
COPY src/ ./src/
RUN pip install --no-cache-dir --upgrade "pip>=23.0" && \
    pip install --no-cache-dir -e . && \
    rm -rf /root/.cache/pip

# Production assumes data/briefs.sqlite is mounted as a read-only volume.
# Partners override this with their own dataset — see DEPLOYMENT.md.
RUN mkdir -p /app/data && chown -R responder:responder /app

USER responder:responder

# Recommended runtime hardening (documented in DEPLOYMENT.md):
#   - --network none + a unix-socket reverse proxy that only allows
#     outbound HTTPS to api.almured.com.
#   - Read-only root filesystem + tmpfs for /tmp.
#   - Resource limits: 256 MiB memory, 0.5 CPU.
# Default-deny egress prevents a compromised agent from exfiltrating
# the partner's proprietary briefs corpus or the asker's question text.

ENTRYPOINT ["python", "-m", "responder.main"]
