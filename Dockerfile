FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=65532:65532 paridad paridad
COPY --chown=65532:65532 migrations migrations
COPY --chown=65532:65532 templates templates
COPY --chown=65532:65532 static static
USER 65532:65532
CMD ["uvicorn", "paridad.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers", "--no-access-log", "--limit-concurrency", "80"]
