FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV POETRY_NO_INTERACTION=1
ENV POETRY_VIRTUALENVS_CREATE=0
# Dependencies are installed with --no-root, so the app package is imported from
# /app. Required by alembic/env.py and scripts/run_rq_worker.py, neither of which
# gets /app on sys.path on its own.
ENV PYTHONPATH=/app
WORKDIR /app

# Install system dependencies
# git is required to resolve the jarvis-* git+https direct references
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        git \
        libxml2-dev \
        libxslt1-dev \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir "poetry==2.2.1"

# Install dependencies from the lock file before copying source, so edits to
# application code do not invalidate the dependency layer
COPY pyproject.toml poetry.lock README.md /app/
RUN poetry install --without dev --no-root \
    && pip cache purge \
    && find /usr/local/lib/python3.11 -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true \
    && find /usr/local/lib/python3.11 -type f -name "*.pyc" -delete 2>/dev/null || true

# Copy application files
COPY jarvis_recipes /app/jarvis_recipes
COPY alembic /app/alembic
COPY alembic.ini /app/
COPY scripts /app/scripts
COPY static_data /app/static_data

CMD ["bash", "-c", "alembic upgrade head && uvicorn jarvis_recipes.app.main:app --host 0.0.0.0 --port 7030"]
