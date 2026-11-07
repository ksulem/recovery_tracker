FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Pinned dependencies first, so code changes don't invalidate this layer.
COPY requirements.lock .
RUN pip install -r requirements.lock

COPY pyproject.toml README.md alembic.ini ./
COPY alembic ./alembic
COPY src ./src
RUN pip install --no-deps . \
    && useradd --create-home --uid 10001 app

USER app

CMD ["python", "-m", "recovery"]
