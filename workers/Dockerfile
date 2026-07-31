FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    wget \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# Install shared packages (context is repo root)
COPY packages/shared-types /packages/shared-types
COPY packages/llm-prompts /packages/llm-prompts
RUN pip install --upgrade pip \
    && pip install -e /packages/shared-types \
    && pip install -e /packages/llm-prompts

COPY workers/requirements.txt .
RUN pip install -r requirements.txt

# Install Playwright browsers
RUN playwright install chromium --with-deps

# Download spaCy model
RUN python -m spacy download en_core_web_sm

COPY workers /app

CMD ["celery", "-A", "workers.celery_app", "worker", "--loglevel=info"]
