FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libxml2-dev \
        libxslt1-dev \
        libffi-dev \
        tesseract-ocr \
        libtesseract-dev \
        libgl1 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir poetry

COPY pyproject.toml poetry.lock* README.md /app/
COPY jarvis_recipes /app/jarvis_recipes
RUN poetry config virtualenvs.create false \
    && poetry install --no-interaction --no-ansi --only main

ENV EASY_OCR_MODEL_PATH=/root/.EasyOCR
# Pre-download EasyOCR English models into the cache directory to avoid runtime downloads/OOM.
COPY . /app

CMD ["bash", "-c", "alembic upgrade head && uvicorn jarvis_recipes.app.main:app --host 0.0.0.0 --port 8001"]

