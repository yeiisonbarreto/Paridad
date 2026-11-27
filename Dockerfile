FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=65532:65532 . .
RUN for logo in 'Logotipo Paridad_'*.png; do \
        if [ -f "$logo" ] && [ ! -e static/paridad-logo.png ]; then \
            mv "$logo" static/paridad-logo.png || exit 1; \
        fi; \
    done
USER 65532:65532
CMD ["uvicorn", "paridad.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers", "--no-access-log", "--limit-concurrency", "80"]
