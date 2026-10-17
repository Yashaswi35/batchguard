# BatchGuard API. Runs on the frozen gold snapshot, so no raw data is needed in the image.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BATCHGUARD_LAKE=/app/evals/fixtures \
    BATCHGUARD_DATA=/data \
    BATCHGUARD_LLM=rule

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent ./agent
COPY api ./api
COPY pipeline ./pipeline
COPY config ./config
COPY regs ./regs
COPY evals ./evals

# run as a non-root user, audit database on a writable volume
RUN useradd --create-home app && mkdir -p /data && chown app /data
USER app
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
