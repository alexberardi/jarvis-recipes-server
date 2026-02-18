FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /app

# Install system dependencies
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libxml2-dev \
        libxslt1-dev \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy all files needed for installation
COPY pyproject.toml README.md /app/
COPY jarvis_recipes /app/jarvis_recipes

# Install build backend first (required for poetry-core build system)
RUN pip install --no-cache-dir "poetry-core>=2.0.0,<3.0.0"

# Install the package and all dependencies using pip (PEP 621 compatible)
RUN pip install --no-cache-dir . \
    && pip cache purge \
    && find /usr/local/lib/python3.11 -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true \
    && find /usr/local/lib/python3.11 -type f -name "*.pyc" -delete 2>/dev/null || true

# Copy remaining application files
COPY alembic /app/alembic
COPY alembic.ini /app/
COPY scripts /app/scripts
COPY static_data /app/static_data

# Verify critical commands are available (debug step)
RUN echo "=== Checking installed commands ===" \
    && which python && python --version \
    && which pip && pip --version \
    && echo "=== Checking for alembic ===" \
    && which alembic || echo "alembic not in PATH" \
    && echo "=== Checking for uvicorn ===" \
    && which uvicorn || echo "uvicorn not in PATH" \
    && echo "=== Listing installed packages ===" \
    && pip list | grep -E "alembic|uvicorn|fastapi" || echo "packages not found" \
    && echo "=== Verification complete ==="

CMD ["bash", "-c", "alembic upgrade head && uvicorn jarvis_recipes.app.main:app --host 0.0.0.0 --port 7030"]

