FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    BACKEND_URL=http://backend:8000 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

ARG DATACOPILOT_UID=1014
ARG DATACOPILOT_GID=1015

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "${DATACOPILOT_GID}" datacopilot \
    && useradd --uid "${DATACOPILOT_UID}" --gid "${DATACOPILOT_GID}" \
        --create-home --shell /usr/sbin/nologin datacopilot

COPY frontend/requirements.txt /app/frontend/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /app/frontend/requirements.txt

COPY frontend /app/frontend
RUN chown -R datacopilot:datacopilot /app

USER datacopilot

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8501/_stcore/health || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["streamlit", "run", "frontend/app.py", "--server.address=0.0.0.0", "--server.port=8501"]
