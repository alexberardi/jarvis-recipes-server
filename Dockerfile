FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /app

# Install system dependencies and poetry in one layer
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libxml2-dev \
        libxslt1-dev \
        libffi-dev \
        tesseract-ocr \
        libtesseract-dev \
        libgl1 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir poetry==1.7.1

# Copy only dependency files first for better caching
COPY pyproject.toml poetry.lock* README.md /app/

# Install dependencies without dev packages, with aggressive cleanup
RUN poetry config virtualenvs.create false \
    && poetry install --no-interaction --no-ansi --only main --no-root \
    && pip cache purge \
    && rm -rf /root/.cache/pypoetry \
    && find /usr/local/lib/python3.11 -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true \
    && find /usr/local/lib/python3.11 -type f -name "*.pyc" -delete 2>/dev/null || true

# Copy application code
COPY jarvis_recipes /app/jarvis_recipes
COPY alembic /app/alembic
COPY alembic.ini /app/
COPY scripts /app/scripts
COPY static_data /app/static_data

# Install the package itself (no dependencies, already installed)
RUN poetry install --no-interaction --no-ansi --only-root

ENV EASY_OCR_MODEL_PATH=/root/.EasyOCR

CMD ["bash", "-c", "alembic upgrade head && uvicorn jarvis_recipes.app.main:app --host 0.0.0.0 --port 8001"]

