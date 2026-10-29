# HealTest worker / sandbox image. Built from the REPO ROOT so the backend
# package is available to RQ jobs:
#   docker build -f worker/Dockerfile -t healtest-worker .
FROM python:3.12-slim

# Headless Chrome for Selenium. The apt chromedriver is used directly
# (CHROMEDRIVER_PATH) so sandboxed runs need no network for driver downloads.
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    chromium-driver \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY backend/requirements.txt ./backend-requirements.txt
COPY worker/requirements.txt ./worker-requirements.txt
RUN pip install --no-cache-dir -r backend-requirements.txt -r worker-requirements.txt

COPY backend/app ./app
COPY worker/conftest_plugin.py ./
COPY worker/runner.py ./

ENV PYTHONPATH=/srv \
    HEALTEST_WORKER_DIR=/srv \
    HEALTEST_DATA_DIR=/data \
    CHROMEDRIVER_PATH=/usr/bin/chromedriver

# Default: RQ worker. Overridden for fresh-container sandbox runs, which
# invoke pytest directly (see backend/app/runner.py).
CMD ["sh", "-c", "rq worker healtest --url \"${REDIS_URL:-redis://redis:6379/0}\" --path /srv"]
