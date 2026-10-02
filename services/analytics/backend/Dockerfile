FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

ARG DATACOPILOT_UID=1014
ARG DATACOPILOT_GID=1015
ARG PYTORCH_VERSION=2.8.0

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "${DATACOPILOT_GID}" datacopilot \
    && useradd --uid "${DATACOPILOT_UID}" --gid "${DATACOPILOT_GID}" \
        --create-home --shell /usr/sbin/nologin datacopilot

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir "torch==${PYTORCH_VERSION}" \
        --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r /app/backend/requirements.txt

COPY backend /app/backend
COPY contracts /app/contracts

RUN mkdir -p /app/data/chromadb /app/data/uploads /app/data/logs /app/data/cache \
    /app/data/embeddings /app/data/temp /app/models \
    && chown -R datacopilot:datacopilot /app

USER datacopilot

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health/live || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "backend.app.main:app", "--host=0.0.0.0", "--port=8000"]
