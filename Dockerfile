# BYTE_BATALLION_CTF_PLATFORM — production image (brief §13).

# Updated base keeps pip + security patches current.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first so this layer caches across code changes.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app

# Never ship secrets or local data inside the image.
RUN rm -f .env && \
    mkdir -p /app/data/challenges /app/data/instances && \
    useradd --create-home --shell /bin/sh appuser && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

# run.py validates the configuration (must not be the dev SECRET_KEY in
# prod: it refuses to boot otherwise) and starts uvicorn.
CMD ["python", "run.py"]
