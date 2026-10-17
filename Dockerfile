# Place Assistant web UI for Cloud Run. Deploy steps: README.md, "Deploy to Cloud Run".
# Debian 12 (bookworm): a base Playwright supports for Chromium's system libraries.
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# Packages at the versions tested locally, then headless Chromium and the
# system libraries it needs, for websites built with JavaScript.
COPY requirements.txt constraints.txt ./
RUN pip install -r requirements.txt -c constraints.txt \
 && playwright install --with-deps chromium

COPY . .

# Run unprivileged. Reports go to /app/.output, inside the instance, so they
# last only as long as the instance does.
RUN useradd --create-home app && mkdir -p .output && chown -R app /app
USER app

# Cloud Run sets PORT. Settings come from environment variables (README.md).
CMD exec uvicorn place_assistant.ui.server:app --host 0.0.0.0 --port ${PORT:-8080}
