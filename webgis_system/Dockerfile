FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libgdal-dev libgeos-dev libproj-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./requirements.txt
RUN python -m pip install --upgrade pip setuptools wheel \
    && pip install -r requirements.txt

COPY . /app

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data/chroma \
    && chown -R appuser:appuser /app

USER appuser
EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=5 \
  CMD curl --fail http://127.0.0.1:8001/health/live || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "2"]
