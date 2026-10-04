FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATABASE_AUTO_CREATE_SCHEMA=false

WORKDIR /app
COPY pyproject.toml README.md alembic.ini ./
COPY app ./app
COPY migrations ./migrations
RUN pip install --no-cache-dir ".[rag]"

EXPOSE 8000
CMD ["sh", "-c", "python -c 'from app.config import Settings; Settings.from_env().validate_security()' && alembic -x legacy_demo_backfill=${LEGACY_DEMO_BACKFILL:-false} upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
