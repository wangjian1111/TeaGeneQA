FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt && useradd --create-home --uid 10001 app
COPY teageneqa ./teageneqa
COPY ui ./ui
COPY migrations ./migrations
COPY scripts ./scripts
COPY config ./config
COPY alembic.ini ./
RUN mkdir -p /app/reports/v1_runtime && chown -R app:app /app
USER app
EXPOSE 8000 8501
CMD ["python", "-m", "uvicorn", "teageneqa.api:app", "--host", "0.0.0.0", "--port", "8000"]
