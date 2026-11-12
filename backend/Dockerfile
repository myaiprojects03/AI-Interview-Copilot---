# Production image for the API. One process per container; run more containers to scale.
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

FROM base AS deps
COPY requirements.txt .
RUN pip install --prefix=/install -r requirements.txt

FROM base
COPY --from=deps /install /usr/local
RUN useradd --create-home --uid 10001 app

# The embedding model is baked into the image, so a new instance is ready at once
# instead of downloading ~70 MB on its first request.
ENV FASTEMBED_CACHE_PATH=/opt/models
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')" \
    && chown -R app:app /opt/models

COPY app ./app
COPY scripts ./scripts
USER app

# Behind the load balancer: trust its X-Forwarded-* headers (real client IPs for rate limits).
# The backend port is never published directly in docker-compose.prod.yml.
ENV FORWARDED_ALLOW_IPS=*
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen(f\"http://127.0.0.1:{os.environ.get('PORT', '8000')}/health\", timeout=2)"
# On shutdown (redeploy), open interviews get 20 s to finish writing history; browsers then
# reconnect to another instance and continue the same interview.
# PORT is set by some hosts (Render); 8000 otherwise.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --ws-max-size 1048576 --timeout-graceful-shutdown 20 --no-server-header"]
